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

1. **Never copy credentials INTO a container, and never inject `ANTHROPIC_API_KEY` /
   `ANTHROPIC_AUTH_TOKEN`.** Auth has two per-project modes (`Project.auth`, default `token`;
   fixed at create → recreate-to-apply; surfaced everywhere — the `claude-man.auth` container
   label, the status AUTH column, the TUI `[login]` Profile badge, the up-notes — so the posture
   is never silent):
   **token (default)** — the env-var long-lived token model: `claude setup-token` once per profile
   on the host → a `0600` token file → injected at launch as `CLAUDE_CODE_OAUTH_TOKEN`. Its
   `user:inference`-only scope means claude.ai account connectors (remote MCP), usage bars, and
   other full-subscription surfaces are unavailable in-container (upstream-intentional —
   docs/DEBUGGING.md; locally-configured MCP via `claude mcp add` works fine).
   **login (opt-in, per-project)** — NO token env is injected (`ensure_created` resolves
   `token=None` deliberately); the operator runs `/login` once inside the container (code-paste
   flow — no in-container browser) and the in-container claude MINTS its own `.credentials.json`
   in that project's claude-config bind, where it self-refreshes in place, survives stop/recreate,
   is denylisted from sync-back (invariant 5), and is removed by `project logout`, any forced
   identity re-seed (`seed_project_config(overwrite_identity=True)` unlinks it), and `delete`.
   What stays absolutely forbidden in BOTH modes: copying or bind-mounting any HOST credential
   file into a container (the known headless 401/no-refresh bug applies to copied-in credentials;
   a **`file` env-mount's container `dst` may never target `/home/agent/.claude` (or any managed
   mount)** — a bind onto `…/.claude/.credentials.json` would smuggle a working credentials file
   in (a verified attack); `schema.EnvMount` rejects it — unchanged under login mode: only the
   in-container claude may mint a credential there); injecting `ANTHROPIC_*` keys (they silently
   outrank claude's auth and can bill the wrong account, so they are scrubbed from the rendered
   container env — including `env_file`, which is parsed + scrubbed host-side and injected as
   pass-through so values never reach argv); ever reading the host's `~/.claude/.credentials.json`.
   A login-mode credential is different in kind: minted BY the sandboxed claude, FOR one project,
   living only in that project's bind — it never crosses the trust boundary in either direction.
   Never pass `--bare` to the in-container `claude` (it ignores the token).
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
   `--security-opt no-new-privileges`, `--user 1000:1000`, `--pids-limit 1024`, **plus a hard
   memory cap `--memory X --memory-swap X` that is ALWAYS rendered** (issue #29 — the one floor
   flag whose VALUE is operator-chosen: `Settings.container_memory`, default `16g`, min `1g`, via
   `config memory` / Settings `m`; equal `--memory-swap` = no swap spill, so a runaway OOM-kills
   inside its own cgroup instead of starving the host — a 30 GB in-container `node` once took out
   the host's Chrome. `runner._render_memory` re-validates via the pure
   `config.normalise_memory_limit`; `settings.load` coerces junk to the default so a value is
   always renderable; there is deliberately no "off" — raise it if you need more), with writable
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
   size-capped tmpfs — a 256m cache OOM'd a large v1 install with ENOSPC. Berry ALSO needs
   `YARN_ENABLE_MIRROR=false`: its mirror defaults ON and duplicates the full package cache into
   `globalFolder/cache` on the tmpfs even with a local cacheFolder, re-ENOSPC'ing a large install.)
   Pinning the owner is *not* a floor relaxation —
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
   `claude.ai` (the OAuth subscription refresh path) or token refresh fails opaquely. **git-over-ssh**
   is made proxy-aware under lock (issue #12): ssh ignores `HTTP(S)_PROXY`, so `lifecycle._seed_ssh`
   rewrites github/gitlab/bitbucket to their **SSH-over-443** endpoints (`ssh.github.com`/`altssh.*`)
   and `ProxyCommand`s through the sidecar with baked `corkscrew`, riding the SAME `dstdomain` allowlist
   (squid `CONNECT` is 443-only; the `to_localhost`/`to_linklocal` denies still apply; keys never
   enter — agent socket forwarded, invariant 1). Strict-mode + ssh-mount only, per-forge `Host` stanzas
   (never `Host *`); Azure DevOps has no 443 SSH endpoint → HTTPS/open fallback. (`dnsmasq` direct-DNS
   forwarding + an in-container `iptables` default-DROP layer are deferred defence-in-depth — today's
   lock covers proxy-aware traffic plus the SSH-over-443 forges; see ROADMAP Phase 4.)
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
  lifecycle.py         create / up / stop / recreate / delete orchestration shared by the CLI + TUI (+ account-mismatch guard, workspace-ownership pre-flight, env-mount add/remove/resync + ssh seed (seeds ~/.ssh/{config,known_hosts}; the baked global known_hosts pre-trusts the common forges, and `set_ssh_auto_trust` toggles the per-project TOFU accept-new block — re-seed-to-apply, no recreate; issue #4), sync-checked delete_plan/delete_project teardown, login-auth mode (`set_auth` recreate-to-apply / `logout` (refused while running) / the pure `login_identity_action` + `_verify_login_identity` up-time warn-or-backfill — invariant 1's opt-in amendment), packs refresh + asset sync-in on up / sync-out on stop, per-session scratch-dir wipe + CLAUDE.md note on up / scratch-dir wipe on stop (`_scratch_prepare`/`_scratch_clear`, best-effort), `set_packs` immediate-apply (registry -> materialize -> sync-in, no recreate), profile/overlay switch via `recreate(profile_name=…/overlay=…)` (validated + persisted before teardown; overlay has no account guard + builds the new image via `ensure_chain` if missing; a recreate into a NON-hybrid state also `gateway.teardown`s any leftover hybrid sidecar+net — the local-pin unpin/displacement paths ride this), egress lock/unlock (`set_egress` recreate-to-apply) + allowlist `add_allow`/`remove_allow` (registry-only + flocked, validated via `network.allowlist.is_valid_dstdomain`, mode-aware recreate/lock reminder — fast, the TUI Egress screen calls them inline), on-start claude-version check (`check_update`) -> operator-confirmed host-side image rebuild + recreate before start via `up(rebuild_to=...)`; stamps the container version from the image's real baked label)
  assets.py            per-project asset sync (host-side copy of CLAUDE.md + skills/agents between the synced config-tier source ~/.config/claude-man/assets/<slug>/ and the /workspace + ~/.claude binds): sync_in on start (asset wins), sync_out on stop (bind wins), backup-then-overwrite; claude side is a default-DENY allowlist (skills/agents/commands only) with a per-entry filtered recursive copy that drops denylisted-named nested entries + refuses escaping / denylist-targeting symlinks; workspace side is containment-checked; bootstraps a stub CLAUDE.md — distinct from the Phase-5 review-gated sync-back
  claudemd.py          PURE shared patcher for claude-man-owned managed blocks in a CLAUDE.md body (fenced begin/end markers; replace-in-place so multiple blocks coexist + re-patch is byte-identical → no asset/bind sync churn). Used by packs/materialize (the `@`-import block) + scratch.py (the scratch-dir note)
  scratch.py           per-session scratch / data-transfer dir (`/workspace/scratch`): a SUBDIR of the existing /workspace bind (NOT a new mount — hardened floor byte-identical, invariant 2). `clear(slug)` containment-checked wipe+recreate on every start AND stop (best-effort; never blocks start/stop); `ensure_note(project)` stamps a managed block into /workspace/CLAUDE.md telling the agent to look there for "provided files" (via claudemd, in-place, operator content preserved). Wiped-each-session, so durable work goes in a repo under /workspace; a repo dir under `scratch/` is refused at `project repo add`
  usage.py             per-profile token-usage parsed from project transcripts (read-only, separate from sync-back)
  updates.py           resolve the latest/stable claude version (token-less GET of downloads.claude.ai/claude-code-releases/<channel> — same endpoint the native installer reads) so the on-start check can offer a host-side image rebuild before `up` when a newer claude exists; pure parse/compare split from the fetch, fails OPEN (offline -> start on the existing image). Never an in-container update (`~/.local` is read-only — invariant 2 holds)
  gitconfig.py         resolve the git author identity (config.toml [git] override, else inherited host git config) → GIT_CONFIG_* env injected at docker create (no writable file needed under --read-only)
  gh_token.py          optional GitHub token (state-tier 0600, NOT config.toml) injected pass-through as GH_TOKEN for in-container `gh` — opt-in via `config gh-token` (invariant 1)
  env_secrets.py       per-project `kind="env"` env-mount VALUES (state-tier 0600 env.json, NOT config.toml/synced) — names live in the registry; values injected `-e NAME` pass-through (invariant 1)
  ssh_agent.py         host-side ssh-agent bootstrap (HOST-ONLY, invariant 1 family): ensure/adopt a managed agent at a stable state-tier socket (`config.managed_ssh_agent_sock()`, parent forced 0700; only a socket owned by the current uid is adopted) + ensure-load the configured keys non-interactively (forced-failing SSH_ASKPASS so a passphrase key fails fast instead of grabbing the TUI) + export `SSH_AUTH_SOCK` into `os.environ` so docker creates forward the agent socket — private keys NEVER enter a container (lifecycle `ensure_ssh_keys`/`add_ssh_key`/`remove_ssh_key` delegate here); pure fingerprint parse split from the ssh subprocess shell
  (ports)              published container ports (`[[project.ports]]` -> `schema.PortMapping`): INGRESS, rendered additively as `-p <bind>:<host>:<container>/<proto>` by `docker/runner._render_ports` (never a `_HARDENING` flag — floor byte-identical, unit-pinned). container port MUST be ≥1024 (`--cap-drop ALL` drops NET_BIND_SERVICE); default bind 127.0.0.1 (host-only) with per-port `0.0.0.0` opt-in. Orthogonal to the egress firewall (invariant 3 — ingress, not egress). Fixed at create -> recreate to apply
  doctor.py            host prerequisite checks behind `claudemanctl doctor` + the TUI setup wizard/startup banner: PURE classify_* verdicts (docker distinguishes binary-missing / daemon-down / socket-EACCES with per-cause fix hints; claude/image/profiles WARN not FAIL) + never-raising probe_* shells (the updates.py pattern) + run_all() -> Report. No textual imports
  __main__.py          `python -m claudeman` -> TUI;  argv dispatch (`_CTL_GROUPS` = every top-level claudemanctl group/verb so `claudeman <group>` reaches the CLI, not the TUI — parity with the real parser pinned by tests/test_main_dispatch.py)
  registry/            projects.py (+ `set_auth` — the per-project auth-mode scalar patch), profiles.py (load/save/default_profile/load_token/token_age_days + `set_account_email` — the login-mode identity-backfill scalar patch, deliberately NOT via save() which never writes [profile.scrub] back), settings.py (global config.toml 'general features' tier: ssh keys + git identity + terminal/opener + splash + claude image channel/pin/update-check + the `[container] memory` hard cap — `set_container_memory`, validated/canonicalised via `config.normalise_memory_limit`, junk coerced to the 16g default at load), schema.py  — TOML store
  docker/              labels.py (incl. the `claude-man.auth` mode label — a login container self-describes, invariant 4), runner.py (hardened `docker create` argv + the ALWAYS-present `_render_memory` hard cap (`--memory X --memory-swap X`, value from settings) + env_file scrub + additive env-mount render + exec-stdin ssh seed + git_env identity + baked GIT_CONFIG_GLOBAL/GH_CONFIG_DIR redirects), status.py (live ps JOIN), stats.py (per-container `docker stats` NetIO — pure argv+parse, time-bounded wrapper — the TUI Network panel's Traffic figure), images.py (build/exists + base→overlay auto-build chain), smoke.py (hardened-profile image gate)
  profiles/            setup_token.py (mint/renew/verify via `claude setup-token`+`auth status`), identity.py (scrubbed stub), seed.py (claude-config seeding + host ~/.claude capture; a FORCED identity overwrite also unlinks a login-minted .credentials.json — a cross-account switch must not keep the old account's live credential)
  checkout/            repos.py (host-side clone/fetch into workspace/ + cred-mask + dir containment; host PAT never enters the container), gitstate.py (porcelain-v2 parser → per-repo live state: branch/dirty/ahead-behind/drift)
  network/             allowlist.py (base egress set + project extras), squid.py (PURE squid.conf renderer — CONNECT allowlist, no MITM), egress.py (Phase 4 strict-egress orchestration: pure `*_argv` renderers for the per-project `--internal` net + `claude-man:proxy` squid sidecar + the bridge-connect; pure access-log parsers `parse_access` (allowed+denied structured records: host/port/allowed/bytes/method/status — `parse_denied` is now a thin filter over it) + `summarize_access` (records → per-host counts/ports/bytes/last-seen, feeds the TUI Network panel) + `smoke_verdict`; daemon wrappers `ensure_network`/`ensure_proxy`/`teardown`/`denied_requests`/`access_log`/`smoke`). The agent's own strict flags (`--network` + `HTTP(S)_PROXY`) are additive in `runner._render_egress` — floor byte-identical (invariant 2, unit-pinned)
  packs/               curated packs (Phase 6 — docs/PACKS.md): library.py (PURE discovery/parse/hash of the in-repo `library/packs/<tier>/<pack>/` bundles — tiers = `common/` + per-language, pack names library-unique, the shipped library linted by a test), materialize.py (selection -> asset-source writes + the fenced CLAUDE.md `@`-import block + a state-tier manifest separating pack-managed files from operator files — operator file wins collisions, curated-wins drift w/ backup, deselect removes from source AND binds). Selection = `Project.packs` (+ explicit `Project.language` for the tier; defaults resolved at CREATE from `default = true` packs in common/ + <language>/); container delivery rides assets.sync_in — no new mounts (invariant 2). `launch_workdir` now defaults to /workspace ALWAYS (lone-repo auto-cd dropped; explicit `workdir` wins)
  network/gateway.py   Phase 9 hybrid-model gateway (issue #14): PURE renderers (mirrors squid egress.py) — `gateway_config_yaml` (LiteLLM config.yaml string: the `claude-* → anthropic/claude-*` PREFIX-PRESERVING wildcard forwards the full `claude-opus-4-8` to Anthropic — `anthropic/*` captured the suffix and forwarded the invalid bare `opus-4-8`, the issue #14 404; the agent's claude.ai OAuth keeps the SUBSCRIPTION via LiteLLM's DEDICATED anthropic path (clean_headers/optionally_handle_anthropic_oauth — NOT `forward_client_headers_to_llm_api`, which only carries anthropic-beta+x-*; verified live: 200 on the subscription lane); a `claude-local-<model>` row → host `ollama_chat/<model>` with `additional_drop_params` force-dropping `thinking`/`reasoning_effort` on the LOCAL route only — non-thinking coders hard-error in Ollama), `gateway_run_argv` (pinned LiteLLM sidecar BY DIGEST; master key state-tier 0600 injected pass-through via `x-litellm-api-key`, never argv), `network_create_argv` (a per-project bridge net — open hybrid keeps egress + resolves the sidecar). Daemon wrappers ensure_network/ensure_gateway (fail-closed `/health/liveliness` gate)/teardown + `check_local_backend` (fail-OPEN `up` pre-flight: warns via the pure `local_backend_warning` if host Ollama is unreachable or the pinned model unpulled — the Claude leg works regardless). `Project.model` (an ollama tag) is the per-project HYBRID switch (`project model set/clear/show`, recreate-to-apply); the host-Ollama route uses the new `hostplatform.host_gateway_create_args`/`host_loopback_host` seam. The agent-env wiring (ANTHROPIC_BASE_URL + the local model as an explicit ANTHROPIC_CUSTOM_MODEL_OPTION picker row — gateway discovery DROPPED as unreliable — + the `x-litellm-api-key` proxy-auth header so Authorization stays free for the OAuth) is additive in runner (floor byte-identical, invariant 2)
  models/              dynamic local-model management (Phase 9 — docs/MODELS.md, issue #14): base.py (PURE provider-shaped `ModelBackend` Protocol + frozen return types), ollama.py (host Ollama daemon via stdlib urllib — PURE parsers `parse_tags`/`parse_pull_line`/`aggregate_pull_progress`/`parse_show`/`split_ref`/`update_verdict` split from the IO, every method FAILS OPEN like updates.py; token-less registry manifest-digest update probe — no multi-GB pull), presets.py (PURE curated 'recommended coding models' table — Qwen3-Coder-30B the default, library-shape, lint-tested), claude_models.py (PURE curated claude `--model` picker table + `is_claude_ref` — the raw-input disambiguator between a claude ref and an ollama tag; feeds the unified Model picker + the `Project.claude_model` LAUNCH pin, lint-tested). CLI `model list/add/update/rm/show/presets`. claude-man manages MODELS only — installing Ollama (a GPU build, bound `0.0.0.0`, the host firewall opened to the Docker subnet) is a host prerequisite (docs/MODELS.md); the Phase-9 gateway/hybrid-mode wiring (the agent's `claude-local-*` route) is IMPLEMENTED — see network/gateway.py
  syncback/            review-gated 3-way merge of in-container ~/.claude changes back to the operator's host ~/.claude (Phase 5, invariant 5): denylist.py (security boundary, enforced before any read), fsmerge.py (shared FS primitives factored out of assets.py — one audited copy/backup/symlink-guard impl), artifacts.py (USER-scope artifact registry), baseline.py (3-way manifest: sha256 trees / canonical-JSON keys / narrow mcpServers-only .claude.json read), detect.py (per-file/key/server classify + conflict + no-op/own-write subtraction + no-baseline implicit reference), diff.py (difflib + canonical key-diff, every line secret-masked incl. value-shape scan), merge.py (GLOBAL flock -> backup-first -> gated tree copy -> inverse settings field-patch (hooks/statusLine immune) -> MCP gate-only -> sync_audit_dir git commit w/ staging-time denylist re-assert -> baseline refresh). Wired via lifecycle.sync_plan/sync_apply + baseline-on-up/pending-note-on-stop; CLI `sync plan`/`sync review [--yes]`; TUI SyncReviewScreen
  tui/                 app.py (projects JOIN + a Model column = the per-project model pin — the local hybrid tag OR the claude `--model` ref, mutually exclusive in the schema (registry-sourced, `-` when default/subscription-direct — `status.Row.model`, threaded like egress/profile so a pin is never silent; a display hint, not a parser — claude refs never carry a colon but a bare local name is legal, `project model show` names the kind) + live Repos column / repo-detail panel via a 30s gitstate worker + per-profile usage panel — token totals from project transcripts + a per-project Network panel — Traffic from `docker stats` NetIO (whole-container, every project incl. open) and Blocked/Allowed distinct-destination counts from the squid access log (locked only), repainted on the projects-poll cycle via `refresh_net`), terminals.py (detached terminal spawn via a settings-driven per-platform launcher table — ghostty/alacritty/kitty/wezterm/foot/ptyxis/gnome-terminal/konsole/xterm (ptyxis title-only — no class flag; issue #31), Terminal.app+iTerm2 on macOS, wt on WSL2, or a custom '{argv}' template (availability-probed at resolve time exactly like a named launcher — a stale template errors instead of failing silently at Popen); `spawn` returns a SpawnHandle (Popen + tempfile stderr capture) every caller hands to `watch_spawn` — a short SPAWN_PROBE_S wait + the PURE `classify_spawn` (None=running ok / 0=client-server exited ok / non-zero=failed w/ stderr tail) so a start-then-fail surfaces (app.py: log + notify toast, the log pane hides on short terminals; cli: rc 1) instead of a false green line (issue #31); every window carries `window_class`/`window_title(slug)` + a per-project identity cue (prompt chip + re-asserted title in the baked shell; the claude/nvim keep-open wrapper stamps its own OSC title + optional `config.project_tint` OSC-11 background when `config terminal-tint on`, since its exec bypasses the bashrc); the one-claude-per-container guard (SEC-3) in spawn_claude, which also appends the project's `claude_model` pin as `--model <ref>` argv (`claude_model_args` — registry-read, fail-open, shlex-quoted into the keep-open wrapper; LAUNCH-time only, so the pin needs no recreate); `spawn_nvim` opening the baked neovim in the project workdir — the `e` Editor action / `project nvim`; + `spawn_path` opening the workspace mount in the system file manager via xdg-open/gio / `open` / wslview — the `b` Browse action), splash.py (PURE boot-splash frame generation — logo/gradient/sweep markup, no textual/rich imports, unit-tested), rowfx.py (PURE row-sweep frames in the splash palette — glint head on terracotta/ember tints sampled from the logo gradient; swipes a project's row once when its status flips to/from UP (status-poll diff) and a repo-panel row ×3 when its git state visibly changes (gitstate diff); driven by a paused-when-idle 30 fps timer in app.py, same no-textual/rich pattern as splash, unit-tested. The repo panel's ↑/↓ cell pops yellow when non-0/0 — gitstate.ab_style), packsview.py (PURE view model for the Packs screen — grouped Common / <language> / Other-selected rows + toggle semantics + the read-only `pack_states` freshness map (stale/drifted/operator/unknown vs the manifest's ours/theirs boundary) behind the State column; the Other section keeps cross-tier/stale selections visible so a toggle's full-list save can't drop them; no textual imports, unit-tested), setupview.py (PURE view model for the first-run setup wizard — STEPS order, `should_offer` (auto-offer ONLY when config.toml AND profiles AND projects are all absent; Skip/Finish materialise config.toml via `settings_registry.save(load())` so it never re-offers — deliberately no `setup_done` settings field) + per-step body-line renderers over doctor CheckResults; no textual imports, unit-tested), profilesview.py (PURE view model for the Profile picker — registry profiles as display rows with the project's EFFECTIVE current marked + account/default columns + a factual token-age hint (no token / `Nd` / `Nd aging` near the ~1yr setup-token cliff — mtime only, never the token value); no textual imports, unit-tested), screens/ (splash — the boot animation screen: transparent-bg modal whose fill scrolls up to reveal the UI, any key skips, off via `config splash off`; create (incl. the Language pack-tier Select, overlay pre-fills the suggestion), add_repo, remove_repo, env_mounts (list/add/remove/resync mounts + the `t` ssh auto-trust toggle), add_mount, add_port, ports, packs (the Packs… checklist — toggles + defaults via lifecycle.set_packs, immediate apply, drift State column from packsview.pack_states), egress (the Egress… screen — lock/unlock toggle + allowlist extras add/remove + a promote-blocked-host picker over `egress.summarize_access`; allowlist edits apply inline (fast `add_allow`/`remove_allow`), lock/unlock/apply dismiss a target mode the app applies off-thread via `set_egress`) + add_allow (the add-domain input modal, `is_valid_dstdomain`-validated), update_confirm, settings, models (the Models management screen — global `m` binding; lists/installs/updates/removes/inspects host-Ollama models via the models/ backend, with a pull-progress worker + the AddModelScreen preset picker; Phase 9), setup (the first-run SetupWizardScreen — welcome/checks -> docker -> terminal -> profile -> image -> done, ONE self-updating modal over setupview.STEPS with the full button set composed once and display-toggled per step (a remove_children+mount rebuild races textual's async removal — DuplicateIds); pushed by app.on_mount UNDER the splash when should_offer, re-run via Settings `w`; the profile step mints INLINE via `app.suspend()` running setup_token.mint (tty restored, browser + token paste, resumes — UI thread by design), the image step streams images.ensure_chain into a RichLog on a thread worker), terminal_select (the launcher picker — custom row ALWAYS shown; picking it opens terminal_custom), terminal_custom (the CustomTerminalScreen '{argv}' template editor — shlex + exactly-one-bare-{argv} validation mirroring Settings.__post_init__, warn-once-then-save when argv0 is off PATH; closes the issue #31 gap where an unlisted terminal was CLI-only), memory_limit (the Settings `m` modal — ONE inline-validated input for the global hard container memory cap, dismisses the canonical size / `""` for default / None; the parent persists via `settings_registry.set_container_memory` and reminds to recreate), overlay_select (the Overlay… image-variant picker — Project menu `i`; lists config.OVERLAYS with the current marked, dismisses the chosen overlay (None on cancel/no-op), applied off-thread via lifecycle.recreate(overlay=…)), model_pin (the Model… picker — Project menu `m`; ONE unified list for the project's one model choice: the curated CLAUDE models (models/claude_models.py — picked → `Project.claude_model`, launched as `claude --model <ref>` by spawn_claude; registry-only, applies at the NEXT launch, no recreate, allowed when LOCKED since no gateway is involved) + the host-Ollama installed models (live, off-thread, fail-open — picked → the Phase 9 hybrid local pin, recreate-to-apply) + a default/unpin row + a raw input (`is_claude_ref` disambiguates a typed claude ref from an ollama tag), the current choice marked; dismisses `ModelPinScreen.CLAUDE + ref` for a claude pick / an ollama tag / `CLEAR` to unpin / None on cancel/no-op — the `""`-is-a-valid-pin case is why CLEAR exists rather than overloading falsy like overlay; setting either pin displaces the other (schema-enforced mutual exclusion), and `_apply_claude_model` recreates ONLY when a claude pick displaces a local pin — reserve-BEFORE-persist like the local flow (`_model_pin_worker(slug, ref, claude=True)` persists in the worker; `lifecycle.recreate` tears the gateway sidecar+net down whenever it recreates into a non-hybrid state, the set_egress-unlock shape), while the registry-only claude paths refuse under a busy slug (an in-flight worker's wholesale `projects.save` would clobber the pin — lost update); local-pin picks still refuse on a strict-egress/LOCKED project — locked+hybrid is deferred (ROADMAP 9c) and `registry.set_model` rejects it, with the TUI `_on_model_pin` + CLI `cmd_project_model_set` pre-checking BEFORE the recreate so a healthy container is never torn down for a state `up` would refuse; unpin stays allowed when locked), profile_select (the Profile… account picker — Project menu `f`; lists registry profiles via profilesview with the project's EFFECTIVE current marked, dismisses the chosen profile name / None on cancel/no-op; applied off-thread via lifecycle.recreate(profile_name=…) which re-seeds identity), auth (the Auth… mode screen — Project menu `a`; shows the project's auth mode + whether a login-minted credential exists, dismisses the TARGET mode ("token"/"login" — the app applies off-thread via set_auth + recreate, the _egress_worker shape) / "logout" (the app runs lifecycle.logout off-thread — refused while running) / None on cancel; the Logout button renders only when a credential exists), profile_switch_confirm (the cross-account re-seed confirm — raised by `_on_profile` ONLY when the pick trips lifecycle.account_mismatch, so the operator acknowledges the identity re-seed (old account's session history stays) BEFORE recreate forces past the guard; dismisses "force"/None — same "recreate refuses cleanly before any teardown/persist" safety as the model_pin locked pre-check, so the running container is untouched until confirmed), git_identity, gh_token, add_key, menu, pull_confirm, delete_project, stop_all_confirm, shutdown, logs, sync_review)
images/                base/Dockerfile (native ~/.local claude install + baked neovim + baked curated bash + baked forge known_hosts: `ssh_known_hosts` copied to /etc/ssh/ssh_known_hosts — github/gitlab/bitbucket/azure PUBLIC host keys so in-container git-over-ssh verifies the forges with no prompt regardless of host SSH history, surviving recreate; non-secret, invariant 1 untouched; issue #4 fix B, per-project TOFU opt-in via `ssh_auto_trust`) + overlays/{python,rust,node,python-node,terraform}.Dockerfile (python-node = polyglot combo: python+uv AND corepack yarn/pnpm in one image, for a node project that also needs python/pip; python deps live in a .venv under /workspace, with pip/uv caches AND uv's downloaded interpreters + tool venvs redirected there via runner._BAKED_ENV so `uv sync` doesn't EROFS on the read-only ~/.local. terraform = infra toolchain (terraform + packer + AWS CLI v2) for the infrastructure/ repo; pinned release zips on a read-only path (AWS CLI is its self-contained bundle, own embedded Python), working state writes ride the /workspace bind, CHECKPOINT_DISABLE + PACKER_*-dir + AWS_CONFIG_FILE/AWS_SHARED_CREDENTIALS_FILE redirects are OVERLAY-scoped ENV (the AWS config/creds files land on the ephemeral .cache tmpfs, NOT the /workspace git checkout, so a creds file never reaches a repo — preferred is env-var creds via a kind="env" env-mount; global _BAKED_ENV untouched), a locked project allowlists registry.terraform.io + releases.hashicorp.com (+ .amazonaws.com for AWS); floor-verified by smoke._overlay_probes)
images/nvim/           curated, no-plugin-manager neovim config baked into the base image (init.lua + after/plugin/curated.lua): TS + Markdown + git-from-nvim + a neo-tree file explorer (<leader>e, pinned to the operator's local LazyVim SHAs) + a mini.starter dashboard (recent files inline — zero-dep, deliberately NOT the picker-driven snacks dashboard). Plugins are native packages (pack/curated/start), treesitter parsers compiled to /opt/nvim-parsers, LSP servers (ts_ls/marksman/jsonls) + prettier on PATH — all baked read-only; nvim writes only shada/state to the .cache tmpfs. No runtime network/Mason. git identity is the injected GIT_CONFIG_* (commits from fugitive/gitsigns carry the right author). Floor unchanged (invariant 2)
images/bash/           curated bash dev environment baked into the base image (Phase 8 — docs/DEVENV.md): bashrc (starship git prompt + history config + eza/zoxide/git aliases + the `n` neovim shortcut + the `hints` banner + a per-project window-title/OSC-11-tint block gated on the injected `CLAUDE_MAN_PROJECT[_TINT]` env — `config terminal-tint on`, default off) + inputrc (↑/↓ history prefix-search + fzf Ctrl-R, `$include /etc/inputrc` first) + starship.toml (the operator's host config plus a per-project prompt chip — an `env_var.CLAUDE_MAN_PROJECT` module that names WHICH project's container the shell is in; hidden on the host where the var is unset) + motd (the 8c shell-open banner: the boot-splash CLAUDE-MAN wordmark in the terracotta→ember gradient — per-row RGB byte-identical to tui/splash.py — + a cheat-sheet (n/ls/lt/g/Ctrl-R/<leader>e) + a DYNAMIC history line (ephemeral vs persistent from CLAUDEMAN_HISTFILE) + the starship git legend; baked at /usr/local/bin/claude-man-motd, shown on every interactive tty shell (the `-t 1` guard keeps non-tty exec probes silent; `CLAUDEMAN_NO_MOTD` opts out), re-shown by `hints`). Sourced by the interactive `docker exec ... bash` (terminals.py); the rc bails on NON-interactive shells FIRST so the exec probes (comm/ssh/gitstate) are untouched. Supporting CLIs (starship/fzf/eza/zoxide/bat/bash-completion) from Trixie apt; `bat` is a /usr/local/bin symlink over Debian's `batcat`. All read-only; the only writes (history/zoxide db/starship cache) go to the .cache tmpfs (ephemeral) UNLESS `config shell-history on` mounts a per-project persistent HISTFILE bind (8d, the one opt-in writable surface — invariant 2). NO claude/opencode alias (invariant 6). Default-path floor byte-identical (image ENV only, no runner change); the image-smoke gate exercises the rc under --read-only
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
- **When adding or changing an overlay, verify the installed tools' CORE OPERATIONS actually run
  under the hardened floor — not just `--version`.** The rootfs is read-only (invariant 2), so a tool
  whose normal use writes to a HOME dotdir (`~/.cache`, `~/.local/share`, `~/.local/bin`, `~/.config`)
  fails with EROFS/ENOSPC at *use* time even though the binary is present and `image smoke` passes.
  This bit us twice — yarn (`~/.cache/yarn` ENOSPC, `~/.yarnrc` EROFS) and uv (`~/.local/share/uv/python`
  EROFS on `uv sync`). So actually exercise the real workflow — `yarn install`, `uv sync`/`uv python
  install`, `cargo build` — in a hardened container (`docker run --read-only --user 1000:1000
  --cap-drop ALL` + the tmpfs/`/workspace` mounts, mirroring `build_create_argv`), then redirect every
  writable path the tool needs onto a writable surface via env vars in `runner._BAKED_ENV` (and the
  base Dockerfile `ENV` for parity): small/ephemeral → the `.cache` tmpfs; large or persistent
  (package caches, downloaded interpreters, tool venvs) → the disk-backed `/workspace` bind. Precedents:
  `YARN_*`, `PIP_CACHE_DIR`, and the `UV_*` dirs (cache/python/bin/tools) all point at writable
  surfaces. Add the check to `image smoke` where it's cheap and network-free. (Known unverified:
  `rust`/cargo's `CARGO_HOME`/`~/.cargo` — audit before relying on the rust overlay for builds.)
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
profile add <name> [--default|--sso|--email ...]   # mint a token via `claude setup-token`
profile renew <name>                               # re-mint an expired token
profile list | verify <name> | usage | seed <name> # accounts: status, account check, token usage, host-config capture
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
project ssh-trust <slug> on|off                      # opt-in (default off) auto-trust of UNKNOWN ssh host keys (TOFU; accept-new). Common forges are pre-trusted by the baked known_hosts regardless. Re-seeds to apply (no recreate); TUI: Env mounts (t). create flag: `--ssh-auto-trust`
project auth <slug> [token|login]                    # per-project claude auth mode (invariant 1): token (default — profile setup-token as env) | login (opt-in: NO token env; /login once in-container mints a self-refreshing credential in the bind → claude.ai account connectors). Show with no mode arg (incl. credential present/absent). Recreate to apply. create flag: `--auth login`. TUI: Project menu -> Auth… (`a`)
project logout <slug>                                # remove a login-minted .credentials.json from the project's bind (refused while running; identity/history stay — `recreate --force` re-seeds, `delete` removes everything). TUI: the Auth… screen's Logout button
project ports add <slug> <container|host:container> [--bind IP] [--proto tcp|udp]   # publish a service port (-p; container ≥1024; default bind 127.0.0.1 host-only; recreate to apply)
project ports rm <slug> <host[/proto]> | ports list # unpublish a port (by host port) or list a project's published ports
packs list [--tier common|node|…]                   # browse the curated pack library (library/packs/ — docs/PACKS.md)
project packs add|rm <slug> <name>                  # select/deselect a curated pack — applies IMMEDIATELY (materialize + sync-in; no recreate; TUI: Project menu -> Packs…)
project packs list <slug> | packs defaults <slug>   # show the selection / re-apply the library defaults for the project's language
model list [--check] | presets                      # installed local models (--check = update-available probe) / the curated coding-model presets (Phase 9 — docs/MODELS.md; host Ollama)
model add <key|tag> | update [<ref>|--all] | rm <ref> | show <ref>   # install (preset key or raw ollama tag, streamed) / re-pull to latest / uninstall / metadata (context, `tools` capability)
project model set <slug> (<ollama-tag> | --claude <model>)           # the per-project model pin (one choice; setting either displaces the other). An ollama tag → HYBRID mode (gateway; recreate-to-apply; refused when locked). --claude <id/alias> (e.g. claude-fable-5, opus) → launch claude with `--model` — applies at the NEXT `project claude`, no recreate, allowed when locked. TUI: Project menu → Model… (`m`)
project model clear <slug> | model show <slug>                       # unpin (→ claude's default, subscription-direct) / show the project's model backend
project create <slug> [--language node|python|rust] # --language picks the pack tier; defaults = `default = true` packs in common/ + <language>/
project lock <slug> | unlock <slug>                 # strict egress on/off (squid allowlist proxy on a no-route net; recreate-to-apply; unlock tears the sidecar+net down; TUI: Project menu -> Egress…, which also edits the allowlist + promotes a blocked host)
project egress-log <slug>                           # destinations a locked project tried to reach but the allowlist BLOCKED (from `docker logs` of the sidecar; the TUI's always-on Network panel shows per-project Blocked/Allowed counts + Traffic)
project egress-smoke <slug>                          # daemon-gated end-to-end check: an allowlisted host reaches + a non-allowlisted host is blocked
image build proxy                                   # (re)build the claude-man:proxy squid sidecar image (standalone; auto-built on first lock)
doctor                                              # host prerequisite checks with per-cause fix hints (docker binary/daemon/socket-permission, claude CLI, base image, terminal, profiles, config); rc 0 unless something FAILs. The TUI setup wizard (auto on a fresh machine; Settings `w`) is the interactive twin
config show                                         # global settings: resolved git identity, gh-token set/none, claude image channel/pin/update-check, terminal/opener, splash, container memory cap, ssh keys/load status
config terminal [--program X | --custom '…' | --auto]   # terminal for shell/claude/nvim windows (built-in launcher table per platform, or an '{argv}' template; TUI: Settings -> e)
config opener [--command '…' | --auto]             # file-manager command for Browse (b)
config splash [on|off]                             # the TUI boot splash (any key skips it)
config shell-history [on|off]                      # persist in-container bash history across recreate (default off; opt-in writable bind — recreate to apply)
config terminal-tint [on|off]                      # per-project OSC-11 background tint on spawned shell/claude windows so parallel projects are distinguishable (default off; recreate to apply)
config memory [LIMIT | --default]                  # the HARD per-container memory cap (--memory X --memory-swap X, always applied — the floor; default 16g, min 1g; a docker size string e.g. 24g/8192m/1.5g; recreate to apply). TUI: Settings (,) -> m
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
