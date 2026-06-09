"""Add-env-mount modal (Phase 3.x TUI).

Collects a new env mount (ssh / file / env var) for a project. Mirrors ``AddRepoScreen``: inline
validation that constructs + validates the ``EnvMount`` (so the guards fire in the modal). Dismisses
``(EnvMount, value)`` — ``value`` is the (masked) secret for a ``kind="env"`` var, else ``None`` — or
``None`` on cancel. The parent runs ``lifecycle.add_mount`` (ssh/file) or ``add_env_var`` (env).
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select

from ...registry.schema import EnvMount, ValidationError

# What the screen hands back: (mount, value) — value is the env-var secret for kind="env", else None.
AddMountResult = "tuple[EnvMount, str | None]"


class AddMountScreen(ModalScreen["tuple[EnvMount, str | None] | None"]):
    """Collect a new env mount. file → src/dst/ro; env → name + (masked) value; ssh → nothing."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    AddMountScreen { align: center middle; }
    #dialog {
        width: 76; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    /* Per-kind field groups; toggled by the Kind selector (see _apply_kind). */
    #file-fields, #env-fields { height: auto; }
    #ssh-hint { padding-top: 1; }
    #mount-error { color: $error; height: auto; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str) -> None:
        super().__init__()
        self._slug = slug

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Add env mount to {self._slug}", classes="title")
            yield Label("Kind")
            yield Select([("file", "file"), ("ssh", "ssh"), ("env var", "env")], value="file",
                         allow_blank=False, id="kind")
            # Per-kind field groups — only the selected kind's group is shown (see _apply_kind).
            with Vertical(id="file-fields"):
                yield Label("Host source (~ and $VARS expanded)")
                yield Input(placeholder="~/.netrc", id="src")
                yield Label("Container dest (absolute, outside the managed mounts)")
                yield Input(placeholder="/home/agent/.netrc", id="dst")
                yield Checkbox("read-only", value=True, id="ro")
            with Vertical(id="env-fields"):
                yield Label("Env var name")
                yield Input(placeholder="MY_TOKEN", id="name")
                yield Label("Env var value (hidden; stored 0600, injected pass-through)")
                yield Input(password=True, placeholder="value", id="value")
            yield Label(
                "ssh forwards your host ssh-agent (keys stay on the host) and seeds "
                "~/.ssh/{config,known_hosts}. No other input needed — just Add.",
                id="ssh-hint",
            )
            yield Label("", id="mount-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Add", variant="success", id="add")

    def on_mount(self) -> None:
        self._apply_kind(self.query_one("#kind", Select).value)  # show only the default kind's fields
        self.query_one("#kind", Select).focus()

    def _apply_kind(self, kind: object) -> None:
        """Show only the field group(s) relevant to the selected Kind (file → src/dst/ro,
        env → name/value, ssh → just a hint). Hidden groups take no layout space, so the modal
        shrinks to the chosen kind."""
        self.query_one("#file-fields").display = kind == "file"
        self.query_one("#env-fields").display = kind == "env"
        self.query_one("#ssh-hint").display = kind == "ssh"

    # -- handlers ---------------------------------------------------------
    @on(Input.Changed)
    def _clear_error(self) -> None:
        self.query_one("#mount-error", Label).update("")

    @on(Select.Changed, "#kind")
    def _kind_changed(self, event: Select.Changed) -> None:
        self.query_one("#mount-error", Label).update("")
        self._apply_kind(event.value)

    @on(Input.Submitted)
    @on(Button.Pressed, "#add")
    def _create(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        err = self.query_one("#mount-error", Label)
        kind = self.query_one("#kind", Select).value
        if kind == "ssh":
            self.dismiss((EnvMount(kind="ssh"), None))
            return
        if kind == "env":
            name = self.query_one("#name", Input).value.strip()
            value = self.query_one("#value", Input).value  # not stripped of internal chars; trim ends
            try:
                mount = EnvMount(kind="env", name=name)  # validates the name (+ rejects forbidden)
            except ValidationError as exc:
                err.update(str(exc))
                return
            if not value.strip():
                err.update("env var needs a value")
                return
            self.dismiss((mount, value.strip()))
            return
        src = self.query_one("#src", Input).value.strip()
        dst = self.query_one("#dst", Input).value.strip()
        ro = self.query_one("#ro", Checkbox).value
        if not src:
            err.update("file mount needs a host path (src)")
            return
        try:
            mount = EnvMount(kind="file", src=src, dst=dst, ro=ro)
        except ValidationError as exc:
            err.update(str(exc))
            return
        self.dismiss((mount, None))
