"""Allow-a-sender modal (Helpdesk screen → ``a``): pick another registered project that may send
requests to this one — the TUI twin of ``claudemanctl project helpdesk allow``. Dismisses the chosen
slug or ``None``."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select


class AllowSenderScreen(ModalScreen["str | None"]):
    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    AllowSenderScreen { align: center middle; }
    #dialog {
        width: 64; height: auto; max-height: 90%;
        padding: 1 2;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str, choices: list[str]) -> None:
        super().__init__()
        self._slug = slug
        self._choices = choices

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Allow a project to ask {self._slug} for help", classes="title")
            yield Label("Its agent will be told (in its CLAUDE.md) which tasks it can request.")
            yield Select([(c, c) for c in self._choices], value=self._choices[0], allow_blank=False,
                         id="sender")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Allow", variant="success", id="allow")

    def on_mount(self) -> None:
        self.query_one("#sender", Select).focus()

    @on(Button.Pressed, "#allow")
    def _allow(self) -> None:
        self.dismiss(str(self.query_one("#sender", Select).value))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
