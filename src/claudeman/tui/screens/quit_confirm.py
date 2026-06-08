"""Quit-confirm modal — shown when closing the TUI with containers still running.

Closing the TUI stops every running container so each syncs its assets out (the per-project
asset-sync model). That also closes any detached claude/shell windows, so we confirm first. The modal
is KEYBOARD-FIRST (the operator pressed ``q``): ``Enter``/``s`` = Stop & sync all (the focused default),
``l`` = Quit & leave running, ``Esc`` = Cancel. Dismisses ``"stop_all"`` / ``"leave"`` / ``None``.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class QuitConfirmScreen(ModalScreen["str | None"]):
    """Confirm quitting with running containers. Dismisses ``"stop_all"`` | ``"leave"`` | ``None``."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("s", "stop", "Stop & sync"),
        Binding("l", "leave", "Leave running"),
    ]
    CSS = """
    QuitConfirmScreen { align: center middle; }
    #dialog {
        width: 76; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #quit-note { color: $text-muted; padding-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, running_slugs: list[str]) -> None:
        super().__init__()
        self._running = list(running_slugs)

    def compose(self) -> ComposeResult:
        n = len(self._running)
        with Vertical(id="dialog"):
            yield Label("Quit claude-man", classes="title")
            yield Label(f"{n} container(s) running: " + ", ".join(self._running))
            yield Label(
                "Stopping syncs each project's assets out (CLAUDE.md + skills/agents) and closes any "
                "detached claude/shell windows.\n"
                "[b]Enter[/]/[b]s[/] = Stop & sync all   ·   [b]l[/] = Quit & leave running   ·   "
                "[b]Esc[/] = Cancel",
                id="quit-note",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Quit & leave running", id="leave")
                yield Button(f"Stop & sync {n}", variant="success", id="stop")

    def on_mount(self) -> None:
        self.query_one("#stop", Button).focus()  # Enter triggers Stop & sync by default

    # -- actions (keyboard) + button handlers share the same dismiss values -----
    @on(Button.Pressed, "#stop")
    def action_stop(self) -> None:
        self.dismiss("stop_all")

    @on(Button.Pressed, "#leave")
    def action_leave(self) -> None:
        self.dismiss("leave")

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
