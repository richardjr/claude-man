"""Set/clear the GitHub token injected as ``GH_TOKEN`` into containers (Settings).

A single MASKED input. The current value is never shown — only whether one is set. Dismisses
``("set", token)`` to save a non-empty token, ``("clear", "")`` to remove it, or ``None`` on cancel.
The caller persists it 0600 in the state tier (``gh_token.save``); it is never echoed or put in argv.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

# What the screen hands back: ("set", token) | ("clear", "") | None on cancel.
GhTokenResult = tuple[str, str]


class GhTokenScreen(ModalScreen["GhTokenResult | None"]):
    """Collect (masked) or clear the GitHub token injected as GH_TOKEN."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    GhTokenScreen { align: center middle; }
    #dialog {
        width: 72; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, is_set: bool) -> None:
        super().__init__()
        self._is_set = is_set

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("GitHub token → GH_TOKEN (recreate to apply)", classes="title")
            yield Label(f"current: {'set' if self._is_set else 'not set'}  ·  input hidden, stored 0600")
            yield Input(password=True, placeholder="ghp_… / github_pat_…", id="token")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                if self._is_set:
                    yield Button("Clear", variant="error", id="clear")
                yield Button("Save", variant="success", id="save")

    def on_mount(self) -> None:
        self.query_one("#token", Input).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        token = self.query_one("#token", Input).value.strip()
        self.dismiss(("set", token) if token else None)  # empty input is a no-op (use Clear to remove)

    @on(Button.Pressed, "#clear")
    def _clear(self) -> None:
        self.dismiss(("clear", ""))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
