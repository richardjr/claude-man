"""Custom terminal launcher template editor (Terminal picker -> ``custom``).

Collects the ``[terminal] command`` argv template in the TUI — previously CLI-only
(``claudemanctl config terminal --custom '…'``), which stranded exactly the user who needs it:
someone whose terminal isn't in the built-in table (issue #31). Mirrors ``MemoryLimitScreen``:
ONE input with inline validation (shlex grammar + exactly one bare ``{argv}`` element — the same
rule ``Settings.__post_init__`` enforces, checked here so a save can't raise). A template whose
binary isn't on PATH gets a warning note but still saves — the operator may install it next.
Dismisses the argv tuple, or ``None`` on cancel; the parent persists via
``settings_registry.set_terminal(program="custom", command=…)``.
"""

from __future__ import annotations

import shlex
import shutil

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label


class CustomTerminalScreen(ModalScreen["tuple[str, ...] | None"]):
    """Edit the custom launcher template. Dismisses the argv tuple or ``None`` on cancel."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    CustomTerminalScreen { align: center middle; }
    #dialog {
        width: 76; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    #custom-warn { color: $warning; height: auto; }
    #custom-error { color: $error; height: auto; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, current: tuple[str, ...]) -> None:
        super().__init__()
        self._current = current
        self._warned_missing = ""  # argv0 already warned about (warn once, then save anyway)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Custom terminal launcher template", classes="title")
            yield Input(value=shlex.join(self._current) if self._current else "",
                        placeholder="myterm --new-window -T {title} -- {argv}", id="custom")
            yield Label("The element {argv} is replaced by the command to run in the window "
                        "(required, on its own); {title} and {class} are substituted inside "
                        "elements. Example for Ptyxis via flatpak:\n"
                        "  flatpak run app.devsuite.Ptyxis -- {argv}",
                        id="custom-hint")
            yield Label("", id="custom-warn")
            yield Label("", id="custom-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="success", id="save")

    def on_mount(self) -> None:
        self.query_one("#custom", Input).focus()

    @on(Input.Changed)
    def _clear_error(self) -> None:
        self.query_one("#custom-error", Label).update("")
        self.query_one("#custom-warn", Label).update("")
        self._warned_missing = ""

    @on(Input.Submitted)
    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        err = self.query_one("#custom-error", Label)
        raw = self.query_one("#custom", Input).value.strip()
        if not raw:
            err.update("a launcher command is required")
            return
        try:
            parts = tuple(shlex.split(raw))
        except ValueError as exc:
            err.update(f"not a valid command line: {exc}")
            return
        if parts.count("{argv}") != 1:
            err.update("the template needs exactly one bare {argv} element "
                       "(the command to run in the window)")
            return
        if not shutil.which(parts[0]) and self._warned_missing != parts[0]:
            # Warn once, then save on the next submit — they may install the binary later.
            self._warned_missing = parts[0]
            self.query_one("#custom-warn", Label).update(
                f"note: {parts[0]!r} is not on PATH — Save again to keep it anyway")
            return
        self.dismiss(parts)
