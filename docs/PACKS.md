# Curated packs — skills + CLAUDE.md injectors (Phase 6 design)

Status: **proposed** (design agreed 2026-06-11; not yet implemented). Implementation tracking
lives in [`ROADMAP.md`](../ROADMAP.md) Phase 6.

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
    review-skills/
      pack.toml
      skills/<name>/SKILL.md …     # ported from the skills the operator already uses
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
packs/library.py      PURE: discover tiers/packs, parse pack.toml + fragment frontmatter,
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

`Project.launch_workdir` currently defaults a lone-repo project into the repo's checkout dir.
This changes to **always `/workspace`** (an explicit `workdir` still wins; the lone-repo
auto-cd is dropped). Note for honesty: Claude Code already traverses upward from cwd, so
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
  `packs: 2 skills, 4 claude-md (1 refreshed, 1 drift overwritten+backed up)`.

## Phasing

- **6a** — `packs/library.py` + schema (`packs`, `language`) + `packs/materialize.py` +
  lifecycle hook + CLI verbs + the `/workspace` launch default. All pure pieces unit-tested
  dependency-free: pack discovery/parse, name-uniqueness lint, fenced-block patch idempotence,
  manifest diff, deselection/collision safety.
- **6b** — TUI Packs screen + drift surfacing + create-modal Language field.
- **6c** — curate the initial library content: guardrails / code-quality / workflow /
  review-skills (common) + node / python / rust convention packs; smoke that a launched claude
  actually reports the imported memory.
