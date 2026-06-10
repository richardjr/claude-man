# CLAUDE.md

Guidance for Claude Code (and humans) working in the **claude-man** repository.

claude-man is a Python **Textual TUI** + **`claudemanctl`** CLI that provisions and manages
hardened Docker containers, each running Claude Code under a chosen account profile, for a set of
long-lived git-checkout projects. Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full
design and [`ROADMAP.md`](ROADMAP.md) for the phase plan before making non-trivial changes.

## Load-bearing invariants — do not break these

These are security- and correctness-critical. Every change must preserve them.

1. **Never copy `.credentials.json` into a container, and never inject `ANTHROPIC_API_KEY` /
   `ANTHROPIC_AUTH_TOKEN`.** Auth is the env-var long-lived token model: `claude setup-token` once
   per profile on the host → a `0600` token file → injected at launch as `CLAUDE_CODE_OAUTH_TOKEN`.
   Copying `.credentials.json` triggers the known headless 401/no-refresh bug; `ANTHROPIC_*` keys
   silently outrank the OAuth token and can bill the wrong account, so they are scrubbed from the
   rendered container env — including `env_file`, which is parsed + scrubbed host-side and injected
   as pass-through so values never reach argv. Never pass `--bare` to the in-container `claude`
   (it ignores the token). Relatedly, a **`file` env-mount's container `dst` may never target
   `/home/agent/.claude` (or any managed mount)** — a bind onto `…/.claude/.credentials.json` would
   smuggle a working credentials file in (a verified attack); `schema.EnvMount` rejects it.
   The in-container **git identity and GitHub CLI do not breach this**: the git author identity is
   non-secret (`user.name`/`user.email`), injected as git ENV-config (`GIT_CONFIG_COUNT` +
   `GIT_CONFIG_KEY_n`/`VALUE_n`) rendered as plain `-e KEY=value`. `gh` is the binary only **by
   default**; a **`GH_TOKEN` is injected only when the operator explicitly configures one**
   (`config gh-token`, or the Settings `t` entry) — stored `0600` in the **state tier**
   (`gh_token.py` → `config.gh_token_path()`; NEVER in the secret-free `config.toml`, never synced),
   injected **pass-through** (`-e GH_TOKEN`, value via the child env, never argv) exactly like the
   OAuth token, and only into containers when set. This is safe where `ANTHROPIC_*` is not: `GH_TOKEN`
   doesn't outrank Claude auth or mis-bill, so opt-in injection doesn't breach invariant 1. With no
   token configured, none is injected (the operator can still `gh auth login` in-container or supply
   one via an env-mount). The token value is never echoed (`config show` reports only set/none).
   The **same pass-through + state-tier model generalises** to arbitrary operator env vars: a
   `[[project.env_mount]]` of `kind = "env"` carries a `name` (in the registry) whose VALUE lives
   `0600` in the state tier (`env_secrets.py` → `config.project_env_path()`, never config.toml/synced)
   and is injected `-e NAME` pass-through. The name is validated and **`FORBIDDEN_ENV_NAMES`**
   (the scrubbed keys + the OAuth token + `GH_TOKEN`) are rejected, so an operator var can never shadow
   the auth/sole-sourced secrets.
2. **The hardened run profile is the floor, not a suggestion.** `--read-only`, `--cap-drop ALL`,
   `--security-opt no-new-privileges`, `--user 1000:1000`, `--pids-limit 1024`, with writable
   surfaces limited to: the persistent `claude-config` bind (`/home/agent/.claude`), the persistent
   `workspace` bind (`/workspace`), two `tmpfs` mounts (`/tmp`, `/home/agent/.cache`, both `exec`),
   and — **only when a project has an `ssh` env-mount** — a `0700` `/home/agent/.ssh` tmpfs (for
   `known_hosts`/`config`; keys never enter — the host agent socket is forwarded). The
   `/home/agent/.cache` tmpfs **must be pinned agent-owned** (`uid=1000,gid=1000,mode=0700` in
   `_HARDENING`): a bare tmpfs defaults to `root:root` mode 755 and the agent (uid 1000) can't write
   it, so node/corepack (`mkdir ~/.cache/node`), claude's `XDG_STATE_HOME` (`~/.cache/state`), the
   git/gh config redirects (below), and the yarn home-config redirect (`~/.yarnrc` is symlinked onto
   the `.cache` tmpfs in the image, for Yarn Classic v1's `saveHomeConfig` write) all fail `EACCES`.
   (Yarn's package CACHE rides the disk-backed `/workspace` bind via `YARN_CACHE_FOLDER`, NOT the
   size-capped tmpfs — a 256m cache OOM'd a large v1 install with ENOSPC.) Pinning the owner is *not* a floor relaxation —
   it makes a declared-writable surface actually writable, as this invariant intends (`/tmp` needs no
   pin: Docker special-cases it to sticky 1777). The image bakes a
   real `/etc/passwd` entry + `HOME` for uid 1000 (without it, `getpwuid` fails under `--read-only
   --user` and `HOME` resolves to `/`). **Env-mounts (`[[project.env_mount]]`) are additive `-v`/
   `--tmpfs`/`-e` only** — `docker/runner.py::_render_env_mounts` never emits a `_HARDENING` flag, so
   the floor is byte-identical with or without them (a unit test pins this). Do not relax these to
   "make something work" — fix the writable-mount set or the image instead, and re-run
   `claudemanctl image smoke`.
3. **The firewall lives at the network layer, never in-container iptables.** `--cap-drop ALL`
   forbids `NET_ADMIN`, so strict egress is a squid+dnsmasq **sidecar** on an `internal: true`
   network, not `iptables` inside the agent container. The base allowlist must always include
   `claude.ai` (the OAuth subscription refresh path) or token refresh fails opaquely.
4. **Registry is the source of truth; docker labels are a projection.** A project exists iff its
   `~/.config/claude-man/projects/<slug>.toml` exists. Live status is read fresh from
   `docker ps`/`inspect` and **never** cached. On any divergence, reconcile *toward* the registry by
   recreating the container (re-stamping labels) — never edit the registry from labels.
5. **Sync-back enforces the denylist before any read, and again at git-staging time.** Never read
   or sync: `.credentials.json`, `.claude.json` (wholesale), `.config.json`, `history.jsonl`,
   `sessions/`, transcripts, `shell-snapshots/`, `statsig`/`cache/`, `file-history/`, `tasks/`,
   `plans/`, `*-cache.json`, `backups/`; and the JSON keys `oauthAccount`, `userID`, `accountUuid`,
   and `last*`/`cached*`/telemetry keys. `settings.json` is **field-patched** (host hooks +
   statusLine are structurally immune), MCP changes are applied via `claude mcp add/remove --scope`,
   and every host target is **backed up before** merge. Deletions and conflicts **default to reject**.
   See `src/claudeman/syncback/denylist.py`.
6. **One `claude` per container.** A second shell is fine; a second `claude` in the same container
   races on `.claude.json`/session writes. `terminals.spawn_claude` enforces this (REVIEW SEC-3):
   it probes the container for a live `claude` process (`build_claude_probe_argv`, a /proc comm
   walk — fails OPEN so a wedged daemon can't lock the operator out) and refuses to spawn a second.
   Every claude-launch path must go through `spawn_claude` — don't add code paths that bypass it,
   and avoid launching a second `claude` manually from a shell (the guard can't see a future one).

## Project layout

```
src/claudeman/
  config.py            XDG paths + all shared constants (label prefix, container/image names, baked container paths)
  hostplatform.py      per-host seams (Linux reference / macOS / WSL2; native Windows out of scope): pure platform predicates, the Docker Desktop ssh-agent magic socket, uid-advisory gating. ALL platform branches go through here — no inline sys.platform checks elsewhere
  cli.py               claudemanctl argparse surface (profile / project / sync / image verbs)
  lifecycle.py         create / up / stop / recreate / delete orchestration shared by the CLI + TUI (+ account-mismatch guard, workspace-ownership pre-flight, env-mount add/remove/resync + ssh seed, sync-checked delete_plan/delete_project teardown, asset sync-in on up / sync-out on stop, on-start claude-version check (`check_update`) -> operator-confirmed host-side image rebuild + recreate before start via `up(rebuild_to=...)`; stamps the container version from the image's real baked label)
  assets.py            per-project asset sync (host-side copy of CLAUDE.md + skills/agents between the synced config-tier source ~/.config/claude-man/assets/<slug>/ and the /workspace + ~/.claude binds): sync_in on start (asset wins), sync_out on stop (bind wins), backup-then-overwrite; claude side is a default-DENY allowlist (skills/agents/commands only) with a per-entry filtered recursive copy that drops denylisted-named nested entries + refuses escaping / denylist-targeting symlinks; workspace side is containment-checked; bootstraps a stub CLAUDE.md — distinct from the Phase-5 review-gated sync-back
  usage.py             per-profile token-usage parsed from project transcripts (read-only, separate from sync-back)
  usage_api.py         per-account subscription usage (5-hour + weekly bars) via GET /api/oauth/usage with a profile's OAuth token — no-redirect opener (no cross-host token leak); pure parse/render split from the network fetch
  updates.py           resolve the latest/stable claude version (token-less GET of downloads.claude.ai/claude-code-releases/<channel> — same endpoint the native installer reads) so the on-start check can offer a host-side image rebuild before `up` when a newer claude exists; pure parse/compare split from the fetch, fails OPEN (offline -> start on the existing image). Never an in-container update (`~/.local` is read-only — invariant 2 holds)
  gitconfig.py         resolve the git author identity (config.toml [git] override, else inherited host git config) → GIT_CONFIG_* env injected at docker create (no writable file needed under --read-only)
  gh_token.py          optional GitHub token (state-tier 0600, NOT config.toml) injected pass-through as GH_TOKEN for in-container `gh` — opt-in via `config gh-token` (invariant 1)
  env_secrets.py       per-project `kind="env"` env-mount VALUES (state-tier 0600 env.json, NOT config.toml/synced) — names live in the registry; values injected `-e NAME` pass-through (invariant 1)
  (ports)              published container ports (`[[project.ports]]` -> `schema.PortMapping`): INGRESS, rendered additively as `-p <bind>:<host>:<container>/<proto>` by `docker/runner._render_ports` (never a `_HARDENING` flag — floor byte-identical, unit-pinned). container port MUST be ≥1024 (`--cap-drop ALL` drops NET_BIND_SERVICE); default bind 127.0.0.1 (host-only) with per-port `0.0.0.0` opt-in. Orthogonal to the egress firewall (invariant 3 — ingress, not egress). Fixed at create -> recreate to apply
  __main__.py          `python -m claudeman` -> TUI;  argv dispatch
  registry/            projects.py, profiles.py (load/save/default/load_token/token_age), settings.py (global config.toml: ssh keys + git identity), schema.py  — TOML store
  docker/              labels.py, runner.py (hardened `docker create` argv + env_file scrub + additive env-mount render + exec-stdin ssh seed + git_env identity + baked GIT_CONFIG_GLOBAL/GH_CONFIG_DIR redirects), status.py (live ps JOIN), images.py (build/exists + base→overlay auto-build chain), smoke.py (hardened-profile image gate)
  profiles/            setup_token.py (mint/renew/verify via `claude setup-token`+`auth status`), identity.py (scrubbed stub), seed.py (claude-config seeding + host ~/.claude capture)
  checkout/            repos.py (host-side clone/fetch into workspace/ + cred-mask + dir containment; host PAT never enters the container), gitstate.py (porcelain-v2 parser → per-repo live state: branch/dirty/ahead-behind/drift)
  network/             allowlist.py (base egress set), squid.py (strict-egress sidecar generator — Phase 4 stub)
  syncback/            denylist.py, artifacts.py, diff.py (impl); baseline.py, detect.py, merge.py — Phase 5 stubs of the review-gated 3-way merge
  tui/                 app.py (projects JOIN + live Repos column / repo-detail panel via a 30s gitstate worker + per-profile usage panel — token totals plus 5h/Week subscription bars from a 60s refresh_utilization worker), terminals.py (detached terminal spawn via a settings-driven per-platform launcher table — ghostty/alacritty/kitty/wezterm/foot/gnome-terminal/konsole/xterm, Terminal.app+iTerm2 on macOS, wt on WSL2, or a custom '{argv}' template; the one-claude-per-container guard (SEC-3) in spawn_claude; + `spawn_path` opening the workspace mount in the system file manager via xdg-open/gio / `open` / wslview — the `b` Browse action), splash.py (PURE boot-splash frame generation — logo/gradient/sweep markup, no textual/rich imports, unit-tested), screens/ (splash — the boot animation screen: transparent-bg modal whose fill scrolls up to reveal the UI, any key skips, off via `config splash off`; create, add_repo, remove_repo, env_mounts, add_mount, add_port, ports, update_confirm, settings, terminal_select, git_identity, gh_token, add_key, menu, pull_confirm, delete_project, stop_all_confirm, shutdown, logs, sync_review)
images/                base/Dockerfile (native ~/.local claude install + baked neovim) + overlays/{python,rust,node}.Dockerfile
images/nvim/           curated, no-plugin-manager neovim config baked into the base image (init.lua + after/plugin/curated.lua): TS + Markdown + git-from-nvim. Plugins are native packages (pack/curated/start), treesitter parsers compiled to /opt/nvim-parsers, LSP servers (ts_ls/marksman/jsonls) + prettier on PATH — all baked read-only; nvim writes only shada/state to the .cache tmpfs. No runtime network/Mason. git identity is the injected GIT_CONFIG_* (commits from fugitive/gitsigns carry the right author). Floor unchanged (invariant 2)
templates/             project.toml.example, profile.toml.example, claude-json-stub.json, squid.conf.j2
tests/                 dependency-free unittest suite (argv renderer, env-file scrub, denylist, registry, seed, usage, smoke verdict)
```

Runtime state lives **outside the repo** under `~/.config/claude-man` (definitions) and
`~/.local/state/claude-man` (workspaces, tokens, config dirs). The `.gitignore` hard-blocks
`*.credentials.json`, `secrets.toml`, `/state/`, `/profiles/` as a belt-and-braces guard.

## Conventions

- **Python ≥ 3.11**, managed by **`uv`** (no pip/poetry). Read TOML with stdlib `tomllib`; write
  with `tomlkit` to preserve operator comments.
- **Tests must stay dependency-free** (`python -m unittest`): stdlib + `tomlkit` (the TOML
  writer) only — no docker/network/textual. Keep `textual` imports inside `tui/` so the CLI and
  tests import without it installed.
- **Shelling out to docker/git/claude** is done via `subprocess` with explicit argv lists (never
  `shell=True`). The hardened argv is rendered by one pure function (`docker/runner.py::build_create_argv`)
  so it can be unit-tested without a daemon.
- Stubs for unimplemented phases raise `NotImplementedError("phase N: ...")` referencing
  [`ROADMAP.md`](ROADMAP.md) — keep them honest rather than silently no-op.

## Common commands

```bash
uv sync                       # install deps
uv run claudemanctl --help    # CLI
uv run claudeman              # TUI (projects table + per-profile token-usage panel)
uv run python -m unittest     # tests (no deps required)
uvx ruff@latest check src tests   # lint (ruff not in the synced env; run via uvx)
```

Key operator verbs (all under `claudemanctl`):

```bash
image build base; image smoke base                 # build + gate the hardened image
profile add <name> [--default|--sso|--email ...]   # mint a token via `claude setup-token` (with the user:profile usage scope)
profile renew <name>                               # re-mint a token (existing tokens must renew to gain the usage scope → activate the 5h/Week bars)
profile list | verify <name> | usage | seed <name> # accounts: status, account check, token usage, host-config capture
profile limits [name]                              # per-account 5h/weekly subscription bars + resets (GET /api/oauth/usage)
project create <slug> [--profile X] ; project up|stop|status <slug>
project stop-all                                   # end-of-day: stop + sync-out EVERY running container (best-effort batch); TUI: top-level `S`
project recreate <slug> [--profile X] [--force]    # rebuild / switch account (mismatch-guarded; also applies a changed git identity)
project shell|claude <slug>                         # auto-start the container if needed, then open a detached terminal into it
project assets <slug> [--bootstrap]                 # show the synced asset source dirs (CLAUDE.md + skills/agents); --bootstrap a stub CLAUDE.md
project sync <slug> [--in]                          # manually sync assets out (bind -> source); --in forces sync-in (source -> bind)
project env add <slug> ssh|file|env [...]           # add an env mount; `env <NAME>` prompts (hidden) for a value -> 0600 state, injected -e NAME (recreate to apply)
project env rm <slug> <ssh|dst|NAME> | env list     # remove (by ssh / file dst / env var name) or list a project's env mounts
project ports add <slug> <container|host:container> [--bind IP] [--proto tcp|udp]   # publish a service port (-p; container ≥1024; default bind 127.0.0.1 host-only; recreate to apply)
project ports rm <slug> <host[/proto]> | ports list # unpublish a port (by host port) or list a project's published ports
config show                                         # global settings: resolved git identity + terminal/opener + ssh keys/load status
config terminal [--program X | --custom '…' | --auto]   # terminal for shell/claude windows (built-in launcher table per platform, or an '{argv}' template; TUI: Settings -> e)
config opener [--command '…' | --auto]             # file-manager command for Browse (b)
config splash [on|off]                             # the TUI boot splash (any key skips it)
config git [--name ... --email ... | --clear]      # set/clear the injected git author identity (recreate to apply; --clear inherits the host git config)
config gh-token [--clear | --stdin]                # set/clear the GitHub token injected as GH_TOKEN (hidden prompt; 0600 state-tier; recreate to apply)
config image [--channel latest|stable] [--pin X | --no-pin] [--check on|off]   # claude release channel/pin + the on-start "newer claude?" check (default: latest, on)
project up <slug> [--update-yes | --no-update]     # start; on-start it checks for a newer claude and (prompt, default) rebuilds the image to it. --update-yes skips the prompt; --no-update skips the check
config ssh add|rm <path> | config ssh load         # ssh keys claude-man auto-loads into the host agent
```

## Commit & PR rules

Follow the workspace conventions: short, factual subject lines (what changed, not why); optional
one-paragraph body; **no** "Co-Authored-By" trailer, marketing language, emojis, or generated-by
footer unless explicitly asked. **Never commit, push, or open a PR unless explicitly asked.**
