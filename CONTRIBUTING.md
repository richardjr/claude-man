# Contributing to claude-man

Thanks for considering a contribution. claude-man is a security-sensitive tool — it exists to keep
an autonomous agent inside a hardened sandbox and credentials out of it — so the bar for changes
that touch the container profile, auth, or sync paths is deliberately high.

## Before anything else: the invariants

Read the **"Load-bearing invariants"** section at the top of [`CLAUDE.md`](CLAUDE.md). Every change
must preserve all six (credential isolation, the hardened container floor, network-layer egress,
registry-as-truth, the sync-back denylist, one-claude-per-container). A PR that relaxes one of them
"to make something work" will be declined — fix the writable-mount set or the image instead, and
re-run `claudemanctl image smoke`.

[`docs/SECURITY.md`](docs/SECURITY.md) explains the threat model behind them, and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) the overall design. Suspected vulnerabilities go
through [`SECURITY.md`](SECURITY.md) (privately), not the issue tracker.

## Dev setup

```bash
git clone https://github.com/richardjr/claude-man.git
cd claude-man
uv sync                          # Python ≥ 3.11, managed by uv (no pip/poetry)
uv run python -m unittest        # the test suite — must pass, needs NO docker/network
uvx ruff@latest check src tests  # lint — must be clean
```

Building/smoking the hardened image needs a docker daemon and is not part of CI:

```bash
uv run claudemanctl image build base
uv run claudemanctl image smoke base   # the hardened-profile gate — run it after image/runner changes
```

## Conventions (enforced in review)

- **Tests stay dependency-free**: stdlib `unittest` (plus `tomlkit`, which the TOML-writing
  paths use), no docker, no network, no `textual`.
  Tests isolate state via `CLAUDE_MAN_CONFIG_HOME` / `CLAUDE_MAN_STATE_HOME` tmpdirs and pin the
  host platform explicitly (mock `hostplatform` predicates) so the suite passes identically on the
  Linux and macOS CI legs.
- **Keep `textual` imports inside `tui/`** so the CLI and tests import without it installed.
- **Subprocess calls use explicit argv lists** (never `shell=True`). Anything that renders an argv
  (docker create, terminal launch, probes) is a **pure function** with unit tests pinning its exact
  shape — `docker/runner.py::build_create_argv` is the canonical example.
- **TOML**: read with stdlib `tomllib`, write with `tomlkit` (comment-preserving). Registry/settings
  schema changes must be **additive and backward compatible** — an existing operator's config must
  load with identical behaviour; a hand-edited bad value coerces to a safe default at load rather
  than bricking the TUI (see the channel/terminal coercions in `registry/settings.py`).
- **Unimplemented roadmap phases** raise `NotImplementedError("phase N: …")` referencing
  [`ROADMAP.md`](ROADMAP.md) — keep stubs honest rather than silently no-op.
- **Platform branches** go through `hostplatform.py` (pure functions over an explicit `platform`
  arg), never inline `sys.platform` checks scattered through the code. Linux is the reference
  platform; macOS and WSL2 are supported hosts; native Windows is out of scope.
- Commit messages: short, factual subject ("what changed", not "why"); optional one-paragraph
  body; no marketing language or emojis.

## Adding or editing a curated pack

The pack library (`library/packs/`) is part of the codebase — curation is "edit the file, commit"
(freshness is content-hashed; there are no version bumps). See [`docs/PACKS.md`](docs/PACKS.md)
for the full design.

```
library/packs/<tier>/<pack>/
  pack.toml             # description = "…" (required); default = true|false (default false)
  claude-md/*.md        # CLAUDE.md fragments — one concern per file
  skills/<name>/        # full skill dirs (SKILL.md + support files)
```

- **Tiers are directories** — `common/` plus language names (`node/`, `python/`, …). Adding a new
  language tier is just adding the directory; nothing else to register.
- **Names are slug-shaped and library-unique** across tiers (they become directory names inside
  the container). The shipped library is linted by `tests/test_packs_library.py`, which imports
  the real tree — a malformed pack (missing description, empty pack, duplicate name, invalid
  skill name) fails the suite.
- `default = true` means the pack is auto-selected for **new** projects in the tier's scope
  (common defaults apply to everyone; a language default applies to projects created with that
  `--language`). Defaults are resolved at create and written explicitly, so flipping the flag
  never changes existing projects.
- **The repo is public** — house rules and generic conventions belong in the library; anything
  client- or project-specific stays in the per-project asset source, never here. No secrets, no
  symlinks (skill-tree symlinks are refused at materialize).
- Keep fragments small and single-concern: they're linked into the project `CLAUDE.md` via `@`
  imports, so each file should stand alone.

## Pull requests

1. One logical change per PR, with tests (new behaviour pinned, regressions covered).
2. `python -m unittest` + `ruff check` green locally (CI runs them on Linux + macOS, Python
   3.11/3.12, plus a gitleaks history scan).
3. If you touched `docker/runner.py`, the image, or any mount/env rendering: state in the PR which
   invariant(s) the change interacts with and how the floor is preserved (the argv unit tests
   should show it byte-identical where applicable).
4. Update the docs that assert behaviour you changed (`README.md`, `CLAUDE.md`, `ROADMAP.md`) in
   the same PR.
