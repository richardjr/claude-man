"""Pick the terminal emulator used for project shell/claude/nvim windows (Settings -> ``e``).

Lists the host platform's launcher table with installed/not-installed status, plus "auto"
(detect — the default) and "custom" (always shown — the escape hatch for a terminal that isn't
in the table, issue #31). Dismisses the chosen program name ("" = auto) or ``None`` on cancel;
the Settings screen persists it via ``settings.set_terminal``, and a "custom" pick opens the
``CustomTerminalScreen`` template editor (prefilled — that's also how an existing template is
edited).
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from .. import terminals

_COLUMNS = ("Terminal", "Status")
_AUTO_KEY = ""


class TerminalSelectScreen(ModalScreen["str | None"]):
    """Select the terminal launcher preference (a name, '' for auto, None on cancel)."""

    BINDINGS = [("escape", "cancel", "Cancel"), ("enter", "choose", "Select")]
    CSS = """
    TerminalSelectScreen { align: center middle; }
    #dialog {
        width: 64; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #terms { height: auto; max-height: 14; }
    #term-note { height: auto; color: $text-muted; padding-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, current: str, custom_command: tuple[str, ...]) -> None:
        super().__init__()
        self._current = current
        self._custom_command = custom_command

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Terminal for shell/claude/nvim windows", classes="title")
            yield DataTable(id="terms", cursor_type="row")
            yield Label(
                "terminal not listed? pick `custom` and define its launcher template",
                id="term-note",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Select", variant="success", id="choose")

    def on_mount(self) -> None:
        table = self.query_one("#terms", DataTable)
        table.add_columns(*_COLUMNS)
        mark = " ←" if not self._current else ""
        table.add_row(f"auto (detect){mark}", "", key=_AUTO_KEY)
        for spec in terminals.platform_terminals():
            mark = " ←" if spec.name == self._current else ""
            status = "installed" if spec.available() else "not installed"
            table.add_row(f"{spec.name}{mark}", status, key=spec.name)
        mark = " ←" if self._current == terminals.CUSTOM_PROGRAM else ""
        template = (" ".join(self._custom_command) if "{argv}" in self._custom_command
                    else "(define a launcher template)")
        table.add_row(f"custom{mark}", template, key=terminals.CUSTOM_PROGRAM)
        table.focus()

    def _selected(self) -> str | None:
        table = self.query_one("#terms", DataTable)
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:  # noqa: BLE001 - empty table / no cursor
            return None

    @on(DataTable.RowSelected)
    @on(Button.Pressed, "#choose")
    def action_choose(self) -> None:
        choice = self._selected()
        if choice is None:
            return
        self.dismiss(choice)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
