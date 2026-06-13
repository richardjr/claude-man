"""Add-egress-allowlist-domain modal.

Collects one ``dstdomain`` to add to a locked project's egress allowlist (the squid CONNECT allowlist).
Mirrors ``AddPortScreen``: inline validation (``network.allowlist.is_valid_dstdomain``) so the
over-broad/malformed guard fires in the modal with feedback, not silently at render time. Dismisses the
validated bare/dotted host string or ``None`` on cancel; the parent runs ``lifecycle.add_allow``.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from ...network import allowlist as allowlist_mod


class AddAllowScreen(ModalScreen["str | None"]):
    """Collect one egress allowlist domain. Dismisses the validated host string | ``None``."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    AddAllowScreen { align: center middle; }
    #dialog {
        width: 76; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    #allow-error { color: $error; height: auto; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str) -> None:
        super().__init__()
        self._slug = slug

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Allow an egress domain for {self._slug}", classes="title")
            yield Label("Domain — a bare host (api.example.com) or a leading-dot wildcard "
                        "(.example.com matches the apex + every subdomain)")
            yield Input(placeholder="raw.githubusercontent.com", id="host")
            yield Label("No scheme, port, or path; '.' / a bare TLD are rejected (they'd widen egress). "
                        "Recreate the project to apply (squid.conf re-renders).", id="allow-hint")
            yield Label("", id="allow-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Allow", variant="success", id="add")

    def on_mount(self) -> None:
        self.query_one("#host", Input).focus()

    @on(Input.Changed)
    def _clear_error(self) -> None:
        self.query_one("#allow-error", Label).update("")

    @on(Input.Submitted)
    @on(Button.Pressed, "#add")
    def _create(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        err = self.query_one("#allow-error", Label)
        host = self.query_one("#host", Input).value.strip()
        if not host:
            err.update("a domain is required")
            return
        if not allowlist_mod.is_valid_dstdomain(host):
            err.update("not a valid domain — a bare or dotted hostname, no scheme/port/path, "
                       "not '.' or a bare TLD")
            return
        self.dismiss(host)
