"""Add-env-mount modal (Phase 3.x TUI).

Collects a new ``EnvMount`` (ssh or file) for a project. Mirrors ``AddRepoScreen``: inline validation
that constructs + validates the ``EnvMount`` (so the dest-denylist + src-required guards fire in the
modal), dismissing with the validated ``EnvMount`` or ``None``. The parent manager screen runs the
registry mutation via ``lifecycle.add_mount``.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select

from ...registry.schema import EnvMount, ValidationError


class AddMountScreen(ModalScreen["EnvMount | None"]):
    """Collect kind/src/dst/ro for a new env mount. ``src``/``dst``/``ro`` apply to ``file`` only."""

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
            yield Select([("file", "file"), ("ssh", "ssh")], value="file",
                         allow_blank=False, id="kind")
            yield Label("Host source (file only — ~ and $VARS expanded)")
            yield Input(placeholder="~/.netrc", id="src")
            yield Label("Container dest (file only — absolute, outside the managed mounts)")
            yield Input(placeholder="/home/agent/.netrc", id="dst")
            yield Checkbox("read-only (file)", value=True, id="ro")
            yield Label("", id="mount-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Add", variant="success", id="add")

    def on_mount(self) -> None:
        self.query_one("#kind", Select).focus()

    # -- handlers ---------------------------------------------------------
    @on(Input.Changed)
    @on(Select.Changed)
    def _clear_error(self) -> None:
        self.query_one("#mount-error", Label).update("")

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
            self.dismiss(EnvMount(kind="ssh"))
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
        self.dismiss(mount)
