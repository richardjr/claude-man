"""Pin a project's local model — Project… menu → ``m``.

Lists the host Ollama daemon's installed models (queried LIVE, off-thread — fail-open) plus a
"subscription-direct" row to UNPIN, with the project's current pin marked. A raw-tag Input lets the
operator pin any ollama tag even when the daemon is unreachable or the model isn't pulled yet — pinning
never requires the model present (the on-``up`` pre-flight warns about an unpulled model), matching
``project model set``.

Dismisses one of:
  • ``None`` — cancel, OR picking the already-current pin (a no-op — nothing to recreate)
  • ``ModelPinScreen.CLEAR`` — unpin → subscription-direct (only when currently pinned)
  • an ollama-tag string — pin that model

The app applies a real change off-thread: ``projects.set_model`` then ``lifecycle.recreate`` (the gateway
sidecar comes up/down at the container boundary — recreate-to-apply, exactly like the overlay/egress
switch). A bare ``None``/``""`` can't carry both "cancel" and "unpin" (``""`` IS a valid pin value —
subscription-direct), so the ``CLEAR`` sentinel disambiguates rather than overloading falsy like the
overlay screen does.
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label

from ...models import get_backend

_COLUMNS = ("Model", "Size", "Params")
_DIRECT_KEY = "\x00direct"  # the subscription-direct (unpin) row's key
_NOTE_KEY = "\x00note"      # the daemon-offline info row's key (not a real selection)


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f}GB" if n else "-"


class ModelPinScreen(ModalScreen["str | None"]):
    """Select a project's local-model pin (an ollama tag, ``CLEAR`` to unpin, or None for cancel/no-op)."""

    CLEAR = "\x00clear"  # dismiss sentinel: unpin → subscription-direct

    BINDINGS = [("escape", "cancel", "Cancel"), ("enter", "choose", "Select")]
    CSS = """
    ModelPinScreen { align: center middle; }
    #dialog {
        width: 80; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #pin-current { color: $text-muted; padding-bottom: 1; }
    #models { height: auto; max-height: 12; }
    #pin-note { height: auto; color: $text-muted; padding-top: 1; }
    #raw { margin-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str, current: str) -> None:
        super().__init__()
        self._slug = slug
        self._current = current
        self._backend = get_backend()

    def compose(self) -> ComposeResult:
        cur = self._current or "subscription-direct (claude.ai only)"
        with Vertical(id="dialog"):
            yield Label(f"Model (local pin) · {self._slug}", classes="title")
            yield Label(f"current: {cur}", id="pin-current")
            yield DataTable(id="models", cursor_type="row")
            yield Label(
                "pinning a local model switches the project to HYBRID mode (claude.ai + the local "
                "model in /model) and recreates the container to apply; needs host Ollama running — "
                "see docs/MODELS.md",
                id="pin-note",
            )
            yield Input(
                placeholder="raw ollama tag, e.g. qwen3-coder:30b (overrides the selection)",
                id="raw",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Select", variant="success", id="choose")

    def on_mount(self) -> None:
        table = self.query_one("#models", DataTable)
        table.add_columns(*_COLUMNS)
        # Paint the unpin row immediately so the screen is usable before the daemon list returns
        # (and so it still works when the daemon is offline). The worker repaints with the models.
        table.add_row("subscription-direct" + (" ←" if not self._current else ""),
                      "claude.ai only", "", key=_DIRECT_KEY)
        table.focus()
        self._load_models()

    @work(thread=True, exclusive=True, group="pin-list")
    def _load_models(self) -> None:
        models, note = self._backend.list_models()
        self.app.call_from_thread(self._paint, models, note)

    def _paint(self, models, note: str) -> None:
        table = self.query_one("#models", DataTable)
        table.clear()
        # The unpin row is ALWAYS present (pre-marked when the project is already subscription-direct).
        table.add_row("subscription-direct" + (" ←" if not self._current else ""),
                      "claude.ai only", "", key=_DIRECT_KEY)
        if note:  # daemon offline/unreachable — the raw-tag Input is still a valid way to pin
            table.add_row(f"(ollama: {note} — type a raw tag below to pin anyway)", "", "", key=_NOTE_KEY)
        for m in models:
            mark = " ←" if m.name == self._current else ""
            table.add_row(f"{m.name}{mark}", _gb(m.size), m.param_size or "-", key=m.name)

    def _picked(self) -> str | None:
        """The selected row's key (a model tag, ``_DIRECT_KEY``, or ``_NOTE_KEY``), or None if no row."""
        table = self.query_one("#models", DataTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:  # noqa: BLE001 - empty table / no cursor
            return None

    @on(DataTable.RowSelected)
    @on(Button.Pressed, "#choose")
    def action_choose(self) -> None:
        raw = self.query_one("#raw", Input).value.strip()
        if raw:
            choice = raw  # a typed tag overrides the selection (pin an un-pulled / offline tag)
        else:
            picked = self._picked()
            if picked in (None, _NOTE_KEY):  # info row / empty table — no real selection
                self.dismiss(None)
                return
            choice = "" if picked == _DIRECT_KEY else picked
        if choice == self._current:  # already pinned to this — nothing to recreate
            self.dismiss(None)
            return
        # An empty choice here means "unpin"; the == check above already handled the already-direct case.
        self.dismiss(self.CLEAR if choice == "" else choice)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
