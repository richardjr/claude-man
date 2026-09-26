# CLI reference (`claudemanctl`)

Everything the TUI does — and a few things it doesn't — as scriptable commands. This is the
**power-user reference**; the average-user path is the TUI ([`../README.md`](../README.md)
§ *Getting started*, [`TUI-GUIDE.md`](TUI-GUIDE.md)). Commands below are written as
`uv run claudemanctl …` (the checkout form); with a wheel install
(`uv tool install dist/*.whl`) the prefix is just `claudemanctl …`. The `claudeman` binary
accepts the same subcommands (`claudeman doctor`, `claudeman config show`, …) — with no
subcommand it launches the TUI.

## Doctor (host prerequisite checks)

```bash
uv run claudemanctl doctor
```

Checks the host and prints one line per prerequisite with an actionable fix hint: platform,
docker (binary on PATH, daemon reachable, socket permission — each failure gets its own hint,
including the `sudo usermod -aG docker $USER` line for a permission-denied socket), the host
`claude` CLI, the hardened base image, the terminal launcher, profiles/tokens, and the config
file. Exit code 0 when nothing blocking was found (warnings — e.g. "image not built yet" —
don't fail it). The TUI setup wizard runs the same checks interactively.

## Quick start

```bash
uv sync                       # install textual + tomlkit into .venv
uv run claudemanctl --help    # the CLI surface
uv run claudemanctl doctor    # check host prerequisites

# Build + smoke-test the hardened base image (needs docker + a claude install)
uv run claudemanctl image build base
uv run claudemanctl image smoke base

# Mint an account profile, then create + run a project under it
uv run claudemanctl profile add home --default     # completes `claude setup-token` (browser flow)
uv run claudemanctl project create demo            # write registry + seed config + create container
uv run claudemanctl project up demo                # start it
uv run claudemanctl project shell demo             # open a shell inside (or `project claude|nvim demo`)
uv run claudemanctl profile usage                  # per-account token usage
```

## Setting up accounts (profiles)

A **profile** is one Claude account identity, minted once on the host with `claude setup-token`
and injected per-launch as `CLAUDE_CODE_OAUTH_TOKEN`. claude-man never copies `.credentials.json`
into a container and never sets `ANTHROPIC_*` keys (see invariant 1 in
[`../CLAUDE.md`](../CLAUDE.md)). Need claude.ai **account connectors** in a project? The
setup-token can't reach them — see *Auth mode* under Managing projects below.

```bash
# A personal subscription account, made the default for new projects:
uv run claudemanctl profile add home --default

# A WORK account behind SSO — runs `claude auth login --sso` first to point the
# host session at the right seat, then mints the token for THAT account:
uv run claudemanctl profile add work --sso --email you@company.com

# Give it a friendlier label shown in the TUI / `profile list`:
uv run claudemanctl profile add work --sso --display-name "Work (ACME SSO)"

# Other login front-ends, used instead of --sso:
uv run claudemanctl profile add work --login     # plain `claude auth login`
uv run claudemanctl profile add api  --console   # Anthropic Console (API billing)

# Profiles are AGENT-scoped (Phase 7 — docs/AGENTS.md): `--agent` names the provider the account
# belongs to (default claude); a project can only run a profile of its own agent. How the token is
# minted follows the provider's auth kind — claude = the setup-token flow above; an api-key-kind
# provider (codex, from 7c) prompts for the key (hidden) or reads it from --stdin, never argv.
# `--login-only` records the profile WITHOUT a token: the identity for projects that use
# `--auth login`, where the credential is minted inside the container.
uv run claudemanctl profile add oa --agent claude --login-only        # no token; for login-mode projects
printenv OPENAI_API_KEY | uv run claudemanctl profile add oa --agent codex --stdin   # api-key kind (API-billed)

# A Codex project on a ChatGPT plan (login mode — docs/AGENTS.md § Codex):
uv run claudemanctl profile add codex-home --agent codex --login-only   # identity only; no key
uv run claudemanctl project create cx --agent codex --profile codex-home --auth login
#   builds base → <overlay> → a tools layer carrying the pinned codex package (docs/TOOLS.md)
uv run claudemanctl project shell cx                 # then, once, inside: codex login --device-auth
uv run claudemanctl project agent cx                 # (= `project claude`) opens codex in a terminal
uv run claudemanctl project run cx "summarise the repo"   # headless, same as a claude project
```

`--sso`, `--login`, and `--console` all run `claude auth login` **before** `claude setup-token`,
so the token is minted for the account you just signed into:

| Flag | Effect |
|---|---|
| `--sso` | `claude auth login --sso` — sign in via your org's SSO seat first |
| `--login` | plain `claude auth login` first |
| `--console` | `claude auth login --console` — Anthropic Console (API billing) |
| `--email <addr>` | passed to the login **and** recorded as the profile's account |
| `--default` | make this the default profile new projects inherit |
| `--display-name <s>` | human-readable label for the TUI and `profile list` |

With **no** login flag, `profile add` mints against whatever account your host `claude` is already
logged into. `--email` is optional; without it the account is read back from `claude auth status`.

Managing existing profiles:

```bash
uv run claudemanctl profile list            # all profiles, default flag, token age
uv run claudemanctl profile verify work     # which account the token authenticates as (--raw for JSON)
uv run claudemanctl profile renew work      # re-mint an expired token (≈1-year life, can't self-refresh)
uv run claudemanctl profile seed work       # (re)capture the host ~/.claude seed new projects inherit
uv run claudemanctl profile usage           # per-account token usage across all projects (from transcripts)
```

## Managing projects

```bash
# Create a project, choosing its account, image overlay, extra tools, pack language, and egress mode:
uv run claudemanctl project create demo --profile work --overlay python --language python --egress open
#   --profile <name>             account to run under   (default: the default profile)
#   --overlay base|python|rust|node|python-node|terraform   toolchain baked into the image (default: base)
#   --tool <name>                approved tool baked as a layer ON TOP of the overlay (repeatable;
#                                `claudemanctl tools list` — see Approved tools below)
#   --language <tier>            curated-pack tier whose defaults apply (see Curated packs below)
#   --agent   claude             the coding-agent provider run in the container (default claude — the
#                                only one registered today; codex lands with Phase 7c, docs/AGENTS.md).
#                                Fixed at create; the profile must belong to the same agent
#   --egress  open|strict        network policy         (default: open; strict = allowlist egress proxy)

uv run claudemanctl project up demo         # create-if-needed + start
uv run claudemanctl project status [demo]   # live state JOINed with the registry (all, or one slug);
                                            # the AGENT column is the project's provider (registry-sourced)
uv run claudemanctl project stop demo       # stop the container (project + workspace are kept)
uv run claudemanctl project shell demo      # open a shell in a new terminal
uv run claudemanctl project claude demo     # run claude in a new terminal
uv run claudemanctl project nvim demo       # open neovim (baked into the image) in a new terminal

# Run ONE headless (non-interactive) agent session in the container — the Phase 7-run seam that the
# manager tier will drive (docs/V2-PLAN.md). The prompt rides the agent's STDIN (never argv); the
# provider's JSON event stream is normalised: tool calls + notices go to stderr, the final message to
# stdout, a usage line + the session id to stderr. Starts the container first if needed; refused while
# the project's agent is already running in it (one agent per container).
uv run claudemanctl project run demo "summarise the repo layout"
uv run claudemanctl project run demo - < prompt.md                 # prompt from stdin
uv run claudemanctl project run demo "fix the failing test" --permission edits   # auto-accept file edits
uv run claudemanctl project run demo "…" --permission full        # every tool call auto-approved — inside the
                                                                   # hardened container, which IS the sandbox
uv run claudemanctl project run demo "continue" --resume <session-id>   # continue a prior session
uv run claudemanctl project run demo "…" --json --timeout 600      # raw provider records; kill after 10 min

# Recreate the container (applies env/port/identity changes). Like `up`, it offers the on-start
# claude update — prompts on a TTY; --update-yes rebuilds to the latest without asking, --no-update skips:
uv run claudemanctl project recreate demo
uv run claudemanctl project recreate demo --update-yes
# Switch a project to a different account (mismatch-guarded; --force to override + re-seed identity):
uv run claudemanctl project recreate demo --profile home
uv run claudemanctl project recreate demo --profile home --force
```

### Repos in a project's workspace

Repos are cloned **host-side** into `workspace/` (the gh PAT / ssh-agent never enters the container);
adding one to a live project clones into the running container's `/workspace` bind immediately — no
recreate. Live git state (branch, clean/dirty, ahead/behind, branch-vs-config drift) is read fresh per
scan.

```bash
uv run claudemanctl project repo add demo git@github.com:org/svc.git   # register + clone live
#   --branch <name>   branch to track          (default: main)
#   --dir <subdir>    workspace subdir         (default: derived from the url)
#   --no-clone        register only; a later `sync-repos` clones it
uv run claudemanctl project repo list demo     # per-repo live state table (fetch-less)
uv run claudemanctl project sync-repos demo    # clone any missing + git fetch all, then show state
uv run claudemanctl project pull demo          # fast-forward each repo (ff-only; skips dirty/diverged)
uv run claudemanctl project repo rm demo svc   # drop from the registry (checkout left on disk)
uv run claudemanctl project repo rm demo svc --purge   # ...and delete the on-disk checkout
```

### Environment mounts (ssh + files + env vars)

Make host material available **inside** the container for the agent's own runtime / git-over-ssh.
`ssh` forwards your running ssh-agent (private keys stay on the host); `file` binds a host file at a
container path (read-only by default); `env <NAME>` prompts (hidden) for a value stored `0600` in the
state tier and injected pass-through as `-e NAME`. Mounts are fixed at create, so a change needs
`recreate`.

```bash
uv run claudemanctl project env add demo ssh                          # agent-forward + ~/.ssh config/known_hosts
uv run claudemanctl project env add demo file ~/.netrc /home/agent/.netrc   # [--rw] writable
uv run claudemanctl project env add demo file ~/Work/CLAUDE.md /workspace/   # workspace-root guidance (cp-style trailing /)
uv run claudemanctl project env add demo env AWS_ACCESS_KEY_ID        # hidden prompt -> 0600 state tier
uv run claudemanctl project env list demo
uv run claudemanctl project resync demo        # re-validate sources + re-seed ssh (no recreate)
uv run claudemanctl project env rm demo /home/agent/.netrc            # by container dst, 'ssh', or env NAME
uv run claudemanctl project ssh-trust demo on  # opt-in TOFU auto-trust of UNKNOWN ssh hosts (re-seeds; common forges are pre-trusted)
```

For **ad-hoc** file transfer (no recreate, nothing persisted) use the scratch dir instead of a `file`
mount: every container gets **`/workspace/scratch/`**, a known drop-zone backed by
`~/.local/state/claude-man/projects/<slug>/workspace/scratch/` on the host (open it from the TUI's
Browse action, `b`). Drop files in while the container runs and tell the agent to "check the data" —
an injected `CLAUDE.md` note points it at `/workspace/scratch/`. It is **wiped on every start and
stop**, so it never persists; keep durable work in a repo under `/workspace/`. (Its sibling
`/workspace/.tmp/` is the container's `TMPDIR` — also wiped each session, not a drop-zone; issue #42.)

Projects launch `claude`/shell at **`/workspace`** (`docker exec -w`) — the uniform anchor where the
workspace `CLAUDE.md` (and any pack-injected guidance) lives; set `[project] workdir = "<subdir>"` in
the TOML to land in a repo dir instead.

### Published ports (ingress)

```bash
uv run claudemanctl project ports add demo 8080          # -p 127.0.0.1:8080:8080 (host-only)
uv run claudemanctl project ports add demo 3000:8080 --bind 0.0.0.0   # LAN-reachable opt-in
uv run claudemanctl project ports list demo
uv run claudemanctl project ports rm demo 3000           # by host port (recreate to apply)
```

Container ports must be ≥ 1024 (`--cap-drop ALL` drops `NET_BIND_SERVICE`). Ports are ingress —
orthogonal to the egress firewall. Fixed at create; recreate to apply.

### Curated packs (guidance templates)

A library of **packs** ships in this repo (`library/packs/`): each pack bundles focused `CLAUDE.md`
fragments and/or skills that travel together — `guardrails`, `code-quality`, `workflow`, plus
per-language convention packs. Projects **select** packs; claude-man **materializes** the selection
into the project's asset source and syncs it into the live binds — changes apply **immediately, no
recreate**. Full design: [`PACKS.md`](PACKS.md).

```bash
uv run claudemanctl packs list                       # browse the library (--tier common|node|python|rust)
uv run claudemanctl project create demo --language node   # defaults: common + node tier packs
uv run claudemanctl project packs list demo          # the project's selection
uv run claudemanctl project packs add demo workflow  # select a pack (applies immediately)
uv run claudemanctl project packs rm demo workflow   # deselect (files removed from source + binds)
uv run claudemanctl project packs defaults demo      # re-apply the library defaults (REPLACES the selection)
```

### Approved tools (a per-project image layer)

A registry of pinned, checksum-verified tools ships in this repo (`library/tools/` —
[`docs/TOOLS.md`](TOOLS.md)); each entry carries the env redirects + smoke probes it needs under the
read-only floor. A project **selects** tools and they are baked as an additive image layer on top of
its overlay (content-addressed `claude-man:<overlay>-t-<hex>`). Recreate to apply.

```bash
uv run claudemanctl tools list [-v]                          # the registry (-v: env redirects + locked-egress hosts)
uv run claudemanctl project create infra --overlay terraform --tool kubectl --tool helm
uv run claudemanctl project tools add infra session-manager-plugin   # validated now; recreate to apply
uv run claudemanctl project tools rm  infra helm
uv run claudemanctl project tools list infra                 # the selection + what `requires` pulls in
uv run claudemanctl project recreate infra                   # builds the layer if missing, recreates on it
```

### Strict egress (lock a project to an allowlist)

Lock a project so its container can only reach an allowlist of domains, routed through a squid proxy
sidecar on a no-direct-route network. Egress is fixed at container create, so lock/unlock
**recreate** the container; the base allowlist always includes `claude.ai` (OAuth refresh), the
Anthropic API, GitHub, and the package registries.

```bash
uv run claudemanctl project create demo --egress strict   # locked from the start
uv run claudemanctl project lock demo            # lock an existing project (builds the proxy image once, recreates)
uv run claudemanctl project unlock demo          # back to open egress (tears the sidecar + network down, recreates)
uv run claudemanctl project egress-log demo      # destinations the allowlist BLOCKED — add legit ones to egress.allowlist
uv run claudemanctl project egress-smoke demo    # verify enforcement: an allowlisted host reaches, a blocked one doesn't
uv run claudemanctl image build proxy            # (re)build the claude-man:proxy squid sidecar image by hand
```

Add project-specific allowlist domains either inline from the TUI (Project menu `p` → Egress… `g`)
or by hand under `[project.egress]` `allowlist = [...]` in the project's TOML, then `project lock
demo` again to re-render and recreate. Today's lock covers proxy-aware traffic (claude, `git` over
HTTPS, npm/pip/apt) plus git-over-ssh to the SSH-over-443 forges; direct-DNS tools are intentionally
not reachable under lock — see [`ARCHITECTURE.md`](ARCHITECTURE.md) § *Network / egress*.

### Auth mode (claude.ai connectors)

By default a project authenticates with the profile's setup-token (`CLAUDE_CODE_OAUTH_TOKEN`).
That token is **inference-only** — claude.ai **account connectors** (remote MCP configured on
your claude.ai account) are unavailable under it (upstream-intentional; local `claude mcp add`
servers work fine — see `docs/DEBUGGING.md`). The opt-in **login mode** fixes that: no token env
is injected, and you run `/login` once inside the container — claude mints its own credential in
the project's claude-config bind, where it self-refreshes and survives stop/recreate.

```bash
uv run claudemanctl project auth demo               # show the mode (+ credential present/absent)
uv run claudemanctl project auth demo login         # opt in (recreate to apply)
uv run claudemanctl project recreate demo
uv run claudemanctl project claude demo             # first launch: /login — authorise in the host
                                                    # browser, paste the code back in the terminal
uv run claudemanctl project auth demo token         # back to the default (recreate to apply)
uv run claudemanctl project logout demo             # remove the minted credential (stop first)
uv run claudemanctl project create demo2 --auth login   # or opt in at create
```

Notes: the project still selects a profile (identity/seeding); on `up` the logged-in account is
verified against it (a mismatch warns; an empty profile email is backfilled). Under **strict
egress**, connector endpoints are remote MCP hosts — allowlist them via `project egress-log` →
the Egress screen. Account connectors auto-load into every session (they can cost significant
context on a heavily-connected account). Security trade-offs: `docs/SECURITY.md` residual risk 6.
In the TUI: Project menu (`p`) → **Auth…** (`a`) — mode switch (recreates to apply) + Logout.

### Git identity + GitHub CLI (`gh`) inside the container

So the agent can `git commit` and `gh` under the read-only rootfs:

- **Git author identity** is injected as git env-config (no writable file needed). It defaults to your
  **host** `git config --global user.{name,email}`; override it per-host in claude-man's global settings.
  **Changing the identity needs a `recreate`** to take effect.
- **`gh` is baked into every image**. A GitHub token is injected **only if you opt in** with
  `config gh-token` (stored `0600` in the state tier, injected pass-through as `GH_TOKEN` — never in
  argv or the config file). Without one, run `gh auth login` inside the container, or supply a token
  via an `env` mount.

```bash
uv run claudemanctl config show                    # resolved git identity + ssh-key load status
uv run claudemanctl config git                      # print the resolved identity (host or override)
uv run claudemanctl config git --name "You" --email you@example.com   # set a claude-man override
uv run claudemanctl config git --clear              # drop the override; inherit the host git config
uv run claudemanctl config gh-token                 # set the GH_TOKEN (hidden prompt; --clear removes)
```

## Sync-back (review-gated config sync)

When a session ends, changes the agent made to its in-container `~/.claude` (agents, skills,
commands, `settings.json` fields, MCP servers) can be diffed against a per-project baseline and
merged back to the **host** `~/.claude` behind an accept/reject gate. Credentials and identity are
never synced (invariant 5); every host target is backed up first.

```bash
uv run claudemanctl sync plan demo          # dry-run: masked diffs of what changed (no write)
uv run claudemanctl sync review demo        # review + apply the DEFAULT decisions
uv run claudemanctl sync review demo --yes  # apply defaults without the prompt
```

Per-row accept/reject is TUI-only (projects table `y` → the sync-review screen).

## Local models (hybrid mode)

```bash
# Manage the models claude-man can use (host Ollama — see MODELS.md for GPU + bind setup):
uv run claudemanctl model presets                 # the curated coding-model table (Qwen3-Coder default)
uv run claudemanctl model add qwen3-coder:30b      # install a preset key or any raw ollama tag (streamed)
uv run claudemanctl model list [--check]           # installed models; --check = update-available probe
uv run claudemanctl model show qwen3-coder:30b     # context length, `tools` capability, quant, family

# Pin / unpin a local model on a project (recreate to apply):
uv run claudemanctl project model set demo qwen3-coder:30b   # -> hybrid mode
uv run claudemanctl project model show demo
uv run claudemanctl project model clear demo                 # back to subscription-direct
uv run claudemanctl project recreate demo

# Or pin a CLAUDE model instead (launched as `claude --model <ref>`; applies at the next
# launch — no recreate, no gateway, allowed on locked projects; one model choice per project):
uv run claudemanctl project model set demo --claude claude-fable-5   # or: opus / sonnet / haiku
```

**Prerequisite:** Ollama runs on the **host** (claude-man manages models, not the server) — GPU
build, `0.0.0.0:11434` bind, model pulled; all in [`MODELS.md`](MODELS.md).

## Terminal & file-manager preferences

`project shell` / `project claude` / `project nvim` open a **detached terminal window** running
`docker exec` into the container, and Browse (`b` in the TUI) opens the workspace in your file
manager. Both are auto-detected per platform, and both are configurable:

```bash
uv run claudemanctl config terminal                 # show the current choice + what's installed
uv run claudemanctl config terminal --program ptyxis # pick a launcher explicitly
uv run claudemanctl config terminal --auto          # back to auto-detect
# Any other terminal, via a template ('{argv}' expands to the docker exec argv;
# '{title}' and '{class}' are also substituted):
uv run claudemanctl config terminal --custom 'myterm --title {title} -e {argv}'

uv run claudemanctl config opener --command 'nautilus'   # Browse opener (the path is appended)
uv run claudemanctl config opener --auto
```

Built-in launchers: `ghostty`, `alacritty`, `kitty`, `wezterm`, `foot`, `ptyxis` (GNOME's default
terminal on Ubuntu 25.10+/Fedora), `gnome-terminal`, `konsole`, `xterm` (Linux/WSL2);
`terminal-app` (Terminal.app — the zero-install macOS fallback), `iterm2`, plus
`kitty`/`alacritty`/`wezterm` (macOS); `wt` (Windows Terminal, WSL2). Auto-detection prefers
`ghostty` → `alacritty` → the rest, so existing setups behave unchanged. A flatpak-only Ptyxis
exposes no `ptyxis` binary on PATH — use the custom template:
`config terminal --custom 'flatpak run app.devsuite.Ptyxis -- {argv}'`.

In the TUI, Settings (`,`) → `e` opens the picker; picking **custom** there opens the template
editor, so unlisted terminals are configurable without the CLI. A launcher that starts and then
fails (a stale template, a Wayland/DISPLAY problem) is surfaced with its exit code and stderr —
in the TUI as a toast + log line, on the CLI as a non-zero exit. The preference lives in
`~/.config/claude-man/config.toml` under `[terminal]` / `[opener]`.

The TUI also opens with a short **boot splash** (any key skips it). Turn it off with
`claudemanctl config splash off` (`[ui] splash = false`).

## Container memory cap

Every project container is created with a **hard memory limit** — `--memory X --memory-swap X`
(equal values, so the container gets **no swap**: a true ceiling). When something inside hits it,
the kernel OOM-kills **inside that container's cgroup**, and the host never comes under memory
pressure. The cap is part of the hardened floor — it is **always applied**; you only choose its
value. Default **`16g`**, minimum `1g`:

```bash
uv run claudemanctl config memory               # show the current cap
uv run claudemanctl config memory 24g           # set it (docker size string: 24g, 8192m, 1.5g)
uv run claudemanctl config memory --default     # back to 16g
```

In the TUI, Settings (`,`) → `m`. Fixed at container create, so **recreate a project to apply**.

## Other config

```bash
uv run claudemanctl config splash off            # disable the TUI boot splash
uv run claudemanctl config shell-history on      # persist in-container bash history across recreate (default off)
uv run claudemanctl config terminal-tint on      # per-project OSC-11 background tint on spawned windows (default off)
uv run claudemanctl config status-bar off        # the per-project status bar on the top row of claude/shell windows (default on)
uv run claudemanctl config image --channel stable --check on   # claude release channel / pin / update check
uv run claudemanctl config ssh add ~/.ssh/id_ed25519           # keys claude-man auto-loads into the host agent
uv run claudemanctl config ssh load
```

## Status bar

Every `project claude` / `project shell` window opens under a **per-project status bar on the top
row** of the terminal (issue #37): the project slug in its palette colour, then profile · auth ·
image · model · egress, and the git branch of the current directory + a clock on the right. It is
drawn *inside* the terminal (a baked tmux session), so it shows on decoration-less tiling desktops
where the window title and background tint never do. Default **on**:

```bash
uv run claudemanctl config status-bar            # show
uv run claudemanctl config status-bar off        # plain launch (the pre-#37 behaviour)
```

Launch-time — applies to the next window, no recreate. The image must bake the launcher: after
upgrading run `image build base`, then any `up`/`recreate` (or `image build --project <slug>`)
rebuilds the project's now-stale overlay chain on it automatically. Until then a window opens
plainly and says so (a stderr line here, a toast in the TUI). Scrollback while the bar is on: the
mouse wheel, or **Shift+PgUp / Alt+PgUp** (page up into tmux's history; Shift/Alt+PgDn back, exits at
the bottom — use the Alt form under ghostty, which keeps Shift+PgUp for itself). Closing a claude
window leaves claude running in its session; the next `project claude` **re-attaches** to it
(`Ctrl-b d` detaches by hand). `nvim` windows never get the bar. In the TUI: Settings (`,`) → `s`.

## Building images

```bash
uv run claudemanctl image build base                  # the hardened base image
uv run claudemanctl image build python                # an overlay (base|python|rust|node|python-node|terraform)
uv run claudemanctl image build base --claude-version 2.1.160   # pin the claude version
uv run claudemanctl image build base --dry-run        # print the docker build argv only, don't run
uv run claudemanctl image smoke base                  # gate an image against the hardened run profile
uv run claudemanctl image build --project infra       # a project's image: overlay + its selected tools (docs/TOOLS.md)
uv run claudemanctl image smoke --project infra       # + every selected tool's core-op probes under the floor
```

Images chain `base → <overlay> → <overlay>-t-<hash>` (the per-project tools layer). A layer is
`FROM` its parent's tag at build time, so rebuilding a parent does **not** update an existing child.
claude-man detects that: a layer created *before* its parent is **stale**, and both `image build
--project` and the `up`/`create`/`recreate` pre-flight rebuild it (and everything below it) on the
current parent. So after `image build base`, the next start of each project rebuilds its overlay
chain once — no manual per-overlay rebuild. A cache-hit base rebuild keeps its original timestamp,
so an unchanged base never triggers rebuilds.

## Windows (WSL2) notes

Everything runs **inside** the WSL2 distro — clone, `uv sync`, and run claude-man there, with
either Docker Desktop's WSL integration or docker-ce installed in the distro. The host ssh-agent,
state dirs, and workspaces are all distro-side, exactly like native Linux. `project shell|claude|nvim`
opens Windows Terminal tabs (running `wsl.exe -e docker exec …`), and Browse opens the workspace
in Explorer via `wslview` (install [`wslu`](https://wslutiliti.es/wslu/) for the best Browse
experience). Running claude-man from native Windows (PowerShell/cmd) is not supported.
