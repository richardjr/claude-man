# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/); versioning is pre-1.0 (breaking changes
may land in minor versions until 1.0).

## [Unreleased]

_Nothing yet._

## [0.1.0] — 2026-06-30

First tagged release. Phases 0–6 + 8 of [`ROADMAP.md`](ROADMAP.md): multi-account OAuth profiles,
hardened per-project containers, the repos / env-mount / ports lifecycle, asset sync, curated packs,
strict egress, the baked dev shell + neovim, on-start claude update checks, and review-gated
sync-back. Installable as a self-contained wheel (`uv tool install` / `pipx`) or run from a checkout.

### Added
- **Self-contained packaging**: the wheel now bundles the Dockerfiles + image assets (`images/`) and
  the curated pack library (`library/`) under `claudeman/_data/`, so an installed copy works with no
  source checkout — `uv build` then `uv tool install dist/*.whl` (or `pipx install`). `config._data_root`
  resolves the packaged data in a wheel and falls back to the repo root from a checkout, so
  `uv run` from a clone is unchanged.
- **Per-use-case setup guides** ([`docs/SETUP-GUIDES.md`](docs/SETUP-GUIDES.md)): copy-pasteable
  recipes with a CLI track and a TUI track each — a universal create→up→repo→shell loop, reusable
  add-ons (SSH agent-forwarding, AWS credentials as env vars, env vars / config files / `gh` token),
  and guides for Node / Python / polyglot / Rust / **Terraform + AWS**, strict egress, and a local
  model. Linked from the README.
- **AWS CLI v2 in the `terraform` overlay**: pinned, self-contained bundle on a read-only system path;
  `AWS_CONFIG_FILE`/`AWS_SHARED_CREDENTIALS_FILE` redirected onto the writable `.cache` tmpfs (off the
  `/workspace` git checkout) so `aws configure` works under the floor. Verified by `image smoke
  terraform`. Preferred credential path is env vars via a `kind="env"` env-mount.
- **Multi-agent provider abstraction design** ([`docs/AGENTS.md`](docs/AGENTS.md), ROADMAP Phase 7):
  a plan for running a different coding agent (e.g. the OpenAI Codex CLI) in the same hardened
  container model via an `AgentProvider` seam, with the security floor enforced identically for every
  provider. Design only — not started.
- **Live container-log viewer** (Phase 1 / TUI-5): View… → **Logs** opens a near-full-screen
  `LogsScreen` that streams a project's `docker logs --tail 200 --timestamps -f` into a `RichLog`
  via an off-UI-thread follower worker. The argv is a pure, unit-tested `runner.build_logs_argv`;
  the follower subprocess is reaped (`terminate`→`kill`) when the screen is dismissed or the app
  exits, so no `docker logs -f` leaks. Read-only (it never writes to the container).
- **Sync-back** (Phase 5, invariant 5): a review-gated three-way merge that flows accepted
  in-container `~/.claude` changes (new skills/agents/commands, edited `settings.json`) back into
  the operator's real global host `~/.claude`, so improvements made in one project benefit all.
  The denylist is enforced **before any read** and **again at git-staging**; every host target is
  **backed up before** an overwrite (refuse on backup failure — nothing lost); `settings.json` is an
  **inverse field-patch** (host `hooks`/`statusLine` structurally immune, denied keys skipped); MCP
  is detected/diffed but **gate-only** (apply deferred); accepted file artifacts are committed to a
  state-tier audit repo (`config.sync_audit_dir()`) for free revert history; the merge runs under a
  single **global** lock. Diffs are secret-masked (key-name **and** value-shape: `sk-`/`ghp_`/JWT/
  long-base64). A three-way `baseline.json` is captured once on first `up` (after sync-in, before
  start) and refreshed from real on-disk state after each merge; `stop` prints a pending-changes
  nudge. CLI: `sync plan <slug>` (dry-run masked diffs), `sync review <slug> [--yes]`. TUI: projects
  table `y` → `SyncReviewScreen` (per-row accept/reject/skip/cycle + accept-all-non-reject).
- **Lockable strict egress** (Phase 4, invariant 3): per-project squid allowlist sidecar
  (`claude-man:proxy`) on a no-route `--internal` network — the agent's only path out is the
  CONNECT-tunnel allowlist proxy (no MITM); the hardened agent floor is byte-identical (the
  strict flags are additive). `up` is fail-closed. CLI: `project lock|unlock`, `project
  egress-log`, `project egress-smoke`, `project create --egress`, `image build proxy`. TUI: Project…
  → Egress… (`g`) for lock/unlock + inline allowlist add/remove + promote a blocked host, and an
  always-on Network panel (per-project Blocked/Allowed counts + whole-container Traffic).
- **Curated packs** (Phase 6a+6b, [`docs/PACKS.md`](docs/PACKS.md)): an in-repo library of
  task-focused CLAUDE.md fragments + skills (`library/packs/<tier>/<pack>/`) that projects
  select as packs, materialized into the per-project asset source and carried into the
  container by the existing asset sync (no new mounts — the hardened floor is untouched).
  Defaults resolve at create from the project's language tier; changes apply immediately, no
  recreate. CLI: `packs list`, `project packs add|rm|list|defaults`, `project create --language`.
  TUI: Project… → Packs… checklist + a Language field on the create form.
- **Baked dev environment** (Phase 8, [`docs/DEVENV.md`](docs/DEVENV.md)): a curated bash shell
  (starship git prompt, history prefix-search + fzf `Ctrl-R`, `n`/eza/zoxide, a shell-open banner)
  and a no-plugin-manager neovim (TS + Markdown LSP, treesitter, git-from-nvim, a file tree) baked
  read-only into the base image; all writes go to the `.cache` tmpfs. Opt-in persistent shell history
  via `config shell-history on` (the one documented, default-off writable surface).
- **TUI boot splash**: a terracotta block-letter logo with a reveal + highlight sweep that scrolls
  up to reveal the live projects table (~2s, any key skips). Disable with `config splash off`.
- **Open-source release hygiene**: MIT `LICENSE`, root `SECURITY.md` (private disclosure policy),
  `CONTRIBUTING.md`, CI (unit tests + ruff on Linux/macOS × Python 3.11/3.12; gitleaks history
  scan), generic config templates.
- **Terminal & opener preferences**: `[terminal]` / `[opener]` in `config.toml`, `claudemanctl config
  terminal|opener`, a TUI Settings picker (`,` → `e`), a built-in launcher table (ghostty, alacritty,
  kitty, wezterm, foot, gnome-terminal, konsole, xterm; Terminal.app + iTerm2 on macOS; Windows
  Terminal on WSL2), and fully custom launchers via an argv template.
- **Hybrid local models** (Phase 9, issue #14): pin a self-hosted Ollama model on a project for a
  per-project LiteLLM gateway sidecar that fronts both the claude.ai subscription (passthrough — no
  API key injected) and the local model in one `/model` picker. `claudemanctl model …` manages host
  models; `project model set|clear`. The Claude-subscription passthrough is verified live.
- **macOS support** (Docker Desktop ssh-agent socket, `open` opener, Terminal.app fallback;
  `hostplatform.py` centralises the per-host seams) and **Windows via WSL2** (Windows Terminal
  launcher, `wslview`/`explorer.exe` opener). Native Windows is out of scope.
- **Foundation** (Phases 0–3): multi-account OAuth-token profiles, the fully hardened per-project
  container profile (`--read-only --cap-drop ALL --user 1000:1000`, etc.), the repos / env-mount /
  ports lifecycle, host-side repo checkouts with live git state, per-project asset sync, on-start
  claude update checks, and baked git identity + `gh` + neovim.

### Changed
- **`project recreate` now offers the on-start claude update** (same prompt as `project up`):
  `--update-yes` skips the prompt; `--no-update` skips the check.
- **TUI key scoping**: the global single-key verbs only act from the projects table — the side
  panels (Repos/Profiles/Network/Log) are non-focusable, and the bottom keybar is hidden over any
  modal/sub-menu, so global keys no longer leak into sub-menus.
- **TUI responsive layout**: as the terminal shrinks, the Network, then Token-usage, then Log panels
  drop so the Projects + Repos tables never collapse; Projects has a `min-height` floor.
- **Repos column** is now an aggregate per-flag rollup (e.g. `2 ✓  4 ⚠  1 ~`) instead of a per-repo
  list that truncated for projects with many repos; the per-repo breakdown stays in the Repos panel.
- **Repo-panel branch names** are truncated to 40 chars (trailing ellipsis).

### Security
- **SEC-6**: project slugs / profile names are shape-validated at the CLI argparse boundary and
  re-validated in the terminal spawn path before the keep-open shell string is built.
- **SEC-3**: "one `claude` per container" (invariant 6) is enforced — `spawn_claude` probes the
  container for a live `claude` process and refuses to start a second (fails open on probe errors).

### Fixed
- From a critical-review pass ([`docs/REVIEW.md`](docs/REVIEW.md) 2026-06-14): a **TOCTOU** in the
  OAuth-token write (now one audited `config.write_secret_file`, `O_CREAT` at `0600`); git
  clone/fetch/ff-merge run **non-interactively + time-bounded**; `projects._atomic_write` uses a
  **unique** temp name; `status.query_containers` guards on `docker` being absent; the squid log
  parser drops the literal `-` host of an early-denied request.

### Removed
- **Per-account 5-hour / weekly subscription-usage bars** (`usage_api.py`, the `profile limits` CLI,
  the TUI `5h`/`Week` columns + `refresh_utilization` worker, the `OAUTH_USAGE_*` config): the
  `user:profile` scope the `/api/oauth/usage` endpoint requires cannot be minted via `claude
  setup-token`, so the bars never worked. Token minting reverted to `setup-token`'s default
  `user:inference` scope. The transcript-based per-profile token-totals panel + `profile usage` are
  unaffected.
- Dead code: `projects.load_path`, `config.profile_identity_path`, `gh_token.ENV_NAME` (no callers).

[Unreleased]: https://github.com/richardjr/claude-man/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/richardjr/claude-man/releases/tag/v0.1.0
