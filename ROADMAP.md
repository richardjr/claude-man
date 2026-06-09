# Roadmap

Phased so an early phase yields a runnable skeleton and each later phase adds one load-bearing
subsystem. Each phase lists its goal and concrete deliverables. Checkboxes track scaffold status.

> **2026-06-02 scaffold review:** see [`docs/REVIEW.md`](docs/REVIEW.md) for the 38 verified
> findings. The one critical defect (the baked `claude` was a dangling symlink, unrunnable under
> the hardened profile) plus the `--env-file` credential-scrub gap are addressed in the new
> **Phase 0.5**. Lower-severity findings are folded into the phase that owns them (tagged inline).

## Current status — 2026-06-05

**Done & verified:** Phase 0, **Phase 0.5** (hardened image runs `claude` under `--read-only
--user`; `image smoke` gate; native `~/.local` install so `claude doctor` is clean; `env_file`
scrubbed), **Phase 1** (create / up / stop / shell / claude / status — a project goes from TOML to
a running hardened container), **Phase 2** (accounts: `profile add`/`renew`/`verify`/`seed`/
`usage`, per-project profile, `project recreate --profile` with an email-mismatch guard, per-profile
token-usage in the CLI + TUI), and **Phase 3 (repos — CLI + TUI)** — `project repo add`/`rm`/`list`,
`sync-repos`, and live per-repo git state (branch, clean/dirty, ahead/behind, branch-vs-config drift)
via the new `checkout/gitstate.py` porcelain-v2 parser; add clones live into the running container
(no recreate), with dir-containment, credential-masking, a per-slug `flock`, and the BUG-5/BUG-6 fixes.
The **TUI** surfaces it as a live Repos column + a per-project repo-detail panel (8 s fetch-less gitstate
worker, `g` for a fetch-ful rescan) plus `a` Add-repo / `R` Remove-repo modal screens. A
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
open via `docker exec -w` in a lone repo's dir by default (else `/workspace`). **Per-account
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
neovim 0.12 with a curated, no-plugin-manager config (`images/nvim/`) for TypeScript + Markdown +
git-from-nvim: plugins are native packages, treesitter parsers compiled to `/opt/nvim-parsers`, and
LSP servers (`ts_ls`/`marksman`/`jsonls`) + prettier baked on PATH — all read-only, no runtime
network/Mason; nvim writes only shada/state to the `.cache` tmpfs, so the hardened floor is unchanged.
Commits from fugitive/gitsigns carry the injected git identity. 341 dependency-free
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

**Next up (small polish):** auto-capture the token in `profile add` (skip the paste); move the projects
table to an async `docker ps`/`docker events` worker (TUI-2); the one-claude-per-container guard
(SEC-3). **Then:** finish Phase 3 (`project delete` / version-bump lifecycle), Phase 4 (strict egress —
incl. routing in-container ssh-git when egress is locked), Phase 5 (sync-back). Tracked lower-severity
items live in [`docs/REVIEW.md`](docs/REVIEW.md).

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
- [x] `images/base/Dockerfile` (debian-slim + node + pinned native claude + non-root uid1000 `/etc/passwd` entry + baked env) and `images/overlays/{python,rust,node}.Dockerfile`
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
- [~] `tui/app.py`: live JOIN + DEFINED→create+start with surfaced errors (TUI-1), `enter`→shell (TUI-3), cursor-by-slug (TUI-7); **still TODO:** async `docker ps` worker (TUI-2), drop the redundant re-query (TUI-4)
- [x] `tui/screens/create.py`: `n`→new-project modal (slug/profile/overlay/egress; inline slug validation + duplicate guard; runs `lifecycle.create_project` in a thread worker that converts every failure to a red `Result`). **Repos/env/allowlist capture deferred** — `create_project` has no `repos` param yet (see Phase 3)
- [x] `tui/terminals.py`: detached ghostty/alacritty spawn (**SEC-6** CLI-boundary slug validation for `shell`/`claude` still TODO — distinct from the create-form slug check above)
- [~] logs pane streaming `docker logs -f` — `screens/logs.py` still a stub (TUI-5)
- [x] Auth: `profiles.load_token(name)` injects a `0600` token file as `CLAUDE_CODE_OAUTH_TOKEN`; minted by `profile add` (Phase 2); `ANTHROPIC_*` scrubbed incl. `env_file` (0.5)
- [ ] **SEC-3:** enforce "one claude per container" at the spawn paths (guard not yet implemented; CLAUDE.md invariant 6 wording reflects this)
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
  repos); `gh auth` is the operator's job (no token injected — `gh auth login` in-container, or supply
  `GH_TOKEN` via an env-mount). Settings: `Settings.git_user_name/git_user_email` + `[git]` in
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
- [~] docker-events-driven refresh — the projects table still polls (TUI-2); the usage panel already uses a thread worker

## Phase 3 — Persistent multi-repo checkouts + full lifecycle
**Goal:** projects own a set of repos, persist across restarts, and tear down cleanly.

_Review notes: **BUG-2** read labels via `docker inspect --format '{{json .Config.Labels}}'`
(not CSV-splitting `docker ps`); **BUG-5** `status.join` prefers registry values + a drift marker
on label divergence (invariant 4); **BUG-6** concise `fetch_all` detail instead of raw git fatal;
**TUI-6** gate actions on orphan rows (container with no registry entry)._

- [x] `checkout/repos.py`: host-side clone of every `[[repos]]` entry into `workspace/`; `project sync-repos` (clone-missing + fetch); **`checkout/gitstate.py`** porcelain-v2 parser → live per-repo state (branch, dirty, ahead/behind, branch-vs-config drift); `project repo add`/`rm`/`list`; registry mutators with dir-containment + cred-mask + per-slug `flock`; **BUG-5** (registry-wins repo count + drift marker) and **BUG-6** (concise `fetch_all`) landed. **TUI:** live Repos column + repo-detail panel (8 s fetch-less gitstate worker, `g` fetch-ful) + `a`/`R` Add/Remove-repo modal screens.
- [x] Idempotent `project delete` (`rm -f` container + `rm -rf` state dir + `rm` toml, registry removed LAST so a partial failure stays retry-able); start/stop/recreate verbs in TUI + ctl. **Sync-gated:** `lifecycle.delete_plan` scans each repo (fetch-less) via `gitstate.delete_risk` and the TUI `DeleteProjectScreen` / CLI surface the per-repo unsynced-work assessment before the irreversible delete — risky repos require an explicit "Delete anyway" (TUI) / `--force` (ctl), and `--keep-workspace` / the "keep workspace" toggle preserves the `/workspace` checkout as a non-destructive exit.
- [ ] Version-bump-by-recreate flow + running-version status column; `backups/` convention
- [ ] DEFINED/STOPPED/UP JOIN hardened; **BUG-2** second `docker inspect` per-label read for robust multi-label parsing (still latent — values are comma-free today)
- [x] **Per-project asset sync** (`assets.py`): a synced config-tier source `~/.config/claude-man/assets/<slug>/{workspace,claude}/` mirrors CLAUDE.md (→ `/workspace`) + skills/agents/commands (→ `~/.claude`, USER scope) to/from the host binds. `sync_in` on `up` (asset wins, runs after the profile seed so per-project assets layer on top, before `runner.start`); `sync_out` on `stop` (bind wins); **back up the target before every overwrite** (`backups/<ts>/{in,out}/`) and refuse on backup failure — last-write-wins, nothing lost. The claude side is a **default-deny allowlist** (only `skills`/`agents`/`commands` — a blocklist can't safely enumerate sensitive paths like `projects/` transcripts) with a filtered recursive copy that drops denylisted-named nested entries and refuses symlinks that escape the source or target a denylisted path; the workspace side is containment-checked (`is_within`). Bootstraps a stub `CLAUDE.md` if none exists. **TUI** `q` quits immediately and leaves containers running (it never stops/syncs — that previously blocked the close behind a serial, unabortable per-container `docker stop`); the end-of-day "stop + sync all running containers" is the separate top-level `S` (stop-all) command (`StopAllConfirmScreen` → `ShutdownScreen` progress modal → off-thread stop-all worker, offering stop-sync-&-quit or stop-sync-&-stay; `_busy`/`_stopping_all` guarded). **ctl:** `project stop-all` (batch stop+sync via `lifecycle.stop_all`), `project assets [--bootstrap]`, `project sync [--in]`. `[project.sync]` TOML block (enabled/workspace/claude). Distinct from the Phase-5 review-gated sync-back (this targets an isolated per-project dir, so it's safe to run automatically). Migration: drop a project's `~/Work/CLAUDE.md` `file` env-mount (it shadows the synced file) and recreate.

## Phase 4 — Lockable strict egress
**Goal:** per-project opt-in strict egress allowlist that keeps `--cap-drop ALL` intact.

_Review notes: **IMG-4** before strict egress can work for the node/python overlays, pin their
toolchain caches to read-only system paths (`COREPACK_HOME`, uv cache) so first run needs no
network, and add the yarn download hosts (`registry.yarnpkg.com`, `repo.yarnpkg.com`) to the
base allowlist; verify offline in `image smoke <overlay>`._

- [ ] `network/allowlist.py` base set (incl. `claude.ai` for OAuth refresh + GitHub + npm) + project extras
- [ ] `network/squid.py`: generate squid+dnsmasq sidecar compose, `internal: true` agent net + egress net, proxy env + apt/npm/git proxy wiring, dnsmasq → `172.17.0.1`
- [ ] `project lock`/`unlock` verbs; denied-request logging surfaced in the TUI for allowlist tuning
- [ ] Smoke: `claude.ai` refresh, github clone, npm install, apt install all succeed under lock; `example.com` blocked

## Phase 5 — Sync-back accept flow
**Goal:** review-gated three-way merge of container config changes to host + setups repo.

_Review notes: **SYNC-5** `json_key_diff` must drop `is_denied_json_key` keys before diffing (so
`oauthAccount`/`userID` never reach the diff buffer); **SYNC-3** containment-check synced skill
symlinks (reject/down-rank absolute/escaping targets); **SYNC-1** add project/repo-scope artifact
producers; **BUG-3** anchor the `*-cache.json` deny to the basename; **BUG-4** add a value-shape
secret scan to `mask_line`. (SYNC-4/SYNC-6 denylist additions + SEC-5 doc fix already landed.)_

- [ ] `syncback/artifacts.py` registry + `denylist.py` enforced before read; `baseline.py` 3-way manifest (sha256 / canonical-JSON / symlink-target)
- [ ] `detect.py` classify; `diff.py` difflib + canonical key-diff + secret-mask; per-profile merge lock
- [ ] `tui/screens/sync_review.py` accept/reject/skip gate with defaults (text accept; settings/MCP/deletions/conflict reject)
- [ ] `merge.py`: backup → field-patch `settings.json` → `claude mcp add/remove --scope` → symlink-preserving tree copy + path rewrite → mirror to `setups/claude-code` + git commit; baseline refresh; `sync --plan` dry-run

---

Legend: `[x]` done in scaffold · `[~]` partially scaffolded (structure + stubs) · `[ ]` not started.
