"""Pure view model for the Packs screen (Phase 6b).

Two pieces, both textual-free (unit-tested dependency-free, the same pattern as
``splash``/``rowfx``):

- ``pack_states`` — the read-only freshness map behind the State column: each SELECTED pack
  compared against the library through the manifest's ours/theirs boundary (the one
  ``materialize.refresh`` enforces), via materialize's public ``load_manifest`` /
  ``desired_files`` + ``library.file_hash``. Read-only — the materializer itself stays the
  only writer.
- ``rows`` — the grouped checklist the ``PacksScreen`` modal renders: a *Common* section, the
  project's *<language>* tier section, then an *Other (selected)* section catching selections
  outside those tiers — cross-tier CLI adds and names that have outlived the library — so
  every stored selection is always visible and de-selectable (a toggle saves the FULL list; a
  hidden entry would be silently dropped).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import config
from ..packs import library
from ..packs.materialize import desired_files, load_manifest
from ..registry.schema import Project

HEADER_KEY_PREFIX = "#hdr:"  # table row keys for section headers — never toggleable

# Pack freshness states for the State column. Worst-wins ordering when a pack's files
# disagree: a drifted file (an edit that will be overwritten + backed up) outranks an
# operator collision (the pack entry won't apply), which outranks a merely
# stale/unmaterialized copy.
STATE_OK = ""
STATE_STALE = "stale"          # asset copy missing, or library moved on — refreshed next materialize
STATE_OPERATOR = "operator"    # un-manifested operator file in the way — operator wins, entry skipped
STATE_DRIFTED = "drifted"      # managed copy edited — curated-wins: re-stamped + backed up
STATE_UNKNOWN = "unknown"      # selection entry not in the library — skipped at materialize
_STATE_RANK = {STATE_OK: 0, STATE_STALE: 1, STATE_OPERATOR: 2, STATE_DRIFTED: 3}


def pack_states(project: Project, *, root: Path | None = None) -> dict[str, str]:
    """Freshness of each SELECTED pack vs the library: name -> ``STATE_*`` (worst file wins).

    Per-file I/O faults never raise — an unreadable copy degrades to a state (the read-only
    probe must not be more fragile than the ``refresh`` it mirrors). ``library.LibraryError``
    on a malformed library still propagates — the screen wraps fail-soft."""
    lib = library.discover(root)
    manifest = load_manifest(project.slug)
    assets_root = config.project_assets_dir(project.slug)
    out: dict[str, str] = {}
    for name in project.packs:
        if name not in lib:
            out[name] = STATE_UNKNOWN
            continue
        desired, _notes = desired_files((name,), lib)
        worst = STATE_OK
        for key, src in desired.items():
            dst = assets_root / key
            try:
                want = library.file_hash(src)
            except OSError:
                state = STATE_STALE  # unreadable library source — refresh will note it
            else:
                try:
                    have = library.file_hash(dst) if dst.is_file() else None
                except OSError:
                    # Unreadable asset copy: a manifested one is ours-but-unverifiable (refresh
                    # will surface the fault as drift handling); a foreign one is a collision.
                    state = STATE_DRIFTED if key in manifest else STATE_OPERATOR
                else:
                    if have == want:
                        state = STATE_OK
                    elif have is None:
                        state = STATE_STALE
                    elif key not in manifest:
                        state = STATE_OPERATOR
                    elif have != manifest[key]:
                        state = STATE_DRIFTED
                    else:
                        state = STATE_STALE
            if _STATE_RANK[state] > _STATE_RANK[worst]:
                worst = state
        out[name] = worst
    return out


@dataclass(frozen=True)
class Row:
    """One table row: a section header (``is_header``) or a toggleable pack entry."""

    key: str                 # DataTable row key: the pack name, or HEADER_KEY_PREFIX + title
    is_header: bool = False
    title: str = ""          # header rows only
    name: str = ""           # pack rows only ↓
    selected: bool = False
    contents: str = ""       # Pack.summary ("2 md + 1 skill")
    state: str = ""          # STATE_* ("" = in sync)
    description: str = ""


def _header(title: str) -> Row:
    return Row(key=f"{HEADER_KEY_PREFIX}{title}", is_header=True, title=title)


def _pack_row(pack: library.Pack, selection: tuple[str, ...], states: dict[str, str]) -> Row:
    prefix = "(default) " if pack.default else ""
    return Row(key=pack.name, name=pack.name, selected=pack.name in selection,
               contents=pack.summary, state=states.get(pack.name, ""),
               description=prefix + pack.description)


def rows(lib: dict[str, library.Pack], selection: tuple[str, ...], language: str,
         states: dict[str, str]) -> list[Row]:
    """The grouped checklist rows: Common, then ``<language>``, then other/unknown selections.

    Packs keep the library's discovery order (tier, then name) within each section; the *Other*
    section keeps selection order. ``states`` is ``pack_states`` output (selected packs only —
    unselected rows have nothing materialized to be stale). ``language == "common"`` (storable —
    the registry shape-validates only) degrades to common-only: the tier is already the first
    section, and a duplicate section would repeat pack names the screen uses as row KEYS."""
    out: list[Row] = []
    if language == library.COMMON_TIER:
        language = ""
    common = [p for p in lib.values() if p.tier == library.COMMON_TIER]
    lang_packs = [p for p in lib.values() if language and p.tier == language] if language else []
    shown = {p.name for p in common} | {p.name for p in lang_packs}
    for title, packs in ((library.COMMON_TIER.capitalize(), common), (language, lang_packs)):
        if not packs:
            continue
        out.append(_header(title))
        out.extend(_pack_row(p, selection, states) for p in packs)
    other = [n for n in selection if n not in shown]
    if other:
        out.append(_header("Other (selected)"))
        for name in other:
            pack = lib.get(name)
            if pack is None:
                out.append(Row(key=name, name=name, selected=True, contents="?",
                               state=states.get(name, STATE_UNKNOWN),
                               description="not in the library — skipped at materialize"))
            else:
                row = _pack_row(pack, selection, states)
                out.append(Row(key=row.key, name=row.name, selected=row.selected,
                               contents=row.contents, state=row.state,
                               description=f"[{pack.tier}] {row.description}"))
    return out


def toggled(selection: tuple[str, ...], name: str) -> tuple[str, ...]:
    """The new selection after toggling ``name``: removed if present, appended if not (append
    keeps registry order, which drives the CLAUDE.md block's ``@``-import order)."""
    if name in selection:
        return tuple(n for n in selection if n != name)
    return selection + (name,)
