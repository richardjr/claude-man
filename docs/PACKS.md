# Curated packs — skills + CLAUDE.md injectors (Phase 6 design)

Status: **6a + 6b implemented** (6a 2026-06-11 — library + schema + materializer + CLI verbs +
the `/workspace` launch default; 6b 2026-06-12 — TUI Packs screen + drift surfacing +
create-modal Language field). Remaining: **6c** (deeper curation — port the operator's existing
skills). Tracking: [`ROADMAP.md`](../ROADMAP.md) Phase 6.

## Concept

A **library** of curated, task-focused templates lives in this repo (versioned, reviewed,
improved in one place). A project **selects** entries from it as **packs**. On every start,
claude-man **materializes** the selection into the project's existing asset source and lets the
proven `assets.sync_in` rail carry it into the container binds — the agent picks everything up
with zero in-container mechanism, and the hardened floor is untouched (no new mounts; invariant 2).

A **pack is a bundle**: one directory carrying any mix of CLAUDE.md fragments and skills that
travel together (e.g. the four `guardrails` fragments are one selection). Selection, defaults,
and the UI all operate at pack granularity — a short checklist of meaningful units.

Two content kinds inside a pack:

- **Skills** — full skill dirs (`SKILL.md` + support files) → injected under
  `~/.claude/skills/<name>/`. The asset sync's claude side already allowlists exactly
  `skills/agents/commands` (default-deny), so the security boundary is already built.
- **CLAUDE.md fragments** ("injectors") — focused memory fragments, one concern per file →
  injected as files under `/workspace/.claude-man/<pack>/`, and **linked** from the main
  `/workspace/CLAUDE.md` via Claude Code's native `@path` import syntax. The main file stays the
  operator's; claude-man owns only a fenced block of `@` lines inside it.

## Library layout (in this repo)

```
library/packs/
  common/                          # generic tier — applies to any project
    guardrails/                    # ── a pack ──
      pack.toml                    # description, default = true
      claude-md/no-autocommit.md   # never commit/push/PR without explicit instruction
      claude-md/no-destructive-git.md
      claude-md/no-secrets.md
      claude-md/ask-before-deps.md
    code-quality/
      pack.toml                    # default = true
      claude-md/code-quality.md    # match surrounding style, no drive-by reformats, comments
      claude-md/test-discipline.md
    workflow/
      pack.toml                    # default = false
      claude-md/commit-style.md    # short factual subjects, no trailers/emoji
      claude-md/notes-sync.md
  node/                            # language tiers — discovered, not a hardcoded list
    node-conventions/
      pack.toml                    # default = true (within the node tier)
      claude-md/yarn-only.md
  python/
    python-uv/                     # default = true: uv-only, no pip/poetry
  rust/
    rust-cargo/ …
```

- `pack.toml` is minimal for v1: `description` (required), `default` (bool, default false).
  The pack's name is its directory name.
- **Tiers are language names, discovered from the directory layout** — adding a `typescript/`
  tier is just a directory. Tiers are NOT tied to image overlays.
- **Pack names are unique across tiers** (a dependency-free library lint test enforces this), so
  a project's stored selection stays a flat list and tier is purely a curation/defaults concept.
- Freshness identity is a **content hash** — curation is "edit the file, commit"; no manual
  version bumps.
- The repo is public: library templates are published content. House rules and generic
  conventions go in; anything client- or project-specific stays in the per-project asset source.

## Project schema

```toml
language = "node"                       # EXPLICIT field — not inferred from the overlay
packs = ["guardrails", "code-quality", "node-conventions"]
```

- `Project.language` is set at create (TUI create modal gains a Language field; CLI
  `project create --language node`). The create flow may *pre-fill* the suggestion from the
  chosen overlay, but the stored value is explicit and freely editable. Empty = common-only.
- **Defaults are resolved at create** and written explicitly into `packs`: every
  `default = true` pack in `common/` plus every `default = true` pack in `<language>/`.
  Explicit-at-create means no silent behaviour changes later — a new default added to the
  library does not creep into existing projects. `project packs defaults <slug>` (and a button
  on the TUI screen) re-applies the current defaults on demand.

## Architecture

```
packs/library.py      PURE: discover tiers/packs, parse pack.toml,
                      hash content, validate names (uniqueness lint) — dependency-free
packs/materialize.py  selection (registry) -> asset-source writes + the CLAUDE.md
                      fenced-block patch + a state-tier manifest of managed paths/hashes
schema.py             Project.packs + Project.language
lifecycle.up          hook BEFORE assets.sync_in: refresh selection -> sync_in carries it in
```

Flow on `project up`:

1. `materialize.refresh(project)` resolves `project.packs` against the library; for each entry,
   compares the library content hash to the manifest → writes missing/stale copies into
   `assets/<slug>/claude/skills/<name>/` and `assets/<slug>/workspace/.claude-man/<pack>/`.
2. It patches **only** the fenced block in the asset-source `CLAUDE.md` (appended if absent,
   replaced if present, everything outside untouched — same philosophy as the settings.json
   field-patch in invariant 5):

   ```markdown
   <!-- claude-man:packs (managed — edits inside this block are overwritten) -->
   @.claude-man/guardrails/no-autocommit.md
   @.claude-man/guardrails/no-destructive-git.md
   @.claude-man/code-quality/code-quality.md
   <!-- /claude-man:packs -->
   ```

3. The existing `sync_in` carries it into the binds with all its guards (claude-side allowlist,
   denylist, symlink containment, backup-before-overwrite). Because the binds are live host
   dirs, a selection change applies to a *running* container immediately (claude reads it at
   the next session launch).
4. **Manifest** (state tier, per slug — e.g. `packs-manifest.json`): the set of managed
   rel-paths + content hashes. It is what distinguishes "ours to re-stamp" from "theirs to
   never touch":
   - **Deselection** removes exactly the managed paths, never operator-authored files.
   - **Drift** (an agent edited an injected file in-container; `sync_out` carried it back) is
     **curated-wins**: re-stamped from the library on next start, backed up first via the
     existing backup machinery, reported in the start detail. Improvements belong upstream in
     the library — that is the point of curation.
   - **Collision**: if a pack's skill name matches a skill the operator already has in the
     project's asset source (not manifest-managed), the operator's wins and the pack entry is
     skipped with a note.
5. Failure is soft: a bad/missing template skips with a note; it never blocks a start.

## Launch workdir change

`Project.launch_workdir` used to default a lone-repo project into the repo's checkout dir.
It now defaults to **always `/workspace`** (an explicit `workdir` still wins; the lone-repo
auto-cd was dropped in 6a). Note for honesty: Claude Code already traverses upward from cwd, so
`/workspace/CLAUDE.md` is loaded either way — the change is for consistency, not pickup:
`/workspace` becomes the uniform anchor (the injected CLAUDE.md is what you see where you
land), multi- and single-repo projects behave identically, and the agent can see sibling
repos. Operators who prefer landing in the repo set `workdir = "<dir>"` once.

## Operator surface

- **TUI**: Project menu → **Packs…** — a checklist modal grouped *Common* / *<language>*,
  with descriptions; toggling saves to the registry and materializes immediately. A drift/stale
  indicator where a managed copy differs from the library. A "re-apply defaults" action.
- **CLI**: `claudemanctl packs list [--tier common|node|…]` to browse the library;
  `project packs add|rm|list|defaults <slug>`.
- Start detail reports it:
  `packs: 2 refreshed, 1 removed; <path>: drifted from the curated copy — overwritten (backed up)`.

## Phasing

- **6a** — `packs/library.py` + schema (`packs`, `language`) + `packs/materialize.py` +
  lifecycle hook + CLI verbs + the `/workspace` launch default. All pure pieces unit-tested
  dependency-free: pack discovery/parse, name-uniqueness lint, fenced-block patch idempotence,
  manifest diff, deselection/collision safety.
- **6b** — TUI Packs screen + drift surfacing + create-modal Language field.
- **6c** — deeper curation: port the operator's existing skills (e.g. a common `review-skills`
  pack — the fragment starter library, guardrails / code-quality / workflow + node / python /
  rust conventions, shipped in 6a); smoke that a launched claude actually reports the imported
  memory.

## Implementation notes — 6a as built (2026-06-11)

Everything in the design above is implemented as described; the concrete map:

- **Modules:** `src/claudeman/packs/library.py` (pure: `discover()`, `defaults_for(language)`,
  `tiers()`, `file_hash()`; raises `LibraryError` on curation mistakes) and
  `src/claudeman/packs/materialize.py` (`refresh(project)`, the pure `patch_block`/`block_lines`/
  `desired_files`, manifest load/save). Paths: `config.library_packs_dir()` (repo-relative, like
  `images/`) and `config.packs_manifest_path(slug)` (state tier).
- **Schema/registry:** `Project.language` + `Project.packs` (shape-validated only — a stored
  name may outlive the library); `projects.set_packs()` comment-preserving TOML patch;
  `DEFAULT_SYNC_WORKSPACE` gained `".claude-man"` (projects with a hand-written sync list need it
  added — `refresh` notes when it's missing).
- **Lifecycle:** `up()` calls `_packs_refresh` (fail-soft) right before `_sync_in`;
  `create_project(language=…)` resolves + stores the default selection for NEW projects;
  `lifecycle.set_packs(slug, names)` = registry → materialize → sync-in (immediate apply, no
  recreate — used by the CLI, intended for the 6b TUI screen too).
- **CLI:** `packs list [--tier]`, `project packs add|rm|list|defaults <slug>`,
  `project create --language <tier>`.
- **Starter library:** `common/guardrails` + `common/code-quality` (default),
  `common/workflow` (opt-in), `node/node-conventions`, `python/python-uv`, `rust/rust-cargo`
  (default within their tiers). All fragments only — no skills ported yet (that's 6c).
- **Tests:** `tests/test_packs_library.py` (incl. the shipped-library lint),
  `tests/test_packs_materialize.py` (patch idempotence, drift/collision/deselect, bind removal,
  CLAUDE.md adoption), plus registry round-trip + the updated `launch_workdir` pins.

**Migration notes for existing projects:** selections start empty — run
`project packs defaults <slug>` to opt in (explicit-at-create means nothing creeps in);
lone-repo projects now launch at `/workspace` — set `workdir = "<repo-dir>"` to restore the old
landing spot.

## Implementation notes — 6b as built (2026-06-12)

- **`tui/screens/packs.py`** — `PacksScreen`, opened from the Project… submenu (`p` → `p`),
  mirroring `ports.py`/`env_mounts.py`. A `DataTable` checklist grouped *Common* /
  *<language>* / *Other (selected)* — the last section catches cross-tier CLI adds and names
  that have outlived the library, so the FULL stored selection is always visible and
  de-selectable (a toggle saves the whole list; a hidden entry would be silently dropped).
  Space/enter toggles; `d` re-applies `defaults_for(project.language)`; both go through
  `lifecycle.set_packs` (registry → materialize → sync-in — applies immediately, no recreate),
  run inline (host-local file I/O on small trees, no subprocess). Library faults are fail-soft:
  any library fault (malformed `pack.toml` → `LibraryError`, or an unreadable tree → raw
  `OSError`) still renders the selection so packs can be deselected, and per-file I/O faults
  inside `pack_states` degrade to a state instead of raising.
- **`tui/packsview.py`** — the PURE view model behind the screen (no textual/rich imports —
  the `splash`/`rowfx` pattern), so grouping/toggle semantics are unit-tested dependency-free.
- **Drift surfacing** — read-only `packsview.pack_states(project)`: selected pack →
  worst-file state via the manifest's ours/theirs boundary (consuming materialize's public
  `load_manifest`/`desired_files` + `library.file_hash`; the materializer stays the only
  writer). ``stale`` (unmaterialized, or the library moved on), ``drifted`` (managed copy
  edited — will be re-stamped + backed up), ``operator`` (un-manifested collision — operator
  file wins), ``unknown`` (not in the library). Rendered as the State column.
- **Create modal** — `screens/create.py` gained a Language `Select` (options =
  `library.tiers()` minus common, discovered not hardcoded; fail-soft on a malformed library).
  Picking an Overlay pre-fills the matching tier as a suggestion until the operator picks a
  language themselves (the programmatic-echo bookkeeping lives in the screen; the stored value
  is always the explicit selection). `NewProject` is now a 5-tuple ending in `language`,
  threaded through `app._create_project_worker` → `lifecycle.create_project(language=…)`.
- **Tests:** `tests/test_packsview.py` — the `pack_states` state matrix (incl. worst-wins)
  plus grouping, marks, state threading, and toggle order.

**6c:** port the operator's real skills into `library/packs/common/<pack>/skills/…` (remember:
the repo is public) and verify in-container that claude reports the imported memory
(`/memory` shows the `@` imports).
