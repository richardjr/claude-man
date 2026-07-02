"""Account-switch confirm modal — shown when a profile swap crosses accounts.

A project's config dir belongs to whatever account first seeded it. Pointing it at a profile for a
*different* account re-seeds the container identity and mixes session state, so ``lifecycle.recreate``
refuses without ``force`` (the work/home cross-contamination guard). The TUI pre-checks the mismatch
and raises this confirm before forcing — the operator explicitly acknowledges the re-seed rather than
seeing an opaque failure in the log.

KEYBOARD-FIRST: ``Enter``/``y`` = Switch & re-seed (the focused default), ``Esc`` = Cancel.
Dismisses ``"force"`` | ``None``.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ProfileSwitchConfirmScreen(ModalScreen["str | None"]):
    """Confirm re-seeding a project's identity to switch to a different account's profile.
    Dismisses ``"force"`` | ``None``."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("y", "force", "Switch"),
    ]
    CSS = """
    ProfileSwitchConfirmScreen { align: center middle; }
    #dialog {
        width: 76; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #switch-note { color: $text-muted; padding-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str, profile: str, existing_email: str, target_email: str) -> None:
        super().__init__()
        self._slug = slug
        self._profile = profile
        self._existing = existing_email
        self._target = target_email or "(no account email)"

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Switch account for {self._slug}?", classes="title")
            yield Label(
                f"this project's config belongs to [b]{self._existing}[/]  →  "
                f"profile [b]{self._profile}[/] is [b]{self._target}[/]"
            )
            yield Label(
                "Switching re-seeds the container identity for the new account. The previous "
                "account's session history stays in the config dir.\n"
                "[b]Enter[/]/[b]y[/] = Switch & re-seed   ·   [b]Esc[/] = Cancel",
                id="switch-note",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Switch & re-seed", variant="warning", id="force")

    def on_mount(self) -> None:
        self.query_one("#force", Button).focus()  # Enter confirms the switch the operator chose

    @on(Button.Pressed, "#force")
    def action_force(self) -> None:
        self.dismiss("force")

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
