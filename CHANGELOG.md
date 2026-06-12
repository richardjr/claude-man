# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/); versioning is pre-1.0 (breaking changes
may land in minor versions until 1.0).

## [Unreleased]

### Added
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
  scrolls up to reveal the live projects table (~1s, any key skips). Disable with
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

## [0.1.0] — unreleased baseline

Phases 0–3 of [`ROADMAP.md`](ROADMAP.md): profiles (multi-account OAuth tokens), hardened
per-project containers, repos/env-mounts/ports lifecycle, asset sync, usage + subscription-limit
bars, on-start claude update checks, baked git/gh/neovim. Phase 4 (strict egress) and Phase 5
(review-gated sync-back) are honest stubs.
