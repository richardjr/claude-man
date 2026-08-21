"""Per-project auth mode screen (Project menu -> ``a`` Auth…).

The TUI face of invariant 1's login amendment: shows the project's auth mode (token — the
default env-injected setup-token — or login — in-container ``/login``-minted credential in the
bind, which enables claude.ai account connectors), plus whether a minted credential currently
exists. Mirrors the Egress… recreate-to-apply flow: the screen dismisses the TARGET mode and the
app applies it off-thread (``set_auth`` + ``recreate``); ``"logout"`` asks the app to remove the
minted credential (``lifecycle.logout`` — refused while the container runs); ``None`` on cancel.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ItemGrid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class AuthScreen(ModalScreen["str | None"]):
    """Dismisses the target auth mode ("token"/"login"), "logout", or None on cancel."""

    BINDINGS = [
        Binding("s", "switch", "Switch mode"),
        Binding("x", "logout", "Logout"),
        Binding("escape", "cancel", "Close"),
    ]
    CSS = """
    AuthScreen { align: center middle; }
    #dialog {
        width: 76; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #auth-body { height: auto; }
    #auth-note { height: auto; color: $text-muted; padding-top: 1; }
    /* ItemGrid wraps the action buttons into rows instead of cropping them off the
       dialog's right edge — see CLAUDE.md "TUI dialog button rows" (reflow, no crop). */
    #buttons { height: auto; padding-top: 1; grid-gutter: 0 1; }
    """

    def __init__(self, slug: str, mode: str, cred_present: bool) -> None:
        super().__init__()
        self._slug = slug
        self._mode = mode
        self._cred_present = cred_present
        self._target = "login" if mode == "token" else "token"

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Auth mode · {self._slug}", classes="title")
            yield Static("\n".join(self._body_lines()), id="auth-body")
            yield Label("s Switch · x Logout · esc Close", id="auth-note")
            with ItemGrid(id="buttons", min_column_width=16):
                yield Button(f"Switch to {self._target}", variant="success", id="switch")
                if self._cred_present:
                    yield Button("Logout", variant="error", id="logout")
                yield Button("Close", id="close")

    def _body_lines(self) -> list[str]:
        lines = [f"Current: [bold]{self._mode}[/]"]
        if self._mode == "login":
            state = ("present (minted by the in-container /login; survives stop/recreate)"
                     if self._cred_present else
                     "absent — open claude in the container (c) and run /login once")
            lines.append(f"Credential: {state}")
        elif self._cred_present:
            lines.append("Credential: a minted login credential remains in the bind — "
                         "Logout removes it")
        lines += [
            "",
            "[bold]token[/] (default) — the profile's setup-token injected as env. "
            "Inference-only scope: claude.ai account connectors are unavailable.",
            "[bold]login[/] (opt-in) — no token env; /login once in-container mints a "
            "self-refreshing credential in this project's bind. Enables claude.ai account "
            "connectors (remote MCP).",
            "",
            "Switching recreates the container to apply (workspace + config binds are kept)."
            + (" Logout requires the project to be stopped." if self._cred_present else ""),
        ]
        return lines

    @on(Button.Pressed, "#switch")
    def action_switch(self) -> None:
        self.dismiss(self._target)

    @on(Button.Pressed, "#logout")
    def action_logout(self) -> None:
        if self._cred_present:
            self.dismiss("logout")

    @on(Button.Pressed, "#close")
    def action_cancel(self) -> None:
        self.dismiss(None)
