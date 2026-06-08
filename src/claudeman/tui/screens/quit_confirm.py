"""Quit-confirm modal — shown when closing the TUI with containers still running.

Closing the TUI stops every running container so each syncs its assets out (the per-project
asset-sync model). That also closes any detached claude/shell windows, so we confirm first and offer
an escape hatch: *Stop & sync all* (the intended default), *Quit & leave running* (no stop, no sync),
or Cancel. Dismisses the chosen action string (``"stop_all"`` / ``"leave"``) or ``None`` on cancel.
Mirrors ``pull_confirm`` / ``delete_project``.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class QuitConfirmScreen(ModalScreen["str | None"]):
    """Confirm quitting with running containers. Dismisses ``"stop_all"`` | ``"leave"`` | ``None``."""

    BINDINGS = [("escape", "cancel", "Cancel")]
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
                "Stopping syncs each project's assets out (CLAUDE.md + skills/agents) and closes "
                "any detached claude/shell windows.",
                id="quit-note",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Quit & leave running", id="leave")
                yield Button(f"Stop & sync {n}", variant="success", id="stop")

    @on(Button.Pressed, "#stop")
    def _stop(self) -> None:
        self.dismiss("stop_all")

    @on(Button.Pressed, "#leave")
    def _leave(self) -> None:
        self.dismiss("leave")

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
