"""Stop-all progress modal — shown while the ``S`` (stop-all) command stops + syncs every running
container, then either stays in the app or quits.

An animated ``LoadingIndicator`` plus a live status line the app updates per container, instead of a
"frozen-looking main screen" while the off-thread stop-all worker runs.

ESCAPE HATCH (``escape``): this modal is normally dismissed BY the worker when it finishes. But if a
single container's stop ever stalls (a wedged/contended docker daemon despite the bounded
``runner.stop``/``egress`` timeouts), a modal with NO way out would eat every key and freeze the whole
TUI ("totally locks up"). ``Esc`` hides the modal and hands control back — the stop-all worker keeps
running in the BACKGROUND (its results still stream to the log), so nothing is cancelled; the operator
just isn't trapped. ``q`` still quits the app immediately regardless.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, LoadingIndicator


class ShutdownScreen(ModalScreen[None]):
    """A spinner + live status while the stop-all worker stops + syncs each running container.

    ``escape`` dismisses it as a safety hatch (the worker continues in the background) — see the
    module docstring. The app clears its stop-all guard in the screen's dismiss callback, so hiding
    the modal this way fully returns the TUI to normal."""

    BINDINGS = [Binding("escape", "hide", "Hide (stopping continues)")]
    CSS = """
    ShutdownScreen { align: center middle; }
    #dialog {
        width: 64; height: auto; padding: 1 2;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #shutdown-status { color: $text-muted; padding-top: 1; }
    #shutdown-hint { color: $text-muted; padding-top: 1; }
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
            yield Label("Esc to hide (stopping continues in the background)", id="shutdown-hint")

    def action_hide(self) -> None:
        self.dismiss()

    def set_status(self, text: str) -> None:
        # Guarded: a `call_from_thread(set_status)` may land after the operator hid the modal (the
        # widget is gone) — a stale update must not raise into the worker.
        try:
            self.query_one("#shutdown-status", Label).update(text)
        except Exception:  # noqa: BLE001 - the screen may already be dismissed
            pass
