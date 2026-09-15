"""Pure view model for the Tools screen (docs/TOOLS.md).

Textual-free (the splash/rowfx/packsview pattern — unit-tested dependency-free): the registry as a
checklist of rows with the project's PENDING selection marked, the toggle semantics, and the
"what will actually be baked" summary (selection + the ``requires`` closure). The screen edits a
local copy of the selection and applies it in ONE step (Apply → ``lifecycle.set_tools`` + a
recreate) because every change means a new image layer — unlike packs, which apply per toggle.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..tools import library


@dataclass(frozen=True)
class Row:
    key: str                 # DataTable row key: the tool name
    name: str
    selected: bool           # in the pending selection
    pulled_in: bool          # not selected, but required by a selected tool (baked anyway)
    source: str              # Tool.summary ("apt: jq" / "release 1.37.0")
    requires: str            # comma-joined requires ("" when none)
    description: str         # description + the note, when any


def rows(lib: dict[str, library.Tool], selection: tuple[str, ...]) -> list[Row]:
    """One row per registry tool (library order) + trailing rows for selection entries the
    registry no longer has (kept visible so they can be deselected — an unknown entry blocks the
    image render). ``pulled_in`` marks tools the closure adds without being selected."""
    try:
        closure = {t.name for t in library.resolve(selection, lib)}
    except library.LibraryError:
        closure = set(selection)  # unknown entries: no closure beyond the selection itself
    out: list[Row] = []
    for tool in lib.values():
        desc = tool.description + (f"  [{tool.note}]" if tool.note else "")
        sel = tool.name in selection
        out.append(Row(key=tool.name, name=tool.name, selected=sel,
                       pulled_in=(not sel and tool.name in closure), source=tool.summary,
                       requires=",".join(tool.requires), description=desc))
    for name in selection:
        if name not in lib:
            out.append(Row(key=name, name=name, selected=True, pulled_in=False, source="?",
                           requires="", description="not in the registry — deselect (blocks the image render)"))
    return out


def toggled(selection: tuple[str, ...], name: str) -> tuple[str, ...]:
    """The selection after toggling ``name``: removed if present, appended if not (append keeps
    the operator's order in the registry; the render itself sorts, so order never changes the
    image)."""
    if name in selection:
        return tuple(n for n in selection if n != name)
    return selection + (name,)


def changed(before: tuple[str, ...], after: tuple[str, ...]) -> bool:
    """Whether Apply has anything to do — order is irrelevant (the layer is set-addressed)."""
    return set(before) != set(after)


def summary(lib: dict[str, library.Tool], selection: tuple[str, ...]) -> str:
    """The status line: what the pending selection bakes, or why it can't."""
    if not selection:
        return "no tools selected — the project runs on its plain overlay image"
    try:
        baked = [t.name for t in library.resolve(selection, lib)]
    except library.LibraryError as exc:
        return f"cannot render: {exc}"
    extra = [n for n in baked if n not in selection]
    line = "bakes: " + ", ".join(baked)
    if extra:
        line += f"  (+{', '.join(extra)} pulled in by requires)"
    return line
