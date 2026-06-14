# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/); versioning is pre-1.0 (breaking changes
may land in minor versions until 1.0).

## [Unreleased]

### Added
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
  nudge. The security-reviewed copy/backup/symlink-guard primitives are now shared with the asset
  sync via `syncback/fsmerge.py` (one audited implementation). CLI: `sync plan <slug>` (dry-run
  masked diffs), `sync review <slug> [--yes]` (apply defaults). TUI: projects table `y` →
  `SyncReviewScreen` (per-row accept/reject/skip/cycle + accept-all-non-reject, masked-diff pane).
- **Lockable strict egress** (Phase 4, invariant 3): per-project squid allowlist sidecar
  (`claude-man:proxy`) on a no-route `--internal` network — the agent's only path out is the
  CONNECT-tunnel allowlist proxy (no MITM); the hardened agent floor is byte-identical (the
  strict flags are additive). `up` is fail-closed. CLI: `project lock|unlock`, `project
  egress-log` (denied destinations, for allowlist tuning), `project egress-smoke` (daemon-gated
  allow/deny verification), `project create --egress`, `image build proxy`. TUI: Project… →
  Egress… (`g`) for lock/unlock + inline allowlist add/remove + promote a blocked host, and an
  always-on Network panel (per-project Blocked/Allowed distinct-destination counts from the squid
  access log on locked projects + whole-container Traffic from `docker stats` NetIO on every
  project).
- **Curated packs** (Phase 6a+6b, [`docs/PACKS.md`](docs/PACKS.md)): an in-repo library of
  task-focused CLAUDE.md fragments + skills (`library/packs/<tier>/<pack>/`) that projects
  select as packs, materialized into the per-project asset source and carried into the
  container by the existing asset sync (no new mounts — the hardened floor is untouched).
  Defaults resolve at create from the project's language tier; changes apply immediately, no
  recreate. CLI: `packs list`, `project packs add|rm|list|defaults`, `project create
  --language`. TUI: Project… → Packs… checklist (grouped Common / *language*, drift State
  column, `d` re-applies defaults) and a Language field on the create form (pre-filled from
  the Overlay choice). Lone-repo projects now launch at `/workspace` (set `workdir` to
  restore the old landing spot).
- **TUI boot splash**: a terracotta block-letter logo with a reveal + highlight sweep that
  scrolls up to reveal the live projects table (~2s, any key skips). Disable with
  `config splash off`. Frame generation is pure and unit-tested (`tui/splash.py`).
- **Open-source release hygiene**: MIT `LICENSE`, root `SECURITY.md` (private disclosure policy),
  `CONTRIBUTING.md`, CI (unit tests + ruff on Linux/macOS × Python 3.11/3.12; gitleaks history
  scan), generic config templates.
- **Terminal & opener preferences**: `[terminal]` / `[opener]` in `config.toml`,
  `claudemanctl config terminal|opener`, a TUI Settings picker (`,` → `e`), a built-in launcher
  table (ghostty, alacritty, kitty, wezterm, foot, gnome-terminal, konsole, xterm; Terminal.app +
  iTerm2 on macOS; Windows Terminal on WSL2), and fully custom launchers via an argv template
  (`'{argv}'`/`'{title}'`/`'{class}'`). Auto-detection unchanged (ghostty → alacritty → …).
- **macOS support**: same Linux image under Docker Desktop; ssh-agent forwarding via Docker
  Desktop's default-agent socket (`/run/host-services/ssh-auth.sock`); uid-mismatch advisories
  suppressed (bind ownership is synthesised there); `open` as the Browse opener; Terminal.app as
  the zero-install launcher fallback. New `hostplatform.py` centralises the per-host seams.
- **Windows via WSL2**: documented install path; Windows Terminal (`wt`) launcher and
  `wslview`/`explorer.exe` Browse opener auto-detected inside a distro. Native Windows is
  explicitly out of scope.

### Security
- **SEC-6**: project slugs / profile names are now shape-validated at the CLI argparse boundary,
  and re-validated in the terminal spawn path before the keep-open shell string is built.
- **SEC-3**: "one `claude` per container" (invariant 6) is now enforced — `spawn_claude` probes
  the container for a live `claude` process and refuses to start a second (fails open on probe
  errors so a wedged daemon can't lock the operator out).

### Changed
- **`project recreate` now offers the on-start claude update** (same prompt as `project up`): it
  checks the configured channel and, on a TTY, prompts before rebuilding the image to a newer
  claude. `--update-yes` skips the prompt; `--no-update` skips the check.

## [0.1.0] — unreleased baseline

Phases 0–3 of [`ROADMAP.md`](ROADMAP.md): profiles (multi-account OAuth tokens), hardened
per-project containers, repos/env-mounts/ports lifecycle, asset sync, usage + subscription-limit
bars, on-start claude update checks, baked git/gh/neovim. Phase 5 (review-gated sync-back) is an
honest stub; Phase 4 (strict egress) was a stub at this baseline and is delivered in [Unreleased].
