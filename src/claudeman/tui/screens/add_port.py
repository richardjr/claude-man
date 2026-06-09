"""Add-published-port modal.

Collects a port to publish (``-p``) for a project so a service running INSIDE the container is
reachable for testing. Mirrors ``AddMountScreen``: inline validation that constructs + validates the
``PortMapping`` (so the guards — container ≥1024, valid bind IP, proto — fire in the modal). Dismisses
the validated ``PortMapping`` or ``None`` on cancel; the parent runs ``lifecycle.add_port``.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select

from ...registry.schema import PortMapping, ValidationError


class AddPortScreen(ModalScreen["PortMapping | None"]):
    """Collect a port mapping (container + optional host + bind + proto). Dismisses ``PortMapping`` | ``None``."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    AddPortScreen { align: center middle; }
    #dialog {
        width: 76; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    #port-error { color: $error; height: auto; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str) -> None:
        super().__init__()
        self._slug = slug

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Publish a port for {self._slug}", classes="title")
            yield Label("Container port (the service inside — must be ≥ 1024)")
            yield Input(placeholder="5173", id="container")
            yield Label("Host port (optional — defaults to the container port)")
            yield Input(placeholder="(same as container)", id="host")
            yield Label("Bind IP (127.0.0.1 = host-only; 0.0.0.0 = expose on the LAN)")
            yield Input(value="127.0.0.1", id="bind")
            yield Label("Protocol")
            yield Select([("tcp", "tcp"), ("udp", "udp")], value="tcp", allow_blank=False, id="proto")
            yield Label(
                "Reminder: the service inside must listen on 0.0.0.0 (not container-localhost) for "
                "docker to forward to it. Recreate the container to apply.",
                id="port-hint",
            )
            yield Label("", id="port-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Publish", variant="success", id="add")

    def on_mount(self) -> None:
        self.query_one("#container", Input).focus()

    @on(Input.Changed)
    def _clear_error(self) -> None:
        self.query_one("#port-error", Label).update("")

    @on(Input.Submitted)
    @on(Button.Pressed, "#add")
    def _create(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        err = self.query_one("#port-error", Label)
        container_s = self.query_one("#container", Input).value.strip()
        host_s = self.query_one("#host", Input).value.strip()
        bind = self.query_one("#bind", Input).value.strip() or "127.0.0.1"
        proto = str(self.query_one("#proto", Select).value)
        if not container_s:
            err.update("container port is required")
            return
        try:
            container = int(container_s)
            host = int(host_s) if host_s else 0
        except ValueError:
            err.update("ports must be integers")
            return
        try:
            mapping = PortMapping(container=container, host=host, bind=bind, proto=proto)
        except ValidationError as exc:
            err.update(str(exc))
            return
        self.dismiss(mapping)
