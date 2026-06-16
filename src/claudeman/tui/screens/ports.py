"""Published-ports management screen.

An ``o``-from-the-Project-menu modal that lists a project's published ports and manages them in place —
add (via ``AddPortScreen``) and remove — mirroring ``EnvMountsScreen``. Registry mutations
(``lifecycle.add_port``/``remove_port``) are fast flocked TOML writes, so they run inline. Ports are
fixed at container create, so each result reminds that a ``recreate`` is needed to apply.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ItemGrid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from ... import lifecycle
from ...registry import projects
from .add_port import AddPortScreen

_PORT_COLUMNS = ("Bind", "Host", "Container", "Proto", "Reachable")


class PortsScreen(ModalScreen[None]):
    """Manage ``slug``'s published ports (list / add / remove). Recreate to apply."""

    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("x", "remove", "Remove"),
        Binding("escape", "close", "Close"),
    ]
    CSS = """
    PortsScreen { align: center middle; }
    #dialog {
        width: 88; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #ports { height: auto; max-height: 12; }
    #port-status { height: auto; color: $text-muted; padding-top: 1; }
    /* ItemGrid wraps the action buttons into rows instead of cropping them off the
       dialog's right edge — see CLAUDE.md "TUI dialog button rows" (reflow, no crop). */
    #buttons { height: auto; padding-top: 1; grid-gutter: 0 1; }
    """

    def __init__(self, slug: str) -> None:
        super().__init__()
        self._slug = slug

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Published ports · {self._slug}", classes="title")
            yield DataTable(id="ports", cursor_type="row")
            yield Label("a Add · x Remove · esc Close   (recreate to apply)", id="port-status")
            with ItemGrid(id="buttons", min_column_width=16):
                yield Button("Add", variant="success", id="add")
                yield Button("Remove", variant="error", id="remove")
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#ports", DataTable).add_columns(*_PORT_COLUMNS)
        self._refresh()
        self.query_one("#ports", DataTable).focus()

    # -- data -------------------------------------------------------------
    def _refresh(self) -> None:
        table = self.query_one("#ports", DataTable)
        table.clear()
        try:
            ports = projects.load(self._slug).ports
        except Exception as exc:  # noqa: BLE001 - never tear down the app on a bad project load
            self._status(lifecycle.Result(False, f"failed to load {self._slug}: {exc}"))
            return
        for p in ports:
            if p.error:
                table.add_row("?", "?", "?", "?", f"⚠ INVALID: {p.error}", key=(p.target or "bad"))
                continue
            reach = "host only (localhost)" if not p.exposed else f"LAN ({p.bind})"
            table.add_row(p.bind, str(p.host_eff), str(p.container), p.proto, reach, key=p.target)

    def _status(self, res: lifecycle.Result) -> None:
        colour = "$success" if res.ok else "$error"
        self.query_one("#port-status", Label).update(f"[{colour}]{res.detail}[/]")

    def _selected_target(self) -> str | None:
        table = self.query_one("#ports", DataTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:
            return None

    # -- actions ----------------------------------------------------------
    @on(Button.Pressed, "#add")
    def action_add(self) -> None:
        self.app.push_screen(AddPortScreen(self._slug), self._on_add)

    def _on_add(self, mapping) -> None:
        if mapping is None:
            return
        self._status(lifecycle.add_port(self._slug, mapping))
        self._refresh()

    @on(Button.Pressed, "#remove")
    def action_remove(self) -> None:
        target = self._selected_target()
        if target is None:
            self._status(lifecycle.Result(False, "no published port selected"))
            return
        self._status(lifecycle.remove_port(self._slug, target))
        self._refresh()

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)
