"""Curated-packs checklist screen (Phase 6b — docs/PACKS.md).

A modal (opened from the Project… submenu: ``p`` → ``p``) listing the pack library grouped
*Common* / *<language>* / *Other (selected)* with a toggle checklist. Toggling (space/enter) and
the ``d`` re-apply-defaults action go through ``lifecycle.set_packs`` — registry write +
materialize + asset sync-in, so a change applies IMMEDIATELY (claude reads it at its next
session launch; no recreate). That call is host-local file I/O on small trees (no subprocess),
so it runs inline like the other registry mutations here. A *State* column surfaces drift: a
managed copy that differs from the library (re-stamped + backed up on the next materialize),
an operator collision (operator file wins), or a stale/unknown entry.
"""

from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from ... import lifecycle
from ...packs import library
from ...registry import projects
from .. import packsview

_PACK_COLUMNS = ("Sel", "Pack", "Contents", "State", "Description")
# State column cells — keyed by packsview.STATE_* (STATE_OK renders empty).
_STATE_LABELS = {
    packsview.STATE_STALE: "stale",
    packsview.STATE_DRIFTED: "⚠ drifted",
    packsview.STATE_OPERATOR: "operator file wins",
    packsview.STATE_UNKNOWN: "not in library",
}


class PacksScreen(ModalScreen[None]):
    """Manage ``slug``'s curated-pack selection (toggle / re-apply defaults). Applies immediately."""

    BINDINGS = [
        Binding("space", "toggle", "Toggle"),
        Binding("d", "defaults", "Defaults"),
        Binding("escape", "close", "Close"),
    ]
    CSS = """
    PacksScreen { align: center middle; }
    #dialog {
        width: 100; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #packs { height: auto; max-height: 14; }
    #packs-status { height: auto; color: $text-muted; padding-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str) -> None:
        super().__init__()
        self._slug = slug

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Curated packs · {self._slug}", classes="title")
            yield DataTable(id="packs", cursor_type="row")
            yield Label("space/↵ Toggle · d Defaults · esc Close   (applies immediately — no recreate)",
                        id="packs-status")
            with Horizontal(id="buttons"):
                yield Button("Toggle", variant="success", id="toggle")
                yield Button("Defaults", id="defaults")
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#packs", DataTable).add_columns(*_PACK_COLUMNS)
        self._refresh()
        self.query_one("#packs", DataTable).focus()

    # -- data -------------------------------------------------------------
    def _refresh(self) -> None:
        table = self.query_one("#packs", DataTable)
        cursor = table.cursor_row
        table.clear()
        try:
            project = projects.load(self._slug)
        except Exception as exc:  # noqa: BLE001 - never tear down the app on a bad project load
            self._status(lifecycle.Result(False, f"failed to load {self._slug}: {exc}"))
            return
        lib_err = False
        try:
            lib = library.discover()
            states = packsview.pack_states(project)
        except Exception as exc:  # noqa: BLE001 - fail-soft: still render the selection (deselectable)
            lib, states = {}, {}
            lib_err = True
            self._status(lifecycle.Result(False, f"pack library error: {exc}"))
        rows = packsview.rows(lib, project.packs, project.language, states)
        if not rows:
            if not lib_err:  # don't paint a green "empty" over the library-error status
                self._status(lifecycle.Result(True, "pack library is empty — nothing to select"))
            return
        for row in rows:
            if row.is_header:
                table.add_row("", f"── {row.title} ──", "", "", "", key=row.key)
            else:
                # Text(): descriptions are operator-authored free text — a raw str cell goes
                # through Rich markup, so "[python] …" / "[WIP] …" would vanish (or crash).
                table.add_row("✓" if row.selected else "", row.name, row.contents,
                              _STATE_LABELS.get(row.state, ""), Text(row.description), key=row.key)
        if table.row_count:
            table.move_cursor(row=min(max(cursor, 0), table.row_count - 1))

    def _status(self, res: lifecycle.Result) -> None:
        colour = "$success" if res.ok else "$error"
        # escape(): detail folds in materialize notes + raw exception text (paths, OS errors) —
        # an embedded "[/x]" would otherwise raise MarkupError mid-toggle.
        self.query_one("#packs-status", Label).update(f"[{colour}]{escape(res.detail)}[/]")

    def _selected_pack(self) -> str | None:
        table = self.query_one("#packs", DataTable)
        if table.row_count == 0:
            return None
        try:
            key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:
            return None
        if key is None or key.startswith(packsview.HEADER_KEY_PREFIX):
            return None
        return key

    # -- actions ----------------------------------------------------------
    @on(DataTable.RowSelected)
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value
        if key and not key.startswith(packsview.HEADER_KEY_PREFIX):
            self._toggle(key)

    @on(Button.Pressed, "#toggle")
    def action_toggle(self) -> None:
        name = self._selected_pack()
        if name is None:
            self._status(lifecycle.Result(False, "select a pack row (not a section header)"))
            return
        self._toggle(name)

    def _toggle(self, name: str) -> None:
        try:
            selection = projects.load(self._slug).packs
        except Exception as exc:  # noqa: BLE001
            self._status(lifecycle.Result(False, f"failed to load {self._slug}: {exc}"))
            return
        self._status(lifecycle.set_packs(self._slug, packsview.toggled(selection, name)))
        self._refresh()

    @on(Button.Pressed, "#defaults")
    def action_defaults(self) -> None:
        try:
            project = projects.load(self._slug)
            defaults = library.defaults_for(project.language)
        except library.LibraryError as exc:
            self._status(lifecycle.Result(False, f"pack library error: {exc}"))
            return
        except Exception as exc:  # noqa: BLE001
            self._status(lifecycle.Result(False, f"failed to load {self._slug}: {exc}"))
            return
        self._status(lifecycle.set_packs(self._slug, defaults))
        self._refresh()

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)
