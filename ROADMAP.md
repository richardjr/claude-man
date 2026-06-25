# Roadmap

Phased so an early phase yields a runnable skeleton and each later phase adds one load-bearing
subsystem. Each phase lists its goal and concrete deliverables. Checkboxes track scaffold status.

> **2026-06-02 scaffold review:** see [`docs/REVIEW.md`](docs/REVIEW.md) for the 38 verified
> findings. The one critical defect (the baked `claude` was a dangling symlink, unrunnable under
> the hardened profile) plus the `--env-file` credential-scrub gap are addressed in the new
> **Phase 0.5**. Lower-severity findings are folded into the phase that owns them (tagged inline).

## Current status — 2026-06-14

**Done & verified:** Phase 0, **Phase 0.5** (hardened image runs `claude` under `--read-only
--user`; `image smoke` gate; native `~/.local` install so `claude doctor` is clean; `env_file`
scrubbed), **Phase 1** (create / up / stop / shell / claude / status — a project goes from TOML to
a running hardened container), **Phase 2** (accounts: `profile add`/`renew`/`verify`/`seed`/
`usage`, per-project profile, `project recreate --profile` with an email-mismatch guard, per-profile
token-usage in the CLI + TUI), and **Phase 3 (repos — CLI + TUI)** — `project repo add`/`rm`/`list`,
`sync-repos`, and live per-repo git state (branch, clean/dirty, ahead/behind, branch-vs-config drift)
via the new `checkout/gitstate.py` porcelain-v2 parser; add clones live into the running container
(no recreate), with dir-containment, credential-masking, a per-slug `flock`, and the BUG-5/BUG-6 fixes.
The **TUI** surfaces it as a live Repos column + a per-project repo-detail panel (30 s fetch-less gitstate
worker, `g` for a fetch-ful rescan) plus the Repos submenu (`g`) with `a` Add-repo / `x` Remove-repo modal screens. A
**workspace-ownership pre-flight** (`_ensure_workspace_owned`) stops Docker auto-creating `workspace/`
as root. **Env-mounts (CLI, ssh + files)** — `project env add ssh|file`/`rm`/`list` + `project resync`:
read-only file binds at arbitrary container paths + ssh **agent-forwarding** (keys stay on the host) +
a `0700` `~/.ssh` tmpfs seeded via `exec`-stdin; additive render (floor byte-identical, unit-pinned);
a hardened **dest-denylist** (blocks the `.credentials.json`-injection attack incl. its leading-`//`
bypass, plus the ssh tmpfs, the hardened tmpfs mountpoints, and the baked claude launcher) — but
`/workspace/<path>` IS allowed (a workspace-root `CLAUDE.md`; nested mountpoint pre-created
operator-owned) with a `cp`-style trailing-slash dst; src must be absolute (else docker makes a named
volume); `openssh-client` added to the base image. The **TUI** adds an `e` env-mounts manager screen
(list / add / remove / resync). A **`workdir`** option (`Project.launch_workdir`) makes `claude`/shell
open via `docker exec -w` in an explicit `[project] workdir`, else `/workspace` — always, since
Phase 6a dropped the lone-repo auto-cd. **Per-account
subscription usage bars** — `usage_api.py` reads `GET /api/oauth/usage` (5-hour + weekly utilization
%, account-wide) host-side per profile; surfaced as coloured mini-bars in the TUI usage panel (`5h` +
`Week` columns, `u` refreshes) + `profile limits` CLI (see Phase 2). **In-container git identity + gh**
— `gitconfig.py` injects the operator's git author identity via `GIT_CONFIG_*` env (no writable file
needed under `--read-only`); the base image ships pinned `gh 2.93.0`; git/gh config redirected onto the
writable `.cache` tmpfs (see Phases 0.5 + 1). **On-start claude-version update** — `updates.py` reads
the tracked channel's latest version (token-less GET of `downloads.claude.ai/claude-code-releases/
<channel>`, the same endpoint the native installer uses) and, before `up`, compares it to the image's
baked `claude-man.claude-version` label; when a newer claude exists it offers (default: **prompt**) a
host-side image rebuild + container recreate — `claude update` can't run in the read-only container, so
the binary is bumped by rebuilding the image (invariant 2 byte-identical). Channel/pin/toggle via
`config image` (default `latest`, check on) + a per-project `claude_version` pin; the check fails OPEN
(offline -> start on the existing image). **Published ports** — a dedicated `[[project.ports]]` config
(`schema.PortMapping`) + `project ports add/rm/list` CLI + a TUI manager (Project menu -> Ports), so a
service running INSIDE a container (dev server / test endpoint) is reachable. Rendered additively as
`-p <bind>:<host>:<container>/<proto>` (`runner._render_ports` — never a `_HARDENING` flag, floor
byte-identical, unit-pinned); container port enforced ≥1024 (`--cap-drop ALL` drops NET_BIND_SERVICE),
default bind 127.0.0.1 (host-only) with per-port `0.0.0.0` opt-in. Ingress — orthogonal to the egress
firewall (invariant 3); fixed at create (recreate to apply). **Baked neovim** — the base image ships
neovim 0.11 with a curated, no-plugin-manager config (`images/nvim/`) for TypeScript + Markdown +
git-from-nvim: plugins are native packages, treesitter parsers compiled to `/opt/nvim-parsers`, and
LSP servers (`ts_ls`/`marksman`/`jsonls`) + prettier baked on PATH — all read-only, no runtime
network/Mason; nvim writes only shada/state to the `.cache` tmpfs, so the hardened floor is unchanged.
Commits from fugitive/gitsigns carry the injected git identity. 596 dependency-free
tests + headless-pilot + real-daemon smokes; ruff clean; `image smoke base` green (incl. new
`.cache`/`gh`/git + nvim probes).

**You can today:** mint work/home profiles, create projects on either account, start/stop/shell/run
claude in hardened containers, switch a project's account, watch per-account token usage **and live
5-hour/weekly subscription-limit bars**, add / remove / inspect a project's checked-out repos with live
git state, mount ssh (agent-forward) + host files into a container (`project env` + `project resync`),
and **`git commit` / `gh` inside a hardened container** with the operator's inherited git identity, and
**keep claude up to date** — on start, claude-man checks the tracked channel and offers to rebuild a
project's image to the newer claude (host-side; `claude update` can't run in the read-only container),
and **publish container service ports** (`project ports` / Project menu -> Ports) so a dev server or
test endpoint inside a container is reachable on the host, and **edit/commit in a baked neovim**
(TypeScript + Markdown LSP, treesitter, git-from-nvim) — all from both the CLI and the TUI.

**Done since:** **Phase 4 (strict egress): LANDED** (squid allowlist sidecar on a no-route
`--internal` net + lock/unlock + the TUI Egress screen + Network panel). **Phase 5 (sync-back):
LANDED 2026-06-13, committed 2026-06-14** (review-gated three-way merge into the host `~/.claude` —
`sync plan`/`sync review` + the TUI gate). **Phase 6 (curated packs — [`docs/PACKS.md`](docs/PACKS.md)):
6a LANDED 2026-06-11, 6b LANDED 2026-06-12** (library + schema + materializer + CLI + the /workspace
launch default, then the TUI Packs… checklist screen + create-modal Language field). **TUI-5 (live
container-log streaming): LANDED 2026-06-14** (a `docker logs -f --tail --timestamps` follower pane
— View… → Logs — reaped on dismiss). The one-claude-per-container guard (SEC-3) and CLI slug
validation (SEC-6) are DONE (2026-06-10).

**Next up:** **Phase 6c** (deeper curation — port the operator's existing skills into
`library/packs/`); the projects-table `docker events` push-refresh (the async off-UI-thread `docker
ps` worker (TUI-2) is DONE — only the event-driven trigger remains); and the deferred hardening
items — **BUG-2** (per-label `docker inspect`, latent today) and **IMG-4** (`COREPACK_HOME`/uv-cache
offline pinning + per-overlay offline smoke). Auto-capturing the token in `profile add` (skip the
paste) is the remaining small polish. **The larger architectural effort on deck is Phase 7
(multi-agent provider abstraction — run Codex/others in the same hardened model; designed in
[`docs/AGENTS.md`](docs/AGENTS.md), not started).** Tracked lower-severity items + the 2026-06-14
critical-review backlog live in [`docs/REVIEW.md`](docs/REVIEW.md).

**Operator note:** containers built before the native-install image change show stale `claude
doctor` warnings until `claudemanctl project recreate <slug>` rebuilds them on the current image.
The `openssh-client` base-image addition (for in-container ssh) likewise needs `image build base` +
a `project recreate <slug>` to take effect on existing projects. Activating this session's three
features on existing setups:

- **Usage bars:** existing tokens were minted with `user:inference` only and 403 on the usage
  endpoint (the bars read `re-mint`). Run `claudemanctl profile renew <name>` to re-mint with the
  `user:profile` scope and unlock the 5h/weekly bars.
- **`.cache` writability fix + git identity:** both are container-create options (tmpfs uid/gid +
  `GIT_CONFIG_*` env), so `claudemanctl project recreate <slug>` applies them with **no image
  rebuild**. Recreate is also required after changing the git identity (`config git …` / the TUI
  Settings screen).
- **`gh`:** the pinned `gh 2.93.0` is baked into the base image, so it needs an **image rebuild** —
  `claudemanctl image build base` then `image build node` (or the relevant overlay) + a
  `project recreate <slug>`. `gh auth` stays the operator's job (no token is injected).
- **neovim:** baked into the base image, so it needs an **image rebuild** (`image build base` +
  `image build <overlay>`) + a `project recreate <slug>`. It does NOT bump the claude version, so the
  on-start update check won't prompt for it — rebuild + recreate explicitly.

## Phase 0 — Repo scaffold + image
**Goal:** a buildable repo and a smoke-tested hardened image, no app logic yet.

- [x] `uv` project (`pyproject.toml`) with `textual` + `tomlkit`; `src/claudeman/` package skeleton; `CLAUDE.md` + `README` + docs
- [x] `config.py` XDG path resolution; constants for label prefix + image/container names + baked container paths
- [x] `docker/runner.py` renders the full hardened `docker create` argv (pure, unit-tested)
- [x] `docker/labels.py` label model; `syncback/denylist.py` (unit-tested)
- [x] `images/base/Dockerfile` (debian-slim + node + pinned native claude + non-root uid1000 `/etc/passwd` entry + baked env) and `images/overlays/{python,rust,node,python-node}.Dockerfile` (`python-node` = polyglot combo for node projects that also need python/pip)
- [x] `claudemanctl image build` (base + overlays) renders + runs `docker build`
- [~] `image smoke` — moved to Phase 0.5 (the bake bug below made the original build untrustworthy)

## Phase 0.5 — Unblock: make the hardened image actually run claude (review fallout)
**Goal:** prove a fully hardened container can run `claude` as uid 1000, and close the credential
hole, before any project-create wiring is trusted. (See [`docs/REVIEW.md`](docs/REVIEW.md): IMG-1,
SEC-2, IMG-2/5, IMG-3.)

- [x] **IMG-1 (critical):** base Dockerfile installs claude **natively into the agent's `~/.local`**
  (agent-owned, uid-1000-reachable under `--read-only --user`); `claude --version` verified as the
  agent user at build time. The native path also keeps `claude doctor` clean — `installMethod:
  native` is honest and there's no `/usr/local/bin/claude` "leftover npm" warning
- [x] **IMG-2:** `XDG_STATE_HOME` baked onto the writable `.cache` tmpfs; **IMG-5:** pin → 2.1.160
- [x] **IMG-3:** `image smoke` reuses `build_create_argv`'s hardened argv — asserts (as uid 1000)
  `claude --version`, getpwuid, `rg`→`/usr/bin/rg`, writable `.claude` bind; one-shot `claude -p`
  when a token exists (passes against the default profile)
- [x] **SEC-2 (high):** `env_file` scrubbed host-side (pass-through, values out of argv) + tests
- [x] **`.cache` tmpfs writability (hardened-profile bug):** a bare `/home/agent/.cache` tmpfs
  defaulted to `root:root` mode 755, so the agent (uid 1000) couldn't write it —
  `node`/`corepack` (`mkdir ~/.cache/node`) and claude's `XDG_STATE_HOME=~/.cache/state` failed
  `EACCES` (`/tmp` was fine — Docker special-cases it to sticky `1777`). `runner._HARDENING` now
  pins the `.cache` tmpfs `uid=1000,gid=1000,mode=0700` (keeping `nosuid,exec,size`). NOT a floor
  relaxation — the writable surface is now actually agent-writable, as invariant 2 intends.
  `image smoke` adds a writable-`.cache` probe; `test_docker_argv` pins `uid=1000`. Applies on
  `recreate` (no image rebuild).
- [ ] **IMG-4 (deferred to Phase 4 prep):** overlay toolchain caching (`COREPACK_HOME`, uv cache)

## Phase 1 — Runnable skeleton (one project, default profile)
**Goal:** the TUI lists and creates a single hardened container under one default profile and opens a shell + claude in it.

- [x] `registry/projects.py` read/write/save a single `projects/<slug>.toml`; one-repo checkout (BUG-1 env coercion done)
- [x] `docker/runner.py` create/start/stop + `docker/status.py` live JOIN, wired into the CLI/TUI via `lifecycle.py` (WIRE-1/WIRE-7); `claude-config` seeded first via `profiles/seed.py` (WIRE-3)
- [x] `tui/app.py`: live JOIN + DEFINED→create+start with surfaced errors (TUI-1), `enter`→shell (TUI-3), cursor-by-slug (TUI-7), async off-UI-thread `docker ps` worker (TUI-2 — `@work(thread=True, exclusive)`), single post-action refresh (TUI-4). The only remaining bit is the `docker events` push-refresh (tracked under Phase 2)
- [x] `tui/screens/create.py`: `n`→new-project modal (slug/profile/overlay/egress; inline slug validation + duplicate guard; runs `lifecycle.create_project` in a thread worker that converts every failure to a red `Result`). **Repos/env/allowlist capture deferred** — `create_project` has no `repos` param yet (see Phase 3)
- [x] `tui/terminals.py`: detached terminal spawn — settings-driven launcher table (ghostty/alacritty/kitty/wezterm/foot/gnome-terminal/konsole/xterm + macOS Terminal.app/iTerm2 + WSL2 wt, or a custom template) (**SEC-6** done: slug validated at the CLI argparse boundary *and* in `_inner_exec` before the keep-open shell string)
- [x] **TUI-5** logs pane streaming `docker logs -f` — `screens/logs.py` `LogsScreen` (View… → Logs): an `@work(thread=True, exclusive, group="logs")` follower over the pure `runner.build_logs_argv` (`--tail 200 --timestamps -f`), lines pushed via `self.app.call_from_thread`, the subprocess reaped (`terminate`→`kill`) in `on_unmount` so no follower leaks
- [x] Auth: `profiles.load_token(name)` injects a `0600` token file as `CLAUDE_CODE_OAUTH_TOKEN`; minted by `profile add` (Phase 2); `ANTHROPIC_*` scrubbed incl. `env_file` (0.5)
- [x] **SEC-3:** "one claude per container" enforced in `terminals.spawn_claude` (a /proc comm probe via `docker exec`; fails open on probe errors; both the CLI and TUI spawn paths go through it)
- [x] **In-container git identity + GitHub CLI (under `--read-only` rootfs):** `git commit` failed
  (*Author identity unknown*) and `git config --global` hit EROFS on the read-only rootfs. New
  `gitconfig.py` injects the author identity via git ENV-config (`GIT_CONFIG_COUNT` +
  `GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n`, equivalent to `git -c user.name=…`) — no writable file
  needed; precedence is the claude-man `[git]` setting (`config.toml`) else the host operator's
  `git config --global user.{name,email}`. Identity is non-secret → plain `-e KEY=value`, injected at
  `docker create` via `build_create_argv(git_env=…)` ← `lifecycle.ensure_created` ←
  `gitconfig.container_env()`. `GIT_CONFIG_GLOBAL=/home/agent/.cache/gitconfig` +
  `GH_CONFIG_DIR=/home/agent/.cache/gh` (baked in `runner._BAKED_ENV` + the Dockerfile) redirect
  git/gh config onto the writable `.cache` tmpfs. The base image installs pinned `gh 2.93.0`
  (`config.DEFAULT_GH_VERSION` + `ARG GH_VERSION`, arch-aware upstream `.deb` — `gh` isn't in Debian
  repos); by default no `GH_TOKEN` is injected (`gh auth login` in-container writes the writable
  `GH_CONFIG_DIR`), with an opt-in managed token via `config gh-token` (0600 state-tier, injected
  pass-through as `-e GH_TOKEN` when set — `gh_token.py`; never in `config.toml`, invariant 1).
  Settings: `Settings.git_user_name/git_user_email` + `[git]` in
  `config.toml`; TUI Settings screen (`,`) shows the resolved identity, `g` opens a `GitIdentityScreen`
  edit modal (blank = inherit host); CLI `config git [--name … --email … | --clear]` (`config show`
  also prints the resolved identity). `image smoke` adds `gh` present + writable git/gh-config probes.
  Identity change needs a `recreate`; `gh` needs an image rebuild (`image build base`, then the overlay)
  + `recreate`.

## Phase 2 — Profiles (work/home) + per-project default
**Goal:** multiple account profiles with a per-project default and safe switching.

_Review notes (docs/REVIEW.md): **SYNC-2** — when seeding `claude-config/`, field-patch
`settings.json` to strip host `hooks`/`statusLine` (else the host `SessionEnd → sync-claude.sh`
hook + bun statusLine co-fire in-container) and trim `plugins/` (exclude cache/data/blocklist.json);
the profile column upgrade is also where the **TUI-2** docker-events worker lands._

- [x] `profiles/setup_token.py` wrapping `claude setup-token` (+ `--sso`/`--login`/`--console`/`--email`); `0600` token store; mint time = token file mtime (`profiles.token_age_days`). **Bonus:** `profile verify` (token validity + recorded account; OAuth tokens don't expose the email live, so identity is the mint-time record)
- [x] `profiles/identity.py` scrubbed `.claude.json` onboarding stub; `profiles.save` (single-default); `profile.toml` schema + default resolution
- [x] `claude-config/` seeding from the profile `seed/` through the denylist; `profile seed` captures host `~/.claude` (settings.json field-patched per SYNC-2, cruft excluded); create/`recreate` use the effective profile
- [x] profile column + **per-profile token-usage panel + token age in the TUI** (`u` to refresh; worker-scanned off the UI thread) + `profile usage` CLI; switch-time email-mismatch guard (`recreate --force`)
- [x] **Per-account subscription usage bars (5-hour + weekly):** `usage_api.py` reads
  `GET https://api.anthropic.com/api/oauth/usage` (`config.OAUTH_USAGE_URL`, beta
  `oauth-2025-04-20`) with a profile's OAuth bearer → `five_hour` + `seven_day` utilization % (0–100)
  + reset times (per-model `seven_day_opus/sonnet` parsed, not yet surfaced). These are **account-wide
  subscription limits** (all usage on the account, not just claude-man containers — the panel title
  says so), distinct from the transcript token-totals. Pure parse/render
  (`parse_utilization`/`render_bar`/`level`) split from the network fetch. **Security:** a no-redirect
  urllib opener (`_NoRedirect`) so a 30x never re-sends the `Authorization` bearer cross-host
  (credential-leak fix, invariant 1); `User-Agent` pinned to `claude-code/<ver>`
  (`config.CLAUDE_CODE_USER_AGENT`) to avoid hard rate-limiting; read-only, does NOT consume quota.
  **Token scope:** `setup_token.py` now mints with `CLAUDE_CODE_OAUTH_SCOPES="user:profile
  user:inference"` (`config.OAUTH_USAGE_SCOPES`) so the SAME token runs inference AND reads usage;
  existing `user:inference`-only tokens 403 (bars read `re-mint`) until `profile renew <name>`.
  **TUI:** the usage panel gains `5h` + `Week` coloured mini-bars (green <70% / yellow <90% / red),
  fed by a 60 s off-thread `refresh_utilization` worker cached in `self._util` (`u` refreshes both).
  **CLI:** `profile limits [name]` prints per-account 5h/weekly bars + reset.
- [~] docker-events-driven refresh — the async off-UI-thread worker (TUI-2) is **DONE** (projects/usage/util/gitstate/net panels all run off-thread); what remains is replacing the 10 s poll with a `docker events` push trigger

## Phase 3 — Persistent multi-repo checkouts + full lifecycle
**Goal:** projects own a set of repos, persist across restarts, and tear down cleanly.

_Review notes: **BUG-2** read labels via `docker inspect --format '{{json .Config.Labels}}'`
(not CSV-splitting `docker ps`); **BUG-5** `status.join` prefers registry values + a drift marker
on label divergence (invariant 4); **BUG-6** concise `fetch_all` detail instead of raw git fatal;
**TUI-6** gate actions on orphan rows (container with no registry entry)._

- [x] `checkout/repos.py`: host-side clone of every `[[repos]]` entry into `workspace/`; `project sync-repos` (clone-missing + fetch); **`checkout/gitstate.py`** porcelain-v2 parser → live per-repo state (branch, dirty, ahead/behind, branch-vs-config drift); `project repo add`/`rm`/`list`; registry mutators with dir-containment + cred-mask + per-slug `flock`; **BUG-5** (registry-wins repo count + drift marker) and **BUG-6** (concise `fetch_all`) landed. **TUI:** live Repos column + repo-detail panel (30 s fetch-less gitstate worker, `g` fetch-ful) + Repos-menu (`g`) `a`/`x` Add/Remove-repo modal screens.
- [x] Idempotent `project delete` (`rm -f` container + `rm -rf` state dir + `rm` toml, registry removed LAST so a partial failure stays retry-able); start/stop/recreate verbs in TUI + ctl. **Sync-gated:** `lifecycle.delete_plan` scans each repo (fetch-less) via `gitstate.delete_risk` and the TUI `DeleteProjectScreen` / CLI surface the per-repo unsynced-work assessment before the irreversible delete — risky repos require an explicit "Delete anyway" (TUI) / `--force` (ctl), and `--keep-workspace` / the "keep workspace" toggle preserves the `/workspace` checkout as a non-destructive exit.
- [x] Version-bump-by-recreate flow (`check_update` → `up`/`recreate(rebuild_to=…)`, `--update-yes`/`--no-update`) + running-version status column (stamped from the image's baked `claude-man.claude-version` label); `backups/` convention (`config.backups_dir`, used by asset-sync, sync-back merge, and delete teardown)
- [~] DEFINED/STOPPED/UP JOIN hardened — **DONE** (full outer join: DEFINED-with-no-container rows, registry-wins repo count + drift marker (BUG-5), orphan-container note (TUI-6)); the one remaining bit is **BUG-2** — a second `docker inspect --format '{{json .Config.Labels}}'` per-label read for robust multi-label parsing (still latent — current label values are comma-free)
- [x] **Per-project asset sync** (`assets.py`): a synced config-tier source `~/.config/claude-man/assets/<slug>/{workspace,claude}/` mirrors CLAUDE.md (→ `/workspace`) + skills/agents/commands (→ `~/.claude`, USER scope) to/from the host binds. `sync_in` on `up` (asset wins, runs after the profile seed so per-project assets layer on top, before `runner.start`); `sync_out` on `stop` (bind wins); **back up the target before every overwrite** (`backups/<ts>/{in,out}/`) and refuse on backup failure — last-write-wins, nothing lost. The claude side is a **default-deny allowlist** (only `skills`/`agents`/`commands` — a blocklist can't safely enumerate sensitive paths like `projects/` transcripts) with a filtered recursive copy that drops denylisted-named nested entries and refuses symlinks that escape the source or target a denylisted path; the workspace side is containment-checked (`is_within`). Bootstraps a stub `CLAUDE.md` if none exists. **TUI** `q` quits immediately and leaves containers running (it never stops/syncs — that previously blocked the close behind a serial, unabortable per-container `docker stop`); the end-of-day "stop + sync all running containers" is the separate top-level `S` (stop-all) command (`StopAllConfirmScreen` → `ShutdownScreen` progress modal → off-thread stop-all worker, offering stop-sync-&-quit or stop-sync-&-stay; `_busy`/`_stopping_all` guarded). **ctl:** `project stop-all` (batch stop+sync via `lifecycle.stop_all`), `project assets [--bootstrap]`, `project sync [--in]`. `[project.sync]` TOML block (enabled/workspace/claude). Distinct from the Phase-5 review-gated sync-back (this targets an isolated per-project dir, so it's safe to run automatically). Migration: drop a project's `~/Work/CLAUDE.md` `file` env-mount (it shadows the synced file) and recreate.

## Phase 4 — Lockable strict egress
**Goal:** per-project opt-in strict egress allowlist that keeps `--cap-drop ALL` intact.

_Review notes: **IMG-4** before strict egress can work for the node/python overlays, pin their
toolchain caches to read-only system paths (`COREPACK_HOME`, uv cache) so first run needs no
network, and add the yarn download hosts (`registry.yarnpkg.com`, `repo.yarnpkg.com`) to the
base allowlist; verify offline in `image smoke <overlay>`._

- [x] `network/allowlist.py` base set (incl. `claude.ai` for OAuth refresh + GitHub + npm + PyPI + yarn + Debian apt) + project extras
- [x] `network/squid.py` (pure squid.conf renderer) + `network/egress.py` orchestration: per-project `--internal` agent net + a `claude-man:proxy` squid sidecar (also on the bridge for egress); agent gets `HTTP(S)_PROXY` → the sidecar (additive flags in `runner._render_egress`; hardened floor byte-identical)
- [x] `project lock`/`unlock` verbs (`lifecycle.set_egress`, recreate-to-apply); `project egress-log` surfaces denied requests for allowlist tuning, and the TUI's always-on **Network panel** shows per-project blocked/allowed counts (via the pure `egress.parse_access`/`summarize_access` parsers over the sidecar's access log) + Traffic (whole-container NetIO from `docker/stats.py`'s `container_net_io`, i.e. `docker stats`)
- [x] TUI **Egress screen** (Project… → `g`): lock/unlock toggle (off-thread `set_egress`) + allowlist extras add/remove (`lifecycle.add_allow`/`remove_allow`, `is_valid_dstdomain`-validated, registry-only) + promote-a-blocked-host picker over `summarize_access` — the allowlist-tuning loop without hand-editing TOML
- [x] Smoke: `image smoke proxy` builds the sidecar; `project egress-smoke <slug>` checks an allowlisted host reaches + a non-allowlisted host is blocked (daemon-gated, like `image smoke`)

_Implementation notes (2026-06-10): orchestrated with explicit `docker network`/`docker run` argv
(NOT compose) so the hardened agent stays on the single unit-tested `build_create_argv` renderer
(invariant 2). One per-project `--internal` net + the sidecar bridged for egress (rather than two
nets). **Proxy-only** for now — every proxy-aware tool (claude, git-https, npm/pip/apt) is covered;
the `dnsmasq` direct-DNS forwarder and an in-container `iptables` default-DROP layer remain deferred
defence-in-depth. IMG-4 yarn/apt hosts are in the base allowlist; offline-first overlay cache pinning
(`COREPACK_HOME`/uv) is still worth verifying in `image smoke <overlay>`._

## Phase 5 — Sync-back accept flow ✅
**Goal:** review-gated three-way merge of container config changes back to the operator's host
`~/.claude` (USER scope), audited in a state-tier git repo. **Done & verified** (2026-06-13).

_Landed review notes: **SYNC-5** `json_key_diff` drops `is_denied_json_key` keys before diffing (so
`oauthAccount`/`userID` never reach the diff buffer); **SYNC-3** synced skill symlinks are
containment-checked (escaping/denied-target symlinks refused); **BUG-3** the `*-cache.json` deny is
basename-anchored (tested nested); **BUG-4** `mask_line` has a value-shape secret scan (sk-/ghp_/JWT/
long-base64). **SYNC-1** project/repo-scope artifact producers remain deferred (USER scope only);
**MCP apply** is deferred — detected/diffed + gated, never `claude mcp` exec'd in v1._

- [x] `syncback/fsmerge.py` factors the shared FS primitives out of `assets.py` (one audited copy/backup/symlink-guard impl, zero behaviour change)
- [x] `syncback/artifacts.py` registry + `denylist.py` enforced before read; `baseline.py` 3-way manifest (sha256 / canonical-JSON / symlink-target; narrow `mcpServers`-only `.claude.json` read)
- [x] `detect.py` classify (per-file/key/server; conflict + no-op subtraction + claude-man-own-write subtraction; no-baseline implicit reference); `diff.py` difflib + canonical key-diff + secret-mask
- [x] `tui/screens/sync_review.py` accept/reject/skip/cycle/accept-non-reject gate with defaults (text accept; settings/MCP/deletions/conflict reject)
- [x] `merge.py`: **global** merge lock → backup-first → gated tree copy → inverse field-patch `settings.json` (hooks/statusLine immune) → MCP gate-only → mirror to `config.sync_audit_dir()/<slug>/` + git commit (denylist re-asserted at staging) → baseline refresh; CLI `sync plan` / `sync review [--yes]`

## Phase 6 — Curated packs (skills + CLAUDE.md injectors)
**Goal:** a curated, in-repo library of task-focused skills + CLAUDE.md fragments that projects
select as **packs** (bundles), materialized through the existing asset-sync rail so claude picks
them up automatically. Full design: [`docs/PACKS.md`](docs/PACKS.md) (agreed 2026-06-11).

_Key decisions: pack = a bundle (a dir of `claude-md/*.md` fragments + `skills/<name>/` that
travel together; selection at pack granularity). Library tiers = `common/` + per-language dirs
(discovered, not hardcoded); defaults = `default = true` packs in `common/` + `<language>/`,
resolved at CREATE and written explicitly into the project TOML (no silent creep; a `defaults`
verb re-applies on demand). `Project.language` is an EXPLICIT field — not inferred from the
overlay (create may pre-fill the suggestion). Fragments land at `/workspace/.claude-man/<pack>/`
and are LINKED from the main CLAUDE.md via a fenced block of `@` imports (operator content
outside the block untouched); skills ride the existing claude-side allowlist. Curated-wins drift
policy (re-stamp + backup + note); a state-tier manifest separates "ours to re-stamp" from
operator files (deselection/collision safety). All container delivery is `assets.sync_in` — no
new mounts, floor byte-identical (invariant 2)._

- [x] **6a (landed 2026-06-11):** `library/packs/<tier>/<pack>/` layout + `pack.toml` (description, `default`); `packs/library.py` (pure discovery/parse/hash + name-uniqueness lint); `packs/materialize.py` (asset-source writes + fenced CLAUDE.md block patch + manifest); `Project.packs`/`Project.language` schema + `projects.set_packs` + `lifecycle.set_packs` (immediate apply); lifecycle `up` hook before `sync_in` (fail-soft); CLI (`packs list`, `project packs add|rm|list|defaults`, `project create --language`); `launch_workdir` default → always `/workspace` (explicit `workdir` still wins; lone-repo auto-cd dropped). Starter library: guardrails/code-quality (default) + workflow (opt-in) + node/python/rust convention packs. 34 new tests (discovery lint, block-patch idempotence, ours/theirs manifest boundary, drift/collision/deselect) + a live CLI smoke
- [x] **6b (landed 2026-06-12):** TUI Packs… checklist screen (`tui/screens/packs.py`, opened Project… → `p`): grouped *Common* / *<language>* / *Other (selected)* rows from the pure `tui/packsview.py` row model (splash/rowfx no-textual pattern, unit-tested), space/enter toggle + `d` re-apply-defaults through `lifecycle.set_packs` (immediate apply), and a State column from the new `packsview.pack_states` (read-only freshness map: stale / drifted / operator-collision / not-in-library; the materializer stays the only writer). Create modal gained a Language Select (tiers discovered from the library; the Overlay choice pre-fills the matching tier until the operator picks one) threaded `NewProject` → `create_project`. 31 new tests + a headless pilot smoke
- [ ] **6c:** deeper curation — port the operator's existing skills into `library/packs/` (e.g. a common `review-skills` pack; the fragment starter library — guardrails / code-quality / workflow + node/python/rust conventions — shipped in 6a); launch smoke (claude reports the imported memory via `/memory`). Templates are PUBLIC (repo is public) — house rules in, client/project-specific content stays in per-project assets

## Phase 7 — Multi-agent provider abstraction
**Goal:** a seam that lets claude-man run a *different* coding agent (e.g. the OpenAI Codex CLI)
inside the same hardened-container model, without forking the project or weakening any security
invariant. Full design: [`docs/AGENTS.md`](docs/AGENTS.md) (planned 2026-06-14).

_Key decisions: the security floor is ALREADY agent-agnostic (the hardened argv, egress sidecar,
labels, ports, env-mount/secret passthrough know nothing about the binary inside); the Claude
coupling clusters in 8 seams (auth, image-bake, process-spawn, updates, usage, paths, sync-back,
packs). Localize them in an `agents/` package — one `AgentProvider` value object resolved through one
module (the `hostplatform.py` "all branches go through here" pattern), selected per project via a new
`Project.agent` field (default `claude`). A provider parameterizes BEHAVIOUR, never SECURITY:
invariants 1–3 are enforced BY the layer for every provider, not exposed as per-provider knobs (no
`.credentials.json` copy, no `ANTHROPIC_*`/misbilling-key injection, floor byte-identical, locked =
no route out but the allowlist proxy — for any agent). The 3-way sync-back ENGINE, masking, backup,
flock, audit-commit, the update semver compare, and the usage render helpers are all reused; only the
provider's policy DATA varies._

- [ ] **7a:** introduce `agents/` + a `claude` provider reproducing today's behaviour byte-for-byte (pure refactor, zero behaviour change); route the soft seams through it (spawn binary/comm, config-dir path+env, version-label key+build-arg, release URL/UA, required egress hosts, context-file name + import syntax, sync-back policy data). A unit test pins the hardened argv byte-identical (invariant 2)
- [ ] **7b:** `Project.agent` field (default `claude`) threaded through lifecycle/runner/terminals/images; split `BASE_ALLOWLIST` into a neutral toolchain set + `provider.required_hosts`; key `schema._MANAGED_MOUNTS` on `provider.config_dir`; generalize the one-per-container comm probe
- [ ] **7c:** a `codex` provider + image overlay, validated against the hardened floor (`image smoke`); resolve the auth-kind divergence (single bearer vs refreshable JSON cred — needs research on Codex's auth/login flow; invariant 1 must still hold). `project create --agent codex`
- [ ] **7d:** Codex sync-back policy (adversarially reviewed, like the Claude denylist) + pack content (`AGENTS.md` vs `CLAUDE.md`, its own config taxonomy)

## Phase 8 — In-container dev environment (curated bash + nvim-on-start)
**Goal:** make the in-container `shell` and `nvim` feel like the operator's host Arch/Omarchy bash —
a git-aware prompt, the same history search, the `n` shortcut (which opens neovim with the file tree
via the directory-arg hijack — 8b folded into this), and a shell-open banner explaining the setup.
Full design: [`docs/DEVENV.md`](docs/DEVENV.md) (8a/8c/8d landed 2026-06-15; 8b dropped).

_Key decisions: everything is BAKED, curated, network-free, floor-preserving — the same model as the
already-baked `images/nvim/` config; the default path is invariant-2 byte-identical (read-only rc on
the rootfs, all writes to the existing `.cache` tmpfs). No spawn-path change: `terminals._inner_exec`
already execs a plain interactive `bash`, which sources a baked `~/.bashrc` (guarded with
`[[ $- != *i* ]] && return` so the non-interactive exec probes are untouched). The host `n` =
`nvim .`; the file tree shows because a DIRECTORY argument trips LazyVim's "start Neo-tree with
directory" autocmd — bare `nvim` (no args) shows the dashboard. The container's curated nvim
replicates both: a directory-arg hijack for `n`, plus a `VimEnter` side-panel `Neotree show` (reveal,
don't focus) for the no-arg/single-file cases, guarded against git-editor/diff/stdin. claude-launching
aliases are excluded (invariant 6). History is EPHEMERAL by default (`.cache` tmpfs, resets on
recreate); a default-OFF `config shell-history on` adds one owner-pinned writable bind for
persistence — the only, opt-in, documented invariant-2 relaxation. The operator's LOCAL nvim config
is left untouched._

- [x] **8a (landed 2026-06-15):** curated bash env baked into the base image — `images/bash/{bashrc,inputrc,starship.toml}` (the operator's `starship.toml` byte-for-byte; `inputrc` ↑/↓ history prefix-search + `$include /etc/inputrc`; rc with the `n` neovim fn, eza/git/zoxide aliases, fzf `Ctrl-R` via `fzf --bash`, starship prompt init). `starship/fzf/eza/zoxide/bat/bash-completion` from Trixie apt (`bat` symlinked over Debian's `batcat` — starship is packaged in Trixie, so no pinned-binary fetch). Shell env (EDITOR/STARSHIP_*/_ZO_DATA_DIR/MANPAGER/…) in the image ENV ONLY → inherited by `docker exec`, NO `runner.py` change, so the create-argv floor is byte-identical (invariant 2). NO claude/opencode alias (invariant 6); the rc bails on non-interactive shells FIRST so the comm/ssh/gitstate exec probes are untouched. Writable state (HISTFILE/zoxide db/starship cache) → `.cache` tmpfs (ephemeral; 8d adds opt-in persistence). 12 new static tests (`tests/test_bash_env.py`) + 4 new `image smoke base` probes (rc loads `n`, HISTFILE on tmpfs, starship runs, dev CLIs present) — **smoke GREEN on a real base rebuild; starship renders the git branch under `--read-only --user`**
- [~] **8b: DROPPED (2026-06-15) — covered by `n`.** The host `n` = `nvim .`, and a directory argument already opens the curated nvim's neo-tree as the main view (neo-tree's netrw hijack, same end state as LazyVim's "start Neo-tree with directory"). So the operator gets the tree-on-open via `n` with no extra autocmd; a separate `VimEnter` side-panel was deemed redundant. The `<leader>e` toggle (8a) remains. (Bare `nvim` still opens the mini.starter dashboard — deliberate.)
- [x] **8c (landed 2026-06-15):** shell-open banner — baked `images/bash/motd`, a standalone bash script on PATH (`/usr/local/bin/claude-man-motd`). Renders the boot-splash CLAUDE-MAN wordmark in the terracotta→ember gradient (per-row RGB byte-identical to `tui/splash.py::row_color`, an `ast`-based test pins the sync) + a cheat-sheet (`n`, `ls`/`lt`, `g`/`gcm`, `cd`, `Ctrl-R`/↑↓, `<leader>e`, `hints`), a DYNAMIC history line (ephemeral vs persistent from `CLAUDEMAN_HISTFILE`), and the starship git-status legend. The rc shows it on EVERY interactive shell on a real tty (the `-t 1` guard keeps non-tty exec probes silent), `hints` re-shows it, `CLAUDEMAN_NO_MOTD`/`NO_COLOR` honoured. Colour only on a tty
- [x] **8d (landed 2026-06-15):** history persistence — `[shell] persist_history = false` (default OFF) in the general tier (`registry/settings.py`, mirroring `ui_splash`) + `set_shell_history` + `config shell-history on|off` CLI (+ `config show` line). When ON, `lifecycle.ensure_created` makes a per-project state-tier dir (`config.project_shell_dir`, 0700 = uid 1000) and `runner._render_shell_history` binds it read-WRITE at `CONTAINER_SHELL_HISTORY_DIR` + injects `CLAUDEMAN_HISTFILE` (the baked rc points `$HISTFILE` there; else ephemeral on the `.cache` tmpfs). The ONE opt-in writable surface beyond the floor — default OFF keeps the create-argv byte-identical (a floor test pins both paths). Recreate to apply (the bind is fixed at create)

## Phase 9 — Hybrid local models (claude.ai subscription + a local backend in one session)
**Goal:** let a project's in-container `claude` use BOTH the claude.ai subscription AND a local/
self-hosted model, both shown in the `/model` picker and switchable **mid-session**, via Claude Code's
gateway surface. **Opt-in per project** (default stays subscription-direct). Full design: `docs/MODELS.md`
(planned) + issue #14. **A different axis from Phase 7** (which runs a different *binary* like
Codex): here the SAME `claude` binary points at a different *model backend*.

_Key decisions: Claude Code reads `ANTHROPIC_BASE_URL` ONCE at launch and `/model` only flips the model
NAME at a fixed endpoint, so "both models, one picker, mid-session" requires routing the session through
ONE per-project **gateway sidecar** built like the squid egress sidecar (`network/egress.py` —
additive `--network`/`-e`, floor byte-identical, invariant 2). CORRECTED finding (official
`code.claude.com/docs/.../llm-gateway`): `ANTHROPIC_BASE_URL` alone does NOT replace the subscription —
only a credential var (`ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_API_KEY`) does — so a **passthrough** gateway
that forwards the claude.ai login (`Authorization` + the `anthropic-beta` OAuth capability) KEEPS the
Max/Pro subscription for Claude models while translating local-model calls (Anthropic `/v1/messages` ↔
OpenAI) to a HOST Ollama/vLLM. Chosen (operator decision) over the terminating-router flavor (console
pay-per-token + transparent auto-failover) to preserve the subscription — the cost is that failover to
local is a **manual `/model` switch, not automatic**. Invariant 1 (no mis-bill): the agent keeps the
hard `ANTHROPIC_*` scrub (no console key, no `ANTHROPIC_AUTH_TOKEN`), gets only `ANTHROPIC_BASE_URL` +
`CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY`, and STILL gets `CLAUDE_CODE_OAUTH_TOKEN` (the subscription
stays the active credential). POSTURE CHANGE to document: the OAuth token now transits claude-man's OWN
passthrough sidecar (never a third party — sidecar → `api.anthropic.com` directly), a scoped relaxation
of "the token only touches the `claude` binary," opt-in to hybrid mode only. Mode is a `Project` field
(`subscription` default | `hybrid`), recreate-to-apply (the base_url boundary IS a container boundary),
mirroring the overlay/profile switch; the active mode + billing is surfaced in the TUI so it is never
silent. The background/Haiku tier (`ANTHROPIC_DEFAULT_HAIKU_MODEL` — summaries, `--resume`/`/compact`,
the Explore subagent, titles) ALSO traverses the gateway and is set to MATCH the project's primary
backend (a small local model when primary is local → air-gap; a cheap Claude id when primary is Claude).
Local server on the HOST (`host.docker.internal`); a locked hybrid project is PARTIAL air-gap (the Claude
leg still needs `api.anthropic.com` — route the sidecar's Anthropic egress through the existing squid
allowlist; the local leg stays on-host). LiteLLM (pinned official Docker image — NEVER pip; PyPI
1.82.7/8 were malware) is the reference gateway. Tool-use fidelity through local translation is the
make-or-break NON-invariant risk — prefer vLLM/OpenAI-native over raw Ollama. Design the Project fields
provider-shaped so Phase 7 can later subsume this. A **dynamic model-management framework** sits under
the gateway: a provider-shaped `models/` seam (Ollama first — it IS a model package manager: pull/list/
rm/show + a registry; vLLM later) so the operator installs/updates local models from inside claude-man
(`claudemanctl model …` + TUI) rather than hand-running `ollama pull`. Qwen3-Coder is the reference
first model. "Keep up to date" = re-pull the tag + digest compare (Ollama has no auto-update)._

- [ ] **9a (spike):** validate ONE per-project gateway sidecar that PASSES THROUGH the claude.ai login for `claude-*`/`anthropic-*` ids (forwarding `Authorization` + `anthropic-beta`, subscription billing intact) AND translates `local-*` ids to a host Ollama/vLLM, exposing both via `/v1/models` for `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY`. Confirm a mid-session `/model` switch works and the latest Claude ids appear with no config edit (wildcard route). Pinned LiteLLM image, else a thin custom front if one daemon can't cleanly do passthrough-Claude + translate-local + unified discovery
- [ ] **9-models (dynamic model framework):** an Ollama-backed model-management layer — `claudemanctl model list/add/update/rm` (+ TUI) wrapping the host Ollama HTTP API (`/api/tags`, `/api/pull` with streamed progress, `/api/delete`, `/api/show`, `/api/version`) so the operator installs/updates local models (reference: Qwen3-Coder) without leaving claude-man; a `models/` package with a provider-shaped backend seam (Ollama now, vLLM later) feeding the gateway's local route. Update = re-pull tag + digest compare
- [ ] **9b:** `Project.mode = subscription | hybrid` + a provider-shaped model-backend descriptor (state/registry); `lifecycle.recreate(mode=…)` (validated + persisted before teardown); gateway-sidecar orchestration paralleling `network/egress.py` (ensure/teardown, read-only config bind, fail-closed); additive agent env (`ANTHROPIC_BASE_URL` + discovery flag) rendered like `_render_egress` (floor byte-identical, unit-pinned); `ANTHROPIC_*` scrub UNCHANGED; `ANTHROPIC_DEFAULT_HAIKU_MODEL` set to match the primary backend
- [ ] **9c:** egress for a LOCKED hybrid project — chain the sidecar's `api.anthropic.com` access through the squid allowlist + reach the host model server; TUI mode/billing badge; `image build`-style pin/verify for the gateway image; CLI/TUI verbs (`project model …`); `docs/MODELS.md`
- [ ] **9d (optional, deferred):** the terminating-router flavor (console API key ON THE SIDECAR ONLY + transparent LiteLLM router failover Anthropic→local) as an alternative per-project hybrid auth — only if auto-failover ever outweighs the subscription

---

Legend: `[x]` done in scaffold · `[~]` partially scaffolded (structure + stubs) · `[ ]` not started.
