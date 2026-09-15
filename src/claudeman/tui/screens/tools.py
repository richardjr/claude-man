"""Approved-tools checklist screen (docs/TOOLS.md) — Project… menu → ``t``.

A modal listing the tool registry with the project's selection marked. Toggling (space/enter)
edits a LOCAL pending selection; ``a`` Apply dismisses the new selection tuple and the app persists
it + recreates the container off-thread (``lifecycle.set_tools`` then ``lifecycle.recreate`` —
the tools are baked into the image, so a change is a new content-addressed layer built on the
next create; the recreate-to-apply shape of the Overlay/Egress screens). Escape/Close dismisses
``None`` (nothing changed, or changes discarded). Rows the registry pulls in via ``requires``
are marked so the operator sees exactly what the layer will bake.
"""

from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ItemGrid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from ...tools import library
from .. import toolsview

_COLUMNS = ("Sel", "Tool", "Source", "Requires", "Description")


class ToolsScreen(ModalScreen["tuple[str, ...] | None"]):
    """Edit ``slug``'s tool selection; dismisses the new selection on Apply (None = no change)."""

    BINDINGS = [
        Binding("space", "toggle", "Toggle"),
        Binding("a", "apply", "Apply"),
        Binding("escape", "close", "Close"),
    ]
    CSS = """
    ToolsScreen { align: center middle; }
    #dialog {
        width: 110; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #tools { height: auto; max-height: 14; }
    #tools-status { height: auto; color: $text-muted; padding-top: 1; }
    /* ItemGrid wraps the action buttons into rows instead of cropping them off the
       dialog's right edge — see CLAUDE.md "TUI dialog button rows" (reflow, no crop). */
    #buttons { height: auto; padding-top: 1; grid-gutter: 0 1; }
    """

    def __init__(self, slug: str, selection: tuple[str, ...]) -> None:
        super().__init__()
        self._slug = slug
        self._initial = selection
        self._pending = selection
        self._lib: dict[str, library.Tool] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Tools (image layer) · {self._slug}", classes="title")
            yield DataTable(id="tools", cursor_type="row")
            yield Label("", id="tools-status")
            with ItemGrid(id="buttons", min_column_width=16):
                yield Button("Toggle", id="toggle")
                yield Button("Apply (recreate)", variant="success", id="apply")
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#tools", DataTable).add_columns(*_COLUMNS)
        try:
            self._lib = library.discover()
        except Exception as exc:  # noqa: BLE001 - fail-soft: still render the selection (deselectable)
            self._lib = {}
            self._set_status(f"[$error]tool registry error: {escape(str(exc))}[/]")
        self._refresh()
        self.query_one("#tools", DataTable).focus()

    # -- data -------------------------------------------------------------
    def _refresh(self) -> None:
        table = self.query_one("#tools", DataTable)
        cursor = table.cursor_row
        table.clear()
        for row in toolsview.rows(self._lib, self._pending):
            mark = "✓" if row.selected else ("+" if row.pulled_in else "")
            # Text(): descriptions/notes are registry-authored free text — a raw str cell goes
            # through Rich markup, so a "[…]" note would vanish or crash.
            table.add_row(mark, row.name, row.source, row.requires, Text(row.description), key=row.key)
        if table.row_count:
            table.move_cursor(row=min(max(cursor, 0), table.row_count - 1))
        pending = toolsview.changed(self._initial, self._pending)
        line = escape(toolsview.summary(self._lib, self._pending))
        hint = "space/↵ Toggle · a Apply (recreates to bake the new layer) · esc Close"
        self._set_status(f"{line}\n{'[$warning]unapplied changes[/] · ' if pending else ''}{hint}")

    def _set_status(self, markup: str) -> None:
        self.query_one("#tools-status", Label).update(markup)

    def _selected_tool(self) -> str | None:
        table = self.query_one("#tools", DataTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:  # noqa: BLE001 - empty table / no cursor
            return None

    # -- actions ----------------------------------------------------------
    @on(DataTable.RowSelected)
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value:
            self._toggle(event.row_key.value)

    @on(Button.Pressed, "#toggle")
    def action_toggle(self) -> None:
        name = self._selected_tool()
        if name is None:
            return
        self._toggle(name)

    def _toggle(self, name: str) -> None:
        self._pending = toolsview.toggled(self._pending, name)
        self._refresh()

    @on(Button.Pressed, "#apply")
    def action_apply(self) -> None:
        if not toolsview.changed(self._initial, self._pending):
            self.dismiss(None)  # nothing to bake — no recreate
            return
        try:
            library.resolve(self._pending, self._lib)
        except library.LibraryError as exc:
            self._set_status(f"[$error]{escape(str(exc))}[/]")
            return
        self.dismiss(self._pending)

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)
