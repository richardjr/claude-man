"""Stop-all confirm modal — the end-of-day "stop + sync every running container" command.

Decoupled from quitting: pressing ``q`` now exits immediately and leaves containers running.
This screen is the deliberate batch stop, reached via the top-level ``S`` binding. Stopping each
container runs its asset sync-out (CLAUDE.md + skills/agents → the synced config tier) and closes any
detached claude/shell windows, so we confirm first. KEYBOARD-FIRST: ``Enter``/``q`` = Stop, sync &
quit (the focused default — the end-of-day one-shot), ``s`` = Stop & sync (stay in the app),
``Esc`` = Cancel. Dismisses ``"stop_quit"`` / ``"stop_stay"`` / ``None``.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class StopAllConfirmScreen(ModalScreen["str | None"]):
    """Confirm stopping + syncing all running containers. Dismisses ``"stop_quit"`` | ``"stop_stay"``
    | ``None``."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "stop_quit", "Stop, sync & quit"),
        Binding("s", "stop_stay", "Stop & sync"),
    ]
    CSS = """
    StopAllConfirmScreen { align: center middle; }
    #dialog {
        width: 76; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #stop-all-note { color: $text-muted; padding-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, running_slugs: list[str]) -> None:
        super().__init__()
        self._running = list(running_slugs)

    def compose(self) -> ComposeResult:
        n = len(self._running)
        with Vertical(id="dialog"):
            yield Label("Stop all + sync", classes="title")
            yield Label(f"{n} container(s) running: " + ", ".join(self._running))
            yield Label(
                "Stops each container and syncs its assets out (CLAUDE.md + skills/agents) to the "
                "synced config tier. This closes any detached claude/shell windows.\n"
                "[b]Enter[/]/[b]q[/] = Stop, sync & quit   ·   [b]s[/] = Stop & sync (stay)   ·   "
                "[b]Esc[/] = Cancel",
                id="stop-all-note",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(f"Stop & sync {n}", id="stop_stay")
                yield Button("Stop, sync & quit", variant="success", id="stop_quit")

    def on_mount(self) -> None:
        self.query_one("#stop_quit", Button).focus()  # Enter triggers the end-of-day one-shot

    # -- actions (keyboard) + button handlers share the same dismiss values -----
    @on(Button.Pressed, "#stop_quit")
    def action_stop_quit(self) -> None:
        self.dismiss("stop_quit")

    @on(Button.Pressed, "#stop_stay")
    def action_stop_stay(self) -> None:
        self.dismiss("stop_stay")

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
