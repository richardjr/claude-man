# CLAUDE.md

Guidance for Claude Code (and humans) working in the **claude-man** repository.

claude-man is a Python **Textual TUI** + **`claudemanctl`** CLI that provisions and manages
hardened Docker containers, each running Claude Code under a chosen account profile, for a set of
long-lived git-checkout projects. Each container is also a **working hardened dev environment** — a
curated baked shell (starship prompt, history search, `n`/eza/zoxide, a shell-open banner) + neovim
the operator works in alongside the agent (Phase 8 — [`docs/DEVENV.md`](docs/DEVENV.md)). Read
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and [`ROADMAP.md`](ROADMAP.md)
for the phase plan before making non-trivial changes.

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
   `known_hosts`/`config`; keys never enter — the host agent socket is forwarded). One further
   **opt-in** writable surface exists: **only when `config shell-history on`** (default OFF), a
   per-project state-tier bind at `/home/agent/.persistent-history` for a persistent shell `$HISTFILE`
   (`runner._render_shell_history` — additive, never a `_HARDENING` flag, so the default-off floor is
   byte-identical; a unit test pins both paths). The
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
   forbids `NET_ADMIN`, so strict egress is a squid **sidecar** (`network/egress.py`) on a
   per-project `--internal` network (no gateway → no direct route out), with the agent attached to
   that network only and `HTTP(S)_PROXY` pointing at the sidecar — not `iptables` inside the agent
   container. The sidecar is also on the default bridge (its sole egress path); it enforces a
   `dstdomain` allowlist over CONNECT tunnels (no MITM). The agent's strict flags are ADDITIVE in
   `runner._render_egress` (floor byte-identical, invariant 2); `up` is **fail-closed** (a locked
   project never starts if the sidecar can't come up). The base allowlist must always include
   `claude.ai` (the OAuth subscription refresh path) or token refresh fails opaquely. (`dnsmasq`
   direct-DNS forwarding + an in-container `iptables` default-DROP layer are deferred defence-in-depth
   — today's lock covers proxy-aware traffic only; see ROADMAP Phase 4.)
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
  cli.py               claudemanctl argparse surface (profile / project / packs / sync / config / image verbs)
  lifecycle.py         create / up / stop / recreate / delete orchestration shared by the CLI + TUI (+ account-mismatch guard, workspace-ownership pre-flight, env-mount add/remove/resync + ssh seed, sync-checked delete_plan/delete_project teardown, packs refresh + asset sync-in on up / sync-out on stop, per-session scratch-dir wipe + CLAUDE.md note on up / scratch-dir wipe on stop (`_scratch_prepare`/`_scratch_clear`, best-effort), `set_packs` immediate-apply (registry -> materialize -> sync-in, no recreate), profile/overlay switch via `recreate(profile_name=…/overlay=…)` (validated + persisted before teardown; overlay has no account guard + builds the new image via `ensure_chain` if missing), egress lock/unlock (`set_egress` recreate-to-apply) + allowlist `add_allow`/`remove_allow` (registry-only + flocked, validated via `network.allowlist.is_valid_dstdomain`, mode-aware recreate/lock reminder — fast, the TUI Egress screen calls them inline), on-start claude-version check (`check_update`) -> operator-confirmed host-side image rebuild + recreate before start via `up(rebuild_to=...)`; stamps the container version from the image's real baked label)
  assets.py            per-project asset sync (host-side copy of CLAUDE.md + skills/agents between the synced config-tier source ~/.config/claude-man/assets/<slug>/ and the /workspace + ~/.claude binds): sync_in on start (asset wins), sync_out on stop (bind wins), backup-then-overwrite; claude side is a default-DENY allowlist (skills/agents/commands only) with a per-entry filtered recursive copy that drops denylisted-named nested entries + refuses escaping / denylist-targeting symlinks; workspace side is containment-checked; bootstraps a stub CLAUDE.md — distinct from the Phase-5 review-gated sync-back
  claudemd.py          PURE shared patcher for claude-man-owned managed blocks in a CLAUDE.md body (fenced begin/end markers; replace-in-place so multiple blocks coexist + re-patch is byte-identical → no asset/bind sync churn). Used by packs/materialize (the `@`-import block) + scratch.py (the scratch-dir note)
  scratch.py           per-session scratch / data-transfer dir (`/workspace/scratch`): a SUBDIR of the existing /workspace bind (NOT a new mount — hardened floor byte-identical, invariant 2). `clear(slug)` containment-checked wipe+recreate on every start AND stop (best-effort; never blocks start/stop); `ensure_note(project)` stamps a managed block into /workspace/CLAUDE.md telling the agent to look there for "provided files" (via claudemd, in-place, operator content preserved). Wiped-each-session, so durable work goes in a repo under /workspace; a repo dir under `scratch/` is refused at `project repo add`
  usage.py             per-profile token-usage parsed from project transcripts (read-only, separate from sync-back)
  usage_api.py         per-account subscription usage (5-hour + weekly bars) via GET /api/oauth/usage with a profile's OAuth token — no-redirect opener (no cross-host token leak); pure parse/render split from the network fetch
  updates.py           resolve the latest/stable claude version (token-less GET of downloads.claude.ai/claude-code-releases/<channel> — same endpoint the native installer reads) so the on-start check can offer a host-side image rebuild before `up` when a newer claude exists; pure parse/compare split from the fetch, fails OPEN (offline -> start on the existing image). Never an in-container update (`~/.local` is read-only — invariant 2 holds)
  gitconfig.py         resolve the git author identity (config.toml [git] override, else inherited host git config) → GIT_CONFIG_* env injected at docker create (no writable file needed under --read-only)
  gh_token.py          optional GitHub token (state-tier 0600, NOT config.toml) injected pass-through as GH_TOKEN for in-container `gh` — opt-in via `config gh-token` (invariant 1)
  env_secrets.py       per-project `kind="env"` env-mount VALUES (state-tier 0600 env.json, NOT config.toml/synced) — names live in the registry; values injected `-e NAME` pass-through (invariant 1)
  ssh_agent.py         host-side ssh-agent bootstrap (HOST-ONLY, invariant 1 family): ensure/adopt a managed agent at a stable state-tier socket (`config.managed_ssh_agent_sock()`, parent forced 0700; only a socket owned by the current uid is adopted) + ensure-load the configured keys non-interactively (forced-failing SSH_ASKPASS so a passphrase key fails fast instead of grabbing the TUI) + export `SSH_AUTH_SOCK` into `os.environ` so docker creates forward the agent socket — private keys NEVER enter a container (lifecycle `ensure_ssh_keys`/`add_ssh_key`/`remove_ssh_key` delegate here); pure fingerprint parse split from the ssh subprocess shell
  (ports)              published container ports (`[[project.ports]]` -> `schema.PortMapping`): INGRESS, rendered additively as `-p <bind>:<host>:<container>/<proto>` by `docker/runner._render_ports` (never a `_HARDENING` flag — floor byte-identical, unit-pinned). container port MUST be ≥1024 (`--cap-drop ALL` drops NET_BIND_SERVICE); default bind 127.0.0.1 (host-only) with per-port `0.0.0.0` opt-in. Orthogonal to the egress firewall (invariant 3 — ingress, not egress). Fixed at create -> recreate to apply
  __main__.py          `python -m claudeman` -> TUI;  argv dispatch
  registry/            projects.py, profiles.py (load/save/default_profile/load_token/token_age_days), settings.py (global config.toml 'general features' tier: ssh keys + git identity + terminal/opener + splash + claude image channel/pin/update-check), schema.py  — TOML store
  docker/              labels.py, runner.py (hardened `docker create` argv + env_file scrub + additive env-mount render + exec-stdin ssh seed + git_env identity + baked GIT_CONFIG_GLOBAL/GH_CONFIG_DIR redirects), status.py (live ps JOIN), stats.py (per-container `docker stats` NetIO — pure argv+parse, time-bounded wrapper — the TUI Network panel's Traffic figure), images.py (build/exists + base→overlay auto-build chain), smoke.py (hardened-profile image gate)
  profiles/            setup_token.py (mint/renew/verify via `claude setup-token`+`auth status`), identity.py (scrubbed stub), seed.py (claude-config seeding + host ~/.claude capture)
  checkout/            repos.py (host-side clone/fetch into workspace/ + cred-mask + dir containment; host PAT never enters the container), gitstate.py (porcelain-v2 parser → per-repo live state: branch/dirty/ahead-behind/drift)
  network/             allowlist.py (base egress set + project extras), squid.py (PURE squid.conf renderer — CONNECT allowlist, no MITM), egress.py (Phase 4 strict-egress orchestration: pure `*_argv` renderers for the per-project `--internal` net + `claude-man:proxy` squid sidecar + the bridge-connect; pure access-log parsers `parse_access` (allowed+denied structured records: host/port/allowed/bytes/method/status — `parse_denied` is now a thin filter over it) + `summarize_access` (records → per-host counts/ports/bytes/last-seen, feeds the TUI Network panel) + `smoke_verdict`; daemon wrappers `ensure_network`/`ensure_proxy`/`teardown`/`denied_requests`/`access_log`/`smoke`). The agent's own strict flags (`--network` + `HTTP(S)_PROXY`) are additive in `runner._render_egress` — floor byte-identical (invariant 2, unit-pinned)
  packs/               curated packs (Phase 6 — docs/PACKS.md): library.py (PURE discovery/parse/hash of the in-repo `library/packs/<tier>/<pack>/` bundles — tiers = `common/` + per-language, pack names library-unique, the shipped library linted by a test), materialize.py (selection -> asset-source writes + the fenced CLAUDE.md `@`-import block + a state-tier manifest separating pack-managed files from operator files — operator file wins collisions, curated-wins drift w/ backup, deselect removes from source AND binds). Selection = `Project.packs` (+ explicit `Project.language` for the tier; defaults resolved at CREATE from `default = true` packs in common/ + <language>/); container delivery rides assets.sync_in — no new mounts (invariant 2). `launch_workdir` now defaults to /workspace ALWAYS (lone-repo auto-cd dropped; explicit `workdir` wins)
  syncback/            review-gated 3-way merge of in-container ~/.claude changes back to the operator's host ~/.claude (Phase 5, invariant 5): denylist.py (security boundary, enforced before any read), fsmerge.py (shared FS primitives factored out of assets.py — one audited copy/backup/symlink-guard impl), artifacts.py (USER-scope artifact registry), baseline.py (3-way manifest: sha256 trees / canonical-JSON keys / narrow mcpServers-only .claude.json read), detect.py (per-file/key/server classify + conflict + no-op/own-write subtraction + no-baseline implicit reference), diff.py (difflib + canonical key-diff, every line secret-masked incl. value-shape scan), merge.py (GLOBAL flock -> backup-first -> gated tree copy -> inverse settings field-patch (hooks/statusLine immune) -> MCP gate-only -> sync_audit_dir git commit w/ staging-time denylist re-assert -> baseline refresh). Wired via lifecycle.sync_plan/sync_apply + baseline-on-up/pending-note-on-stop; CLI `sync plan`/`sync review [--yes]`; TUI SyncReviewScreen
  tui/                 app.py (projects JOIN + live Repos column / repo-detail panel via a 30s gitstate worker + per-profile usage panel — token totals plus 5h/Week subscription bars from a 60s refresh_utilization worker + a per-project Network panel — Traffic from `docker stats` NetIO (whole-container, every project incl. open) and Blocked/Allowed distinct-destination counts from the squid access log (locked only), repainted on the projects-poll cycle via `refresh_net`), terminals.py (detached terminal spawn via a settings-driven per-platform launcher table — ghostty/alacritty/kitty/wezterm/foot/gnome-terminal/konsole/xterm, Terminal.app+iTerm2 on macOS, wt on WSL2, or a custom '{argv}' template; the one-claude-per-container guard (SEC-3) in spawn_claude; `spawn_nvim` opening the baked neovim in the project workdir — the `e` Editor action / `project nvim`; + `spawn_path` opening the workspace mount in the system file manager via xdg-open/gio / `open` / wslview — the `b` Browse action), splash.py (PURE boot-splash frame generation — logo/gradient/sweep markup, no textual/rich imports, unit-tested), rowfx.py (PURE row-sweep frames in the splash palette — glint head on terracotta/ember tints sampled from the logo gradient; swipes a project's row once when its status flips to/from UP (status-poll diff) and a repo-panel row ×3 when its git state visibly changes (gitstate diff); driven by a paused-when-idle 30 fps timer in app.py, same no-textual/rich pattern as splash, unit-tested. The repo panel's ↑/↓ cell pops yellow when non-0/0 — gitstate.ab_style), packsview.py (PURE view model for the Packs screen — grouped Common / <language> / Other-selected rows + toggle semantics + the read-only `pack_states` freshness map (stale/drifted/operator/unknown vs the manifest's ours/theirs boundary) behind the State column; the Other section keeps cross-tier/stale selections visible so a toggle's full-list save can't drop them; no textual imports, unit-tested), screens/ (splash — the boot animation screen: transparent-bg modal whose fill scrolls up to reveal the UI, any key skips, off via `config splash off`; create (incl. the Language pack-tier Select, overlay pre-fills the suggestion), add_repo, remove_repo, env_mounts, add_mount, add_port, ports, packs (the Packs… checklist — toggles + defaults via lifecycle.set_packs, immediate apply, drift State column from packsview.pack_states), egress (the Egress… screen — lock/unlock toggle + allowlist extras add/remove + a promote-blocked-host picker over `egress.summarize_access`; allowlist edits apply inline (fast `add_allow`/`remove_allow`), lock/unlock/apply dismiss a target mode the app applies off-thread via `set_egress`) + add_allow (the add-domain input modal, `is_valid_dstdomain`-validated), update_confirm, settings, terminal_select, overlay_select (the Overlay… image-variant picker — Project menu `i`; lists config.OVERLAYS with the current marked, dismisses the chosen overlay (None on cancel/no-op), applied off-thread via lifecycle.recreate(overlay=…)), git_identity, gh_token, add_key, menu, pull_confirm, delete_project, stop_all_confirm, shutdown, logs, sync_review)
images/                base/Dockerfile (native ~/.local claude install + baked neovim + baked curated bash) + overlays/{python,rust,node,python-node}.Dockerfile (python-node = polyglot combo: python+uv AND corepack yarn/pnpm in one image, for a node project that also needs python/pip; python deps live in a .venv under /workspace, pip/uv caches redirected there)
images/nvim/           curated, no-plugin-manager neovim config baked into the base image (init.lua + after/plugin/curated.lua): TS + Markdown + git-from-nvim + a neo-tree file explorer (<leader>e, pinned to the operator's local LazyVim SHAs) + a mini.starter dashboard (recent files inline — zero-dep, deliberately NOT the picker-driven snacks dashboard). Plugins are native packages (pack/curated/start), treesitter parsers compiled to /opt/nvim-parsers, LSP servers (ts_ls/marksman/jsonls) + prettier on PATH — all baked read-only; nvim writes only shada/state to the .cache tmpfs. No runtime network/Mason. git identity is the injected GIT_CONFIG_* (commits from fugitive/gitsigns carry the right author). Floor unchanged (invariant 2)
images/bash/           curated bash dev environment baked into the base image (Phase 8 — docs/DEVENV.md): bashrc (starship git prompt + history config + eza/zoxide/git aliases + the `n` neovim shortcut + the `hints` banner) + inputrc (↑/↓ history prefix-search + fzf Ctrl-R, `$include /etc/inputrc` first) + starship.toml (the operator's host config, byte-for-byte) + motd (the 8c shell-open banner: the boot-splash CLAUDE-MAN wordmark in the terracotta→ember gradient — per-row RGB byte-identical to tui/splash.py — + a cheat-sheet (n/ls/lt/g/Ctrl-R/<leader>e) + a DYNAMIC history line (ephemeral vs persistent from CLAUDEMAN_HISTFILE) + the starship git legend; baked at /usr/local/bin/claude-man-motd, shown on every interactive tty shell (the `-t 1` guard keeps non-tty exec probes silent; `CLAUDEMAN_NO_MOTD` opts out), re-shown by `hints`). Sourced by the interactive `docker exec ... bash` (terminals.py); the rc bails on NON-interactive shells FIRST so the exec probes (comm/ssh/gitstate) are untouched. Supporting CLIs (starship/fzf/eza/zoxide/bat/bash-completion) from Trixie apt; `bat` is a /usr/local/bin symlink over Debian's `batcat`. All read-only; the only writes (history/zoxide db/starship cache) go to the .cache tmpfs (ephemeral) UNLESS `config shell-history on` mounts a per-project persistent HISTFILE bind (8d, the one opt-in writable surface — invariant 2). NO claude/opencode alias (invariant 6). Default-path floor byte-identical (image ENV only, no runner change); the image-smoke gate exercises the rc under --read-only
templates/             project.toml.example, profile.toml.example, config.toml.example, claude-json-stub.json (the strict-egress squid.conf is rendered in pure Python by network/squid.py — no jinja template, since jinja2 is not a dep)
images/proxy/          strict-egress squid sidecar image (`claude-man:proxy`, debian-slim + squid) — standalone, NOT FROM claude-man:base; rendered config bind-mounted read-only at run
library/packs/         the curated pack library (`<tier>/<pack>/pack.toml` + `claude-md/*.md` + `skills/<name>/`): common/{guardrails,code-quality,workflow} + node/python/rust convention packs. PUBLIC content (the repo is public) — house rules in; client/project-specific material stays in the per-project asset source
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
- **TUI dialog button rows reflow, never crop.** A modal's action-button row (`#buttons`) uses a
  reflowing `ItemGrid(id="buttons", min_column_width=16)` with `grid-gutter: 0 1` — **not** a
  fixed-width `Horizontal`. Textual Buttons default to `min-width: 16`, so a `Horizontal` row of
  them silently overflows the dialog's right edge once they no longer fit (issue #2: the 7-button
  Settings and 6-button Egress toolbars cropped to ~4.5 buttons visible). `ItemGrid` wraps the
  buttons onto as many rows as the dialog needs, so the row can never be cropped no matter the
  dialog width or how many buttons are added. This is the standing pattern for the
  management/toolbar screens (`settings`/`egress`/`env_mounts`/`ports`/`packs`); the simple
  Cancel-plus-one-action confirm/input dialogs keep their right-aligned `Horizontal` footer (two
  buttons never overflow). When adding a button to a toolbar, leave it as `ItemGrid` — don't revert
  to `Horizontal`. There is no render assertion in the dependency-free suite (it can't import
  textual); verify layout changes with a headless `App.run_test()` render.
  The complementary half lives in `ClaudeManApp.CSS` (app.py): an app-wide `#dialog { max-width:
  100% }` clamps every modal's fixed-width `#dialog` (each screen still sets its own `width:`) to the
  terminal width, so a dialog wider than the terminal shrinks to fit instead of being clipped off the
  screen's right edge (there's no horizontal screen scroll — clipped content would be unreachable).
  `width` and `max-width` are different properties, so the designed width is preserved whenever the
  terminal has room. New modals should use `id="dialog"` so they inherit this for free (the splash
  uses `#splash-fill` and is intentionally exempt). Together: dialog fits the terminal, buttons fit
  the dialog — never a crop.
- **Shelling out to docker/git/claude** is done via `subprocess` with explicit argv lists (never
  `shell=True`). The hardened argv is rendered by one pure function (`docker/runner.py::build_create_argv`)
  so it can be unit-tested without a daemon.
- Stubs for unimplemented phases raise `NotImplementedError("phase N: ...")` referencing
  [`ROADMAP.md`](ROADMAP.md) — keep them honest rather than silently no-op.
- **Leave no unused functionality behind.** When a change supersedes or removes a feature, delete
  every part that is now orphaned **in the same change** — the whole chain, not just the visible
  entry point: dead UI entries (menu rows, key bindings, screens with no way in), handlers/functions
  with no caller, helpers used only by code you just deleted, their tests, and stale references in
  the docs (CLAUDE.md / ROADMAP / `docs/`). Grep for each removed symbol before finishing to confirm
  nothing dangles. If a removal makes a previously-shared helper single-use, inline it. The **only**
  deliberate exceptions are the phase stubs above (they reference a ROADMAP item, so they're tracked,
  not dead) — everything else that no longer has a live caller or entry point gets removed, not
  commented out or left "just in case".

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
project repo add|rm|list <slug> [...]              # manage a project's checked-out repos (add clones live into /workspace; rm drops from the registry, --purge also removes the checkout)
project sync-repos <slug> | pull <slug>            # git fetch each repo / fast-forward each repo (ff-only; skips dirty/diverged)
project delete <slug> [--keep-workspace] [--force] # tear down (container + state + registry; idempotent); --keep-workspace preserves the checkout; --force overrides the unsynced-work guard
project stop-all                                   # end-of-day: stop + sync-out EVERY running container (best-effort batch); TUI: top-level `S`
project recreate <slug> [--profile X] [--overlay Y] [--force] [--update-yes|--no-update]   # rebuild / switch account (mismatch-guarded; applies a changed git identity) / switch overlay-image (--overlay; validated, persisted, builds the image if missing); offers the on-start claude update like `up` does (prompt, default) — the container is pre-removed so the rebuild always applies. TUI: Project menu -> Overlay… (`i`)
project shell|claude|nvim <slug>                    # auto-start the container if needed, then open a detached terminal into it
project assets <slug> [--bootstrap]                 # show the synced asset source dirs (CLAUDE.md + skills/agents); --bootstrap a stub CLAUDE.md
project sync <slug> [--in]                          # manually sync assets out (bind -> source); --in forces sync-in (source -> bind)
sync plan <slug>                                    # sync-BACK dry-run: detect in-container ~/.claude changes vs the baseline + print masked diffs (no write)
sync review <slug> [--yes]                          # review the sync-back plan; --yes applies the DEFAULT decisions (text accept; settings/MCP/conflict/deletion reject). Per-row accept/reject is TUI-only (projects table `y` -> SyncReviewScreen)
project env add <slug> ssh|file|env [...]           # add an env mount; `env <NAME>` prompts (hidden) for a value -> 0600 state, injected -e NAME (recreate to apply)
project env rm <slug> <ssh|dst|NAME> | env list     # remove (by ssh / file dst / env var name) or list a project's env mounts
project ports add <slug> <container|host:container> [--bind IP] [--proto tcp|udp]   # publish a service port (-p; container ≥1024; default bind 127.0.0.1 host-only; recreate to apply)
project ports rm <slug> <host[/proto]> | ports list # unpublish a port (by host port) or list a project's published ports
packs list [--tier common|node|…]                   # browse the curated pack library (library/packs/ — docs/PACKS.md)
project packs add|rm <slug> <name>                  # select/deselect a curated pack — applies IMMEDIATELY (materialize + sync-in; no recreate; TUI: Project menu -> Packs…)
project packs list <slug> | packs defaults <slug>   # show the selection / re-apply the library defaults for the project's language
project create <slug> [--language node|python|rust] # --language picks the pack tier; defaults = `default = true` packs in common/ + <language>/
project lock <slug> | unlock <slug>                 # strict egress on/off (squid allowlist proxy on a no-route net; recreate-to-apply; unlock tears the sidecar+net down; TUI: Project menu -> Egress…, which also edits the allowlist + promotes a blocked host)
project egress-log <slug>                           # destinations a locked project tried to reach but the allowlist BLOCKED (from `docker logs` of the sidecar; the TUI's always-on Network panel shows per-project Blocked/Allowed counts + Traffic)
project egress-smoke <slug>                          # daemon-gated end-to-end check: an allowlisted host reaches + a non-allowlisted host is blocked
image build proxy                                   # (re)build the claude-man:proxy squid sidecar image (standalone; auto-built on first lock)
config show                                         # global settings: resolved git identity, gh-token set/none, claude image channel/pin/update-check, terminal/opener, splash, ssh keys/load status
config terminal [--program X | --custom '…' | --auto]   # terminal for shell/claude/nvim windows (built-in launcher table per platform, or an '{argv}' template; TUI: Settings -> e)
config opener [--command '…' | --auto]             # file-manager command for Browse (b)
config splash [on|off]                             # the TUI boot splash (any key skips it)
config shell-history [on|off]                      # persist in-container bash history across recreate (default off; opt-in writable bind — recreate to apply)
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
