"""Deny-a-relay-request modal (Requests screen → ``d``): an optional reason, told to the requesting
agent — the TUI twin of ``claudemanctl relay deny <id> --reason …``. Dismisses the reason (``""`` =
none) or ``None`` on cancel; the parent runs ``relay.engine.deny``."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label


class DenyRequestScreen(ModalScreen["str | None"]):
    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    DenyRequestScreen { align: center middle; }
    #dialog {
        width: 72; height: auto; max-height: 90%;
        padding: 1 2;
        border: round $error; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, request_id: str) -> None:
        super().__init__()
        self._rid = request_id

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Deny request {self._rid}", classes="title")
            yield Label("Reason (optional — shown to the requesting agent)")
            yield Input(placeholder="e.g. not today — the dev box is in use", id="reason")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Deny", variant="error", id="deny")

    def on_mount(self) -> None:
        self.query_one("#reason", Input).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#deny")
    def _deny(self) -> None:
        self.dismiss(self.query_one("#reason", Input).value.strip())

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
