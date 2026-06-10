"""Update-confirm modal — shown on start when a newer claude version is available for a project.

claude-man cannot run ``claude update`` inside the hardened container (the native install lives on the
read-only rootfs). Instead it rebuilds the project's image pinned to the newer version, then recreates
the container on it — a host-side update. Because that rebuild can take a minute (or several, if an
overlay toolchain layer must rebuild), the operator confirms first (the chosen 'prompt before rebuild'
behaviour). KEYBOARD-FIRST (the operator pressed ``s`` to start): ``Enter``/``r`` = Rebuild & start
(the focused default), ``s`` = Start on current, ``Esc`` = Cancel. Dismisses
``"rebuild"`` | ``"skip"`` | ``None``.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class UpdateConfirmScreen(ModalScreen["str | None"]):
    """Confirm rebuilding a project's image to a newer claude before start.
    Dismisses ``"rebuild"`` | ``"skip"`` | ``None``."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("r", "rebuild", "Rebuild"),
        Binding("s", "skip", "On current"),
    ]
    CSS = """
    UpdateConfirmScreen { align: center middle; }
    #dialog {
        width: 76; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #update-note { color: $text-muted; padding-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str, current: str, target: str, *, verb: str = "start") -> None:
        super().__init__()
        self._slug = slug
        self._current = current or "(unbuilt)"
        self._target = target
        self._verb = verb                       # "start" (the `s` path) or "recreate" (the recreate path)

    def compose(self) -> ComposeResult:
        verb = self._verb
        with Vertical(id="dialog"):
            yield Label(f"Update claude for {self._slug}", classes="title")
            yield Label(f"image has claude {self._current}  →  {self._target} is available")
            yield Label(
                f"Rebuilds this project's image to the newer claude and recreates the container on it "
                "(a host-side update — `claude update` can't run inside the read-only container). The "
                "rebuild may take a minute or more if a toolchain layer must rebuild.\n"
                f"[b]Enter[/]/[b]r[/] = Rebuild & {verb}   ·   [b]s[/] = {verb.capitalize()} on current"
                "   ·   [b]Esc[/] = Cancel",
                id="update-note",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(f"{verb.capitalize()} on current", id="skip")
                yield Button(f"Rebuild → {self._target}", variant="success", id="rebuild")

    def on_mount(self) -> None:
        self.query_one("#rebuild", Button).focus()  # Enter triggers Rebuild & start by default

    # -- actions (keyboard) + button handlers share the same dismiss values -----
    @on(Button.Pressed, "#rebuild")
    def action_rebuild(self) -> None:
        self.dismiss("rebuild")

    @on(Button.Pressed, "#skip")
    def action_skip(self) -> None:
        self.dismiss("skip")

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
