# claude-man

A Python **Textual TUI** + a scriptable **`claudemanctl`** CLI that provisions, persists, and
manages **hardened Docker containers**, each running **Claude Code** under a chosen **account
profile** (e.g. work / home), for a set of long-lived **git-checkout projects** on a single
host.

It exists to solve five things at once:

1. **Multiple accounts** — launch each Claude instance under a chosen profile. A profile is one
   OAuth identity minted once with `claude setup-token` and injected per-launch as
   `CLAUDE_CODE_OAUTH_TOKEN`. Each project picks a profile (or inherits the default).
2. **Persistent projects** — a project is a named set of git repos checked out once and kept
   across container restarts and host reboots **until you explicitly delete it**. The manager
   shows the live state, profile, egress mode, and version of every project.
3. **Secure sandbox** — every project runs in its own hardened container (read-only rootfs,
   all capabilities dropped, no-new-privileges, non-root, pid-limited), loadable with project
   environment variables and extra software via image overlays. Egress is open by default and
   **lockable** to a strict per-project allowlist.
4. **Curated packs** — an in-repo library of guidance templates (focused `CLAUDE.md` fragments +
   skills, bundled as **packs**: guardrails, code-quality, per-language conventions) that
   projects opt into. Selections materialize into each project's config automatically, so house
   rules are versioned, reviewed, and improved in **one place** instead of drifting per project.
5. **Config sync-back** — when you close a session, changes the agent made to its Claude config
   (agents, skills, slash-commands, `settings.json`, MCP servers, memory, `CLAUDE.md`) are
   diffed against a baseline and offered back to your host config behind an **accept/reject
   gate**. Credentials and identity are **never** synced.

> Status: **alpha — phases 0–4 + 6 working, 5 planned** (2026-06-12). Mint work/home profiles; create /
> start / stop / shell / run Claude in hardened containers under a chosen account; switch accounts
> (mismatch-guarded); watch per-account token usage **and live 5-hour / weekly subscription-limit
> bars**; commit + push from inside a container (inherited git identity + `gh` baked into every image);
> add / remove / inspect a project's git repos with live state; mount ssh (agent-forward) + host
> files into a container; publish service ports; **lock a project to strict egress** (a squid
> allowlist proxy on a no-direct-route network); select **curated packs** of CLAUDE.md guidance +
> skills per project (defaults by language, drift-tracked, applied live) — all from both the CLI
> and the TUI.
> **Not yet implemented** (an honest `NotImplementedError` stub): Phase 5 review-gated config
> sync-back — see [`ROADMAP.md`](ROADMAP.md). 533 dependency-free tests; the hardened image is
> `image smoke`-gated.

## Platform support

| Host | Status | Notes |
|---|---|---|
| **Linux** | ✅ supported | The reference platform (developed against Docker 29.x). |
| **macOS** | ✅ supported | Docker Desktop runs the same Linux image; ssh-agent forwarding uses Docker Desktop's built-in default-agent socket; Terminal.app works out of the box (iTerm2/kitty/alacritty/wezterm too). |
| **Windows** | ✅ via **WSL2** only | Run claude-man *inside* a WSL2 distro (with Docker Desktop's WSL backend or docker-ce in the distro) — there it *is* Linux. Windows Terminal (`wt`) and `explorer.exe`/`wslview` are auto-detected for spawned windows / Browse. **Native Windows is out of scope.** |

The container side is identical everywhere: the image is Linux regardless of host, so the hardened
profile never varies. On macOS, note that bind-mount I/O (VirtioFS) is slower than native Linux —
large `yarn`/`npm` installs in `/workspace` take noticeably longer.

## How it fits together

```
~/.config/claude-man/         durable, secret-free definitions (git-versionable)
  profiles/<name>.toml          one account identity (display name, email, default flag)
  projects/<slug>.toml          a project EXISTS iff this file exists
  assets/<slug>/                per-project asset source (CLAUDE.md, skills) — synced into the
                                binds on start; curated packs materialize here

~/.local/state/claude-man/    durable runtime state (some secret; never committed)
  profiles/<name>/token         0600 long-lived OAuth token (from `claude setup-token`)
  profiles/<name>/identity.json scrubbed oauthAccount block (no UUIDs)
  profiles/<name>/seed/         allowlisted ~/.claude assets new projects inherit
  projects/<slug>/workspace/    the checked-out repos  ->  bind /workspace
  projects/<slug>/claude-config/ per-project CLAUDE_CONFIG_DIR  ->  bind /home/agent/.claude
  projects/<slug>/packs-manifest.json  which files the pack system manages (ours/theirs boundary)
  sync-audit/                   git repo: per-session commit of accepted sync-back

<the claude-man checkout>/    the install is the clone
  library/packs/<tier>/<pack>/  the curated pack library (versioned with the code)

docker                        the live status oracle
  one named container per project: claude-man-<slug>, labelled claude-man.*
```

Both roots default to `$XDG_CONFIG_HOME` / `$XDG_STATE_HOME` (falling back to `~/.config` /
`~/.local/state`), and can be relocated wholesale with the `CLAUDE_MAN_CONFIG_HOME` /
`CLAUDE_MAN_STATE_HOME` env overrides — the test suite points these at a tmpdir so it never touches
real operator state.

The TOML registry answers *what a project is*; `docker ps` (queried fresh, never cached) answers
*what state its container is in right now*. The two never describe the same fact, so they can't
drift — on any divergence the **registry wins** and the container is recreated.

## What it protects against

claude-man's containment is a **headline feature**, not a side effect. The 2026 wave of
supply-chain attacks on AI coding agents — poisoned npm/PyPI packages whose install hooks steal
`~/.claude/.credentials.json`, GitHub/cloud/SSH keys; malicious skills/agents that run shell
commands without approval; `~/.claude.json` rewrites that redirect your OAuth token to an
attacker; `rm -rf ~/` trip-wires; `-setup.pth` startup-hook persistence — all assume **the agent,
its config, and your credentials share one host filesystem**. Running Claude Code inside a
hardened, per-project container breaks that assumption by construction. The point isn't to stop a
compromised package from *installing* — `npm install` still runs its hooks — it's to ensure the
blast radius is **one disposable container**, not your machine.

| Attack technique (real 2026 TTPs) | What claude-man does |
|---|---|
| **Steal `~/.claude/.credentials.json`** | The file is **never** in the container — auth is an env-var OAuth token, never a credentials file (invariant 1). There is nothing to read. |
| **Harvest SSH private keys** | Keys **never enter the container**: only the host ssh-agent *socket* is forwarded, so a payload can't exfiltrate a key (it can sign while connected — a documented residual). |
| **Poison host `~/.claude/settings.json` hooks** | The per-project `~/.claude` is seeded from a **filtered allowlist with hooks/statusLine stripped**; there is no path for a poisoned host hook to ride into the container, nor (today) for a container-written hook to reach the host. |
| **`rm -rf ~/` destructive trip-wire** | `~` is `/home/agent` inside the sandbox — the wiper can only touch the re-seedable per-project config and a tmpfs. **The host home is untouched.** |
| **`-setup.pth` / daemon persistence** | The rootfs is **read-only** (`--read-only`), capabilities are **all dropped** (`--cap-drop ALL`), `no-new-privileges`, non-root, pid-limited. Image-level persistence is impossible; anything in a `/workspace` venv dies on recreate. |
| **Malicious skill runs `Bash(*)` / reverse shell** | The command still runs — but **inside** the sandbox: no host reach, and the reverse shell needs egress + tooling the minimal base image doesn't ship. Under **strict egress** the connection is refused outright. |
| **Exfiltrate secrets / redirect OAuth token to attacker infra** | **Strict egress** (below) routes every connection through an allowlisting proxy on a no-direct-route network — the token and any env secrets can only reach Anthropic/allowlisted hosts, never an attacker endpoint. |
| **Steal the host GitHub PAT during clone** | Repos are cloned **host-side** with the PAT masked; the host git PAT **never enters the container**. |

### Strict egress (the lockable network boundary)

Egress is **open by default** and **lockable per project** to a strict allowlist. Because
`--cap-drop ALL` forbids in-container `iptables`, the firewall lives at the **network layer**: the
agent runs on an `internal` Docker network with **no direct route out**, and a **squid proxy
sidecar** is the only path to the internet. Only allowlisted domains are reachable — the base set
always includes `claude.ai` (OAuth refresh), the Anthropic API, GitHub, and the package registries;
everything else is **denied and logged** so you can tune the list. HTTPS stays end-to-end (CONNECT
tunnels, no MITM, no CA install). Lock a project with `project lock <slug>` (or create it
`--egress strict`); the denied-request log surfaces in the TUI for allowlist tuning.

This is the single biggest lever against the *exfiltration* and *command-and-control* steps that
nearly every one of the attacks above depends on. See [`docs/SECURITY.md`](docs/SECURITY.md) for
the full threat model and [`CLAUDE.md`](CLAUDE.md) for the load-bearing invariants that keep these
guarantees true.

## Install

claude-man runs **from a git checkout** (no PyPI package yet):

```bash
git clone https://github.com/richardjr/claude-man.git
cd claude-man
uv sync          # installs textual + tomlkit into .venv
```

The checkout location matters: image builds resolve `images/` relative to the source tree, so keep
the clone around (it *is* the install). Runtime state lives outside it, under
`~/.config/claude-man` and `~/.local/state/claude-man`.

## Quick start

```bash
uv sync                       # install textual + tomlkit into .venv
uv run claudemanctl --help    # the CLI surface
uv run claudeman              # launch the TUI
uv run python -m unittest     # run the (dependency-free) unit tests

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

Prefer the TUI? [`docs/TUI-GUIDE.md`](docs/TUI-GUIDE.md) is the detailed walkthrough of
building the same environment from `uv run claudeman` — minting a profile, creating a
project, adding repos, git identity + GitHub CLI setup, and ssh-agent pass-through, with
every keybinding and screen.

## Setting up accounts (profiles)

A **profile** is one Claude account identity, minted once on the host with `claude setup-token`
and injected per-launch as `CLAUDE_CODE_OAUTH_TOKEN`. claude-man never copies `.credentials.json`
and never sets `ANTHROPIC_*` keys (see invariant 1 in [`CLAUDE.md`](CLAUDE.md)).

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
uv run claudemanctl profile limits [work]   # per-account 5-hour + weekly subscription-limit bars + reset
```

### Subscription usage bars (5-hour + weekly)

`profile limits` (and the TUI's per-profile panel) show how close each **account** is to its Claude
subscription limits — the rolling 5-hour and weekly *utilization* windows that `claude`'s `/usage`
reports. claude-man reads `GET …/api/oauth/usage` host-side with each profile's stored OAuth token; it's
read-only and **does not consume quota**. These are account-wide figures (all usage on the account, host
sessions included), not just what claude-man's containers spent.

Reading usage needs the token to carry the `user:profile` scope. `claude setup-token` historically
minted `user:inference` only, so **existing tokens read `re-mint` until you re-mint them** —
`claudemanctl profile renew <name>` mints a token with both scopes (it runs inference *and* reads
usage). Newly added profiles already get both scopes.

## Managing projects

```bash
# Create a project, choosing its account, image overlay, pack language, and egress mode:
uv run claudemanctl project create demo --profile work --overlay python --language python --egress open
#   --profile <name>             account to run under   (default: the default profile)
#   --overlay base|python|rust|node   toolchain baked into the image (default: base)
#   --language <tier>            curated-pack tier whose defaults apply (see Curated packs below)
#   --egress  open|strict        network policy         (default: open; strict = allowlist egress proxy)

uv run claudemanctl project up demo         # create-if-needed + start
uv run claudemanctl project status [demo]   # live state JOINed with the registry (all, or one slug)
uv run claudemanctl project stop demo       # stop the container (project + workspace are kept)
uv run claudemanctl project shell demo      # open a shell in a new terminal
uv run claudemanctl project claude demo     # run claude in a new terminal
uv run claudemanctl project nvim demo       # open neovim (baked into the image) in a new terminal

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

### Environment mounts (ssh + files)

Make host material available **inside** the container for the agent's own runtime / git-over-ssh.
`ssh` forwards your running ssh-agent (private keys stay on the host); `file` binds a host file at a
container path (read-only by default). Mounts are fixed at create, so a change needs `recreate`; the
base image needs `openssh-client` (rebuild with `image build base`).

```bash
uv run claudemanctl project env add demo ssh                          # agent-forward + ~/.ssh config/known_hosts
uv run claudemanctl project env add demo file ~/.netrc /home/agent/.netrc   # [--rw] writable
uv run claudemanctl project env add demo file ~/Work/CLAUDE.md /workspace/   # workspace-root guidance (cp-style trailing /)
uv run claudemanctl project env list demo
uv run claudemanctl project resync demo        # re-validate sources + re-seed ssh (no recreate)
uv run claudemanctl project env rm demo /home/agent/.netrc            # by container dst or 'ssh'
```

Projects launch `claude`/shell at **`/workspace`** (`docker exec -w`) — the uniform anchor where the
workspace `CLAUDE.md` (and any pack-injected guidance) lives; set `[project] workdir = "<subdir>"` in
the TOML to land in a repo dir instead.

### Curated packs (guidance templates)

A library of **packs** ships in this repo (`library/packs/`): each pack bundles focused `CLAUDE.md`
fragments and/or skills that travel together — `guardrails` (never commit unasked, no destructive
git, no secrets), `code-quality`, `workflow`, plus per-language convention packs (`node-conventions`,
`python-uv`, `rust-cargo`). Projects **select** packs; claude-man **materializes** the selection into
the project's asset source and syncs it into the live binds, so the agent picks it up at its next
session launch — changes apply **immediately, no recreate**. Because the library is versioned here,
improving a rule once improves it for every project on the next start.

```bash
uv run claudemanctl packs list                       # browse the library (--tier common|node|python|rust)
uv run claudemanctl project create demo --language node   # defaults: common + node tier packs
uv run claudemanctl project packs list demo          # the project's selection
uv run claudemanctl project packs add demo workflow  # select a pack (applies immediately)
uv run claudemanctl project packs rm demo workflow   # deselect (files removed from source + binds)
uv run claudemanctl project packs defaults demo      # re-apply the library defaults (REPLACES the selection)
```

How it behaves (full design: [`docs/PACKS.md`](docs/PACKS.md)):

- **Defaults are explicit, not creeping** — resolved once at `project create` from the `common` +
  `--language` tiers and written into the project TOML. A new default added to the library later
  never silently lands in existing projects (`packs defaults` re-applies on demand).
- **Fragments are linked, not inlined** — they land under `/workspace/.claude-man/<pack>/` and are
  referenced from the workspace `CLAUDE.md` via a fenced block of `@` imports; everything you write
  outside that block is never touched. Skills land under `~/.claude/skills/`.
- **Yours vs theirs is tracked** — a manifest records what the pack system manages. Your own files
  always win collisions; deselecting removes only pack-managed files; an in-container edit to a
  pack file is **curated-wins** (re-stamped from the library on next start, backed up first —
  improvements belong upstream in the library).
- **TUI**: select a project, `p` → `p` (Project… → Packs…) — a checklist grouped *Common* / your
  language tier, with a **State** column showing drift (`stale` / `⚠ drifted` / `operator file
  wins`); `d` re-applies defaults. The create form (`n`) has a Language field, pre-filled from the
  Overlay choice.
- The library is **public content** (this repo is public) — house rules and generic conventions go
  in; anything client- or project-specific stays in the per-project asset source
  (`~/.config/claude-man/assets/<slug>/`).

### Strict egress (lock a project to an allowlist)

Lock a project so its container can only reach an allowlist of domains, routed through a squid proxy
sidecar on a no-direct-route network (see *What it protects against* above for why). Egress is fixed
at container create, so lock/unlock **recreate** the container; the base allowlist always includes
`claude.ai` (OAuth refresh), the Anthropic API, GitHub, and the package registries.

```bash
uv run claudemanctl project create demo --egress strict   # locked from the start
uv run claudemanctl project lock demo            # lock an existing project (builds the proxy image once, recreates)
uv run claudemanctl project unlock demo          # back to open egress (tears the sidecar + network down, recreates)
uv run claudemanctl project egress-log demo      # destinations the allowlist BLOCKED — add legit ones to egress.allowlist
uv run claudemanctl project egress-smoke demo    # verify enforcement: an allowlisted host reaches, a blocked one doesn't
uv run claudemanctl image build proxy            # (re)build the claude-man:proxy squid sidecar image by hand
```

Add project-specific allowlist domains under `[project.egress]` `allowlist = [...]` in the project's
TOML, then `project lock demo` again to re-render and recreate. In the TUI, the **Project** menu
(`p`) → **Egress log** (`g`) shows blocked destinations. Today's lock covers proxy-aware traffic
(claude, `git` over HTTPS, npm/pip/apt); `ssh`-based git and direct-DNS tools are intentionally not
reachable under lock — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) § *Network / egress*.

### Git identity + GitHub CLI (`gh`) inside the container

So the agent can `git commit` and `gh` under the read-only rootfs:

- **Git author identity** is injected as git env-config (no writable file needed). It defaults to your
  **host** `git config --global user.{name,email}`; override it per-host in claude-man's global settings.
  The on-disk `git config --global` and `gh` config land on a writable tmpfs, so `git config --global`
  and `gh auth login` work in-container without hitting the read-only rootfs. **Changing the identity
  needs a `recreate`** to take effect.
- **`gh` is baked into every image** (pinned GitHub CLI; rebuild base, then any overlay, to add it to an
  existing install). A GitHub token is injected **only if you opt in** with `config gh-token` (stored
  `0600` in the state tier, injected pass-through as `GH_TOKEN` — never in argv or the config file).
  Without one, auth is the operator's job: run `gh auth login` inside the container (it writes the
  writable config dir), or supply a token via an `env` mount.

```bash
uv run claudemanctl config show                    # resolved git identity + ssh-key load status
uv run claudemanctl config git                      # print the resolved identity (host or override)
uv run claudemanctl config git --name "You" --email you@example.com   # set a claude-man override
uv run claudemanctl config git --clear              # drop the override; inherit the host git config

# gh needs an image rebuild + a recreate of the project:
uv run claudemanctl image build base
uv run claudemanctl image build node                # (or whichever overlay the project uses)
uv run claudemanctl project recreate demo
```

In the TUI, the **Settings** screen (press `,`) shows the resolved identity and ssh-key status; press
`g` there to open the git-identity edit modal (leave a field blank to inherit the host). Recreate a
project afterwards to apply.

## Terminal & file-manager preferences

`project shell` / `project claude` / `project nvim` open a **detached terminal window** running `docker exec` into
the container, and Browse (`b` in the TUI) opens the workspace in your file manager. Both are
auto-detected per platform, and both are configurable:

```bash
uv run claudemanctl config terminal                 # show the current choice + what's installed
uv run claudemanctl config terminal --program kitty # pick a launcher explicitly
uv run claudemanctl config terminal --auto          # back to auto-detect
# Any other terminal, via a template ('{argv}' expands to the docker exec argv;
# '{title}' and '{class}' are also substituted):
uv run claudemanctl config terminal --custom 'myterm --title {title} -e {argv}'

uv run claudemanctl config opener --command 'nautilus'   # Browse opener (the path is appended)
uv run claudemanctl config opener --auto
```

Built-in launchers: `ghostty`, `alacritty`, `kitty`, `wezterm`, `foot`, `gnome-terminal`,
`konsole`, `xterm` (Linux/WSL2); `terminal-app` (Terminal.app — the zero-install macOS fallback),
`iterm2`, plus `kitty`/`alacritty`/`wezterm` (macOS); `wt` (Windows Terminal, WSL2).
Auto-detection prefers `ghostty` → `alacritty` → the rest, so existing setups behave unchanged.
In the TUI, Settings (`,`) → `e` opens the picker. The preference lives in
`~/.config/claude-man/config.toml` under `[terminal]` / `[opener]`.

The TUI also opens with a short **boot splash** (the logo sweeps in, then scrolls up to reveal
the live table — about a second, any key skips it). Turn it off with
`claudemanctl config splash off` (`[ui] splash = false`).

## Building images

```bash
uv run claudemanctl image build base                  # the hardened base image
uv run claudemanctl image build python                # an overlay (base|python|rust|node)
uv run claudemanctl image build base --claude-version 2.1.160   # pin the claude version
uv run claudemanctl image build base --dry-run        # print the docker build argv only, don't run
uv run claudemanctl image smoke base                  # gate an image against the hardened run profile
```

See [`docs/TUI-GUIDE.md`](docs/TUI-GUIDE.md) for the TUI walkthrough (profile → project →
GitHub → ssh), [`CLAUDE.md`](CLAUDE.md) for the invariants any contributor (human or Claude)
must keep, [`ROADMAP.md`](ROADMAP.md) for the phase plan,
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design,
[`docs/PACKS.md`](docs/PACKS.md) for the curated-pack system, and
[`docs/SECURITY.md`](docs/SECURITY.md) for the threat model and hardening rationale.

## Host requirements

- **Linux** (the reference platform), **macOS** (Docker Desktop), or **Windows via WSL2** — see
  *Platform support* above.
- Docker — rootful docker-ce on Linux/WSL2 (designed against Docker 29.x), or Docker Desktop on
  macOS (also fine as the WSL2 backend on Windows).
- Python ≥ 3.11 and [`uv`](https://docs.astral.sh/uv/).
- A terminal emulator for spawned shells — any of the built-in launchers above, or your own via
  `config terminal --custom`. macOS needs nothing extra (Terminal.app); on WSL2, Windows Terminal
  is picked up automatically (install [`wslu`](https://wslutiliti.es/wslu/) for the best Browse
  experience — `wslview` translates paths for Windows Explorer).
- A Claude Code install on the host to mint profile tokens (`claude setup-token`).

### Windows (WSL2) notes

Everything runs **inside** the WSL2 distro — clone, `uv sync`, and run claude-man there, with
either Docker Desktop's WSL integration or docker-ce installed in the distro. The host ssh-agent,
state dirs, and workspaces are all distro-side, exactly like native Linux. `project shell|claude|nvim`
opens Windows Terminal tabs (running `wsl.exe -e docker exec …`), and Browse opens the workspace
in Explorer via `wslview`. Running claude-man from native Windows (PowerShell/cmd) is not
supported.

## Contributing & security

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — dev setup, the test/lint gate, and the load-bearing
  invariants every change must preserve.
- [`SECURITY.md`](SECURITY.md) — how to report a vulnerability (privately, please).

## License

[MIT](LICENSE) © Richard Reynolds
