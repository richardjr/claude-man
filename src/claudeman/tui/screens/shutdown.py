"""Stop-all progress modal — shown while the ``S`` (stop-all) command stops + syncs every running
container, then either stays in the app or quits.

An animated ``LoadingIndicator`` plus a live status line the app updates per container, instead of a
"frozen-looking main screen" while the off-thread stop-all worker runs. No bindings — it's a transient
progress state (the bounded ``docker stop -t`` + subprocess timeout in ``runner.stop`` guarantee it
can't hang forever; quitting via ``q`` never reaches here — that exits immediately and leaves
containers running).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, LoadingIndicator


class ShutdownScreen(ModalScreen[None]):
    """A spinner + live status while the stop-all worker stops + syncs each running container."""

    CSS = """
    ShutdownScreen { align: center middle; }
    #dialog {
        width: 64; height: auto; padding: 1 2;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #shutdown-status { color: $text-muted; padding-top: 1; }
    LoadingIndicator { height: 1; }
    """

    def __init__(self, total: int) -> None:
        super().__init__()
        self._total = total

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Stopping {self._total} container(s) + syncing assets out …", classes="title")
            yield LoadingIndicator()
            yield Label("", id="shutdown-status")

    def set_status(self, text: str) -> None:
        self.query_one("#shutdown-status", Label).update(text)
