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
2. **The hardened run profile is the floor, not a suggestion.** `--read-only`, `--cap-drop ALL`,
   `--security-opt no-new-privileges`, `--user 1000:1000`, `--pids-limit 1024`, with writable
   surfaces limited to: the persistent `claude-config` bind (`/home/agent/.claude`), the persistent
   `workspace` bind (`/workspace`), two `tmpfs` mounts (`/tmp`, `/home/agent/.cache`, both `exec`),
   and — **only when a project has an `ssh` env-mount** — a `0700` `/home/agent/.ssh` tmpfs (for
   `known_hosts`/`config`; keys never enter — the host agent socket is forwarded). The
   `/home/agent/.cache` tmpfs **must be pinned agent-owned** (`uid=1000,gid=1000,mode=0700` in
   `_HARDENING`): a bare tmpfs defaults to `root:root` mode 755 and the agent (uid 1000) can't write
   it, so node/corepack (`mkdir ~/.cache/node`), claude's `XDG_STATE_HOME` (`~/.cache/state`), and the
   git/gh config redirects (below) all fail `EACCES`. Pinning the owner is *not* a floor relaxation —
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
   races on `.claude.json`/session writes. The spawn paths *should* enforce a single claude session
   per project, but that guard is **not yet implemented** (REVIEW SEC-3) — until it lands, don't add
   code paths that launch a second `claude` in a live container, and avoid doing so manually.

## Project layout

```
src/claudeman/
  config.py            XDG paths + all shared constants (label prefix, container/image names, baked container paths)
  cli.py               claudemanctl argparse surface (profile / project / sync / image verbs)
  lifecycle.py         create / up / stop / recreate / delete orchestration shared by the CLI + TUI (+ account-mismatch guard, workspace-ownership pre-flight, env-mount add/remove/resync + ssh seed, sync-checked delete_plan/delete_project teardown, asset sync-in on up / sync-out on stop)
  assets.py            per-project asset sync (host-side copy of CLAUDE.md + skills/agents between the synced config-tier source ~/.config/claude-man/assets/<slug>/ and the /workspace + ~/.claude binds): sync_in on start (asset wins), sync_out on stop (bind wins), backup-then-overwrite; claude side is a default-DENY allowlist (skills/agents/commands only) with a per-entry filtered recursive copy that drops denylisted-named nested entries + refuses escaping / denylist-targeting symlinks; workspace side is containment-checked; bootstraps a stub CLAUDE.md — distinct from the Phase-5 review-gated sync-back
  usage.py             per-profile token-usage parsed from project transcripts (read-only, separate from sync-back)
  usage_api.py         per-account subscription usage (5-hour + weekly bars) via GET /api/oauth/usage with a profile's OAuth token — no-redirect opener (no cross-host token leak); pure parse/render split from the network fetch
  gitconfig.py         resolve the git author identity (config.toml [git] override, else inherited host git config) → GIT_CONFIG_* env injected at docker create (no writable file needed under --read-only)
  gh_token.py          optional GitHub token (state-tier 0600, NOT config.toml) injected pass-through as GH_TOKEN for in-container `gh` — opt-in via `config gh-token` (invariant 1)
  __main__.py          `python -m claudeman` -> TUI;  argv dispatch
  registry/            projects.py, profiles.py (load/save/default/load_token/token_age), settings.py (global config.toml: ssh keys + git identity), schema.py  — TOML store
  docker/              labels.py, runner.py (hardened `docker create` argv + env_file scrub + additive env-mount render + exec-stdin ssh seed + git_env identity + baked GIT_CONFIG_GLOBAL/GH_CONFIG_DIR redirects), status.py (live ps JOIN), images.py (build/exists + base→overlay auto-build chain), smoke.py (hardened-profile image gate)
  profiles/            setup_token.py (mint/renew/verify via `claude setup-token`+`auth status`), identity.py (scrubbed stub), seed.py (claude-config seeding + host ~/.claude capture)
  checkout/            repos.py (host-side clone/fetch into workspace/ + cred-mask + dir containment; host PAT never enters the container), gitstate.py (porcelain-v2 parser → per-repo live state: branch/dirty/ahead-behind/drift)
  network/             allowlist.py (base egress set), squid.py (strict-egress sidecar generator — Phase 4 stub)
  syncback/            denylist.py, artifacts.py, diff.py (impl); baseline.py, detect.py, merge.py — Phase 5 stubs of the review-gated 3-way merge
  tui/                 app.py (projects JOIN + live Repos column / repo-detail panel via an 8s gitstate worker + per-profile usage panel — token totals plus 5h/Week subscription bars from a 60s refresh_utilization worker), terminals.py (detached ghostty/alacritty spawn), screens/ (create, add_repo, remove_repo, env_mounts, add_mount, settings, git_identity, gh_token, add_key, menu, pull_confirm, delete_project, quit_confirm, logs, sync_review)
images/                base/Dockerfile (native ~/.local claude install) + overlays/{python,rust,node}.Dockerfile
templates/             project.toml.example, profile.toml.example, claude-json-stub.json, squid.conf.j2
tests/                 dependency-free unittest suite (argv renderer, env-file scrub, denylist, registry, seed, usage, smoke verdict)
```

Runtime state lives **outside the repo** under `~/.config/claude-man` (definitions) and
`~/.local/state/claude-man` (workspaces, tokens, config dirs). The `.gitignore` hard-blocks
`*.credentials.json`, `secrets.toml`, `/state/`, `/profiles/` as a belt-and-braces guard.

## Conventions

- **Python ≥ 3.11**, managed by **`uv`** (no pip/poetry). Read TOML with stdlib `tomllib`; write
  with `tomlkit` to preserve operator comments.
- **Tests must stay dependency-free** (`python -m unittest`): pure-stdlib, no docker/network/textual
  needed. Keep `textual` imports inside `tui/` so the CLI and tests import without it installed.
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
project recreate <slug> [--profile X] [--force]    # rebuild / switch account (mismatch-guarded; also applies a changed git identity)
project shell|claude <slug>                         # auto-start the container if needed, then open a detached terminal into it
project assets <slug> [--bootstrap]                 # show the synced asset source dirs (CLAUDE.md + skills/agents); --bootstrap a stub CLAUDE.md
project sync <slug> [--in]                          # manually sync assets out (bind -> source); --in forces sync-in (source -> bind)
config show                                         # global settings: resolved git identity + ssh keys/load status
config git [--name ... --email ... | --clear]      # set/clear the injected git author identity (recreate to apply; --clear inherits the host git config)
config gh-token [--clear | --stdin]                # set/clear the GitHub token injected as GH_TOKEN (hidden prompt; 0600 state-tier; recreate to apply)
config ssh add|rm <path> | config ssh load         # ssh keys claude-man auto-loads into the host agent
```

## Commit & PR rules

Follow the workspace conventions: short, factual subject lines (what changed, not why); optional
one-paragraph body; **no** "Co-Authored-By" trailer, marketing language, emojis, or generated-by
footer unless explicitly asked. **Never commit, push, or open a PR unless explicitly asked.**
