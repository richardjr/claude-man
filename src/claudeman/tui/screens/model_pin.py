"""Pin a project's model — Project… menu → ``m``.

One unified picker for the project's ONE model choice: the curated CLAUDE models (launched as
``claude --model <ref>`` — applies at the next claude launch, no recreate), the host Ollama
daemon's installed LOCAL models (queried live, off-thread — fail-open; picking one switches the
project to HYBRID mode, recreate-to-apply), and a "default" row to UNPIN, with the project's
current choice marked. A raw Input lets the operator pin any ref even when the daemon is
unreachable or the model isn't pulled/entitled yet — ``models.claude_models.is_claude_ref``
disambiguates a typed claude ref from an ollama tag (the CLI's ``--claude`` flag is the explicit
form). Pinning never requires the model present (the on-``up`` pre-flight warns about an unpulled
local model; an unentitled claude model errors inside claude), matching ``project model set``.

Dismisses one of:
  • ``None`` — cancel, OR picking the already-current choice (a no-op — nothing to apply)
  • ``ModelPinScreen.CLEAR`` — unpin → claude's default, subscription-direct
  • ``ModelPinScreen.CLAUDE + <ref>`` — pin that claude model (``--model`` at launch)
  • an ollama-tag string — pin that local model (hybrid mode)

The app applies a real change off-thread: a local pin (or clearing one) goes ``projects.set_model``
then ``lifecycle.recreate`` (the gateway sidecar comes up/down at the container boundary); a claude
pin is ``projects.set_claude_model`` only — registry-only, no recreate — EXCEPT when it displaces a
local pin (the gateway must still come down, so that path recreates). A bare ``None``/``""`` can't
carry both "cancel" and "unpin" (``""`` IS a valid pin value — the default), so the ``CLEAR``
sentinel disambiguates rather than overloading falsy like the overlay screen does.
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label

from ...models import get_backend
from ...models.claude_models import CLAUDE_MODELS, is_claude_ref

_COLUMNS = ("Model", "Kind", "Info")
_DIRECT_KEY = "\x00direct"  # the default (unpin) row's key
_NOTE_KEY = "\x00note"      # the daemon-offline info row's key (not a real selection)


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f}GB" if n else "-"


class ModelPinScreen(ModalScreen["str | None"]):
    """Select a project's model pin (``CLAUDE + ref`` / an ollama tag / ``CLEAR`` / None)."""

    CLEAR = "\x00clear"      # dismiss sentinel: unpin → claude's default, subscription-direct
    CLAUDE = "\x00claude:"   # dismiss/row-key prefix: a claude --model choice (ref follows)

    BINDINGS = [("escape", "cancel", "Cancel"), ("enter", "choose", "Select")]
    CSS = """
    ModelPinScreen { align: center middle; }
    #dialog {
        width: 84; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #pin-current { color: $text-muted; padding-bottom: 1; }
    #models { height: auto; max-height: 14; }
    #pin-note { height: auto; color: $text-muted; padding-top: 1; }
    #raw { margin-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str, current_local: str, current_claude: str = "") -> None:
        super().__init__()
        self._slug = slug
        self._current_local = current_local
        self._current_claude = current_claude
        self._backend = get_backend()

    def _current_key(self) -> str:
        """The row key matching the project's current choice (mutually exclusive in the schema)."""
        if self._current_local:
            return self._current_local
        if self._current_claude:
            return self.CLAUDE + self._current_claude
        return _DIRECT_KEY

    def compose(self) -> ComposeResult:
        if self._current_local:
            cur = f"local {self._current_local} (hybrid)"
        elif self._current_claude:
            cur = f"claude --model {self._current_claude}"
        else:
            cur = "default (claude picks; subscription-direct)"
        with Vertical(id="dialog"):
            yield Label(f"Model · {self._slug}", classes="title")
            yield Label(f"current: {cur}", id="pin-current")
            yield DataTable(id="models", cursor_type="row")
            yield Label(
                "a CLAUDE model applies at the next claude launch (--model; no recreate). A LOCAL "
                "model switches the project to HYBRID mode (claude.ai + the model in /model), "
                "recreates to apply, and needs host Ollama — see docs/MODELS.md",
                id="pin-note",
            )
            yield Input(
                placeholder="raw ref: a claude id/alias (claude-opus-4-8, opus) or an ollama tag "
                            "(qwen3-coder:30b) — overrides the selection",
                id="raw",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Select", variant="success", id="choose")

    def _mark(self, key: str) -> str:
        return " ←" if key == self._current_key() else ""

    def _static_rows(self, table: DataTable) -> None:
        """The rows that never wait on the daemon: the unpin row + the curated claude models."""
        table.add_row("default" + self._mark(_DIRECT_KEY), "",
                      "claude's own default model (no --model)", key=_DIRECT_KEY)
        for m in CLAUDE_MODELS:
            key = self.CLAUDE + m.ref
            table.add_row(f"{m.ref}{self._mark(key)}", "claude",
                          f"{m.label} — {m.note}" if m.note else m.label, key=key)

    def on_mount(self) -> None:
        table = self.query_one("#models", DataTable)
        table.add_columns(*_COLUMNS)
        # Paint the static rows immediately so the screen is usable before the daemon list returns
        # (and so the claude section still works when the daemon is offline). The worker repaints
        # with the local models appended.
        self._static_rows(table)
        table.focus()
        self._load_models()

    @work(thread=True, exclusive=True, group="pin-list")
    def _load_models(self) -> None:
        models, note = self._backend.list_models()
        self.app.call_from_thread(self._paint, models, note)

    def _paint(self, models, note: str) -> None:
        table = self.query_one("#models", DataTable)
        table.clear()
        self._static_rows(table)
        if note:  # daemon offline/unreachable — the raw Input is still a valid way to pin
            table.add_row(f"(ollama: {note} — type a raw tag below to pin anyway)", "", "",
                          key=_NOTE_KEY)
        for m in models:
            table.add_row(f"{m.name}{self._mark(m.name)}", "local",
                          f"{_gb(m.size)} · {m.param_size or '-'}", key=m.name)

    def _picked(self) -> str | None:
        """The selected row's key (a claude/local key, ``_DIRECT_KEY``, or ``_NOTE_KEY``), or None."""
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
            # A typed ref overrides the selection (pin an un-pulled / offline / unlisted model).
            key = (self.CLAUDE + raw) if is_claude_ref(raw) else raw
        else:
            picked = self._picked()
            if picked in (None, _NOTE_KEY):  # info row / empty table — no real selection
                self.dismiss(None)
                return
            key = picked
        if key == self._current_key():  # already the current choice — nothing to apply
            self.dismiss(None)
            return
        self.dismiss(self.CLEAR if key == _DIRECT_KEY else key)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
