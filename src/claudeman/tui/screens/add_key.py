"""Add-ssh-key modal (Settings).

Lets the operator pick a host private key to auto-load into the ssh-agent — either by SELECTING one of
the keys discovered under ``~/.ssh`` (each ``*.pub`` with a private sibling) or by typing an explicit
path. Dismisses the chosen path string (``~`` kept as-typed — expansion happens host-side at load
time), or ``None`` on cancel. The screen never mutates config; the app runs ``lifecycle.add_ssh_key``
(save + immediate ``ssh-add``) off the UI thread.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select

from ... import ssh_agent


class AddKeyScreen(ModalScreen["str | None"]):
    """Pick / type a host private-key path to add to the ssh auto-load list."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    AddKeyScreen { align: center middle; }
    #dialog {
        width: 72; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    #key-error { color: $error; height: auto; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, existing: set[str]) -> None:
        super().__init__()
        self._existing = existing
        # Discovered ~/.ssh private keys not already configured become the picker options.
        self._discovered = [k for k in ssh_agent.discover_ssh_keys() if k not in existing]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Add ssh key (auto-loaded into the agent)", classes="title")
            if self._discovered:
                yield Label("Discovered under ~/.ssh")
                yield Select([(k, k) for k in self._discovered], allow_blank=True, id="discovered")
            else:
                yield Label("No new keys discovered under ~/.ssh — type a path below")
            yield Label("Or a path (~ and $VARS allowed)")
            yield Input(placeholder="~/.ssh/id_ed25519", id="path")
            yield Label("", id="key-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Add", variant="success", id="add")

    def on_mount(self) -> None:
        self.query_one("#path", Input).focus()

    @on(Input.Changed, "#path")
    def _clear_error(self) -> None:
        self.query_one("#key-error", Label).update("")

    @on(Input.Submitted)
    @on(Button.Pressed, "#add")
    def _add(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        typed = self.query_one("#path", Input).value.strip()
        picked = ""
        if self._discovered:
            picked = self.query_one("#discovered", Select).value or ""
        chosen = typed or (picked if isinstance(picked, str) else "")
        err = self.query_one("#key-error", Label)
        if not chosen:
            err.update("pick a discovered key or type a path")
            return
        if chosen in self._existing:
            err.update(f"{chosen} is already configured")
            return
        self.dismiss(chosen)
