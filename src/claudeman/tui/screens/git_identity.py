"""Edit the git author identity injected into containers (Settings).

Two fields — ``user.name`` / ``user.email`` — prefilled from the claude-man override (``config.toml``
``[git]``); a blank field inherits the host's own ``git config`` (the placeholder shows the host value).
Dismisses ``(name, email)`` on save (the app persists via ``settings.set_git_identity`` and reminds that
a ``recreate`` is needed to apply), or ``None`` on cancel. Identity is non-secret name/email only.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

# What the screen hands back: (name, email) — blanks mean "inherit host" — or None on cancel.
GitIdentity = tuple[str, str]


class GitIdentityScreen(ModalScreen["GitIdentity | None"]):
    """Collect git user.name / user.email for the container author identity."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    GitIdentityScreen { align: center middle; }
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

    def __init__(self, name: str, email: str, host_name: str = "", host_email: str = "") -> None:
        super().__init__()
        self._name = name
        self._email = email
        self._host_name = host_name
        self._host_email = host_email

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Git identity (injected into containers)", classes="title")
            yield Label("user.name  (blank = inherit host)")
            yield Input(value=self._name, placeholder=self._host_name or "Your Name", id="name")
            yield Label("user.email  (blank = inherit host)")
            yield Input(value=self._email, placeholder=self._host_email or "you@example.com", id="email")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="success", id="save")

    def on_mount(self) -> None:
        self.query_one("#name", Input).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        email = self.query_one("#email", Input).value.strip()
        self.dismiss((name, email))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
