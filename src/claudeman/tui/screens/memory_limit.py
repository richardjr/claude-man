"""Container memory-limit modal (Settings ``m``).

Collects the global hard per-container memory cap (``Settings.container_memory`` — issue #29).
Mirrors ``AddAllowScreen``: ONE input with inline validation via the pure
``config.normalise_memory_limit`` (docker size grammar, ``1g`` minimum) so a typo gets feedback in
the modal rather than an opaque ``docker create`` failure on the next start. Dismisses the
canonicalised size string (``16G`` -> ``16g``), ``""`` for "reset to default", or ``None`` on
cancel; the parent (``SettingsScreen``) persists via ``settings_registry.set_container_memory``.

The cap is always applied (it's part of the hardened floor) — this only chooses its value, and it
is fixed at container create, so the parent's status reminds the operator to recreate.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from ... import config


class MemoryLimitScreen(ModalScreen["str | None"]):
    """Collect the hard container memory cap. Dismisses the canonical size | ``""`` (default) | ``None``."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    MemoryLimitScreen { align: center middle; }
    #dialog {
        width: 76; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    #memory-error { color: $error; height: auto; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Container memory limit (hard cap — applied to every container)", classes="title")
            yield Label(f"Current: {self._current} · default {config.DEFAULT_CONTAINER_MEMORY} · minimum 1g")
            yield Input(value=self._current, placeholder=config.DEFAULT_CONTAINER_MEMORY, id="memory")
            yield Label("A docker size: 16g, 8192m, 1.5g (units k/m/g/t). Rendered as "
                        "--memory X --memory-swap X, so the container gets no swap — a runaway inside "
                        "is OOM-killed in its own cgroup instead of starving the host. "
                        "Recreate a project to apply; running containers keep their current cap.",
                        id="memory-hint")
            yield Label("", id="memory-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Default", id="default")
                yield Button("Save", variant="success", id="save")

    def on_mount(self) -> None:
        self.query_one("#memory", Input).focus()

    @on(Input.Changed)
    def _clear_error(self) -> None:
        self.query_one("#memory-error", Label).update("")

    @on(Input.Submitted)
    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        self._submit()

    @on(Button.Pressed, "#default")
    def _reset(self) -> None:
        self.dismiss("")  # "" -> the parent resets to DEFAULT_CONTAINER_MEMORY

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        err = self.query_one("#memory-error", Label)
        raw = self.query_one("#memory", Input).value.strip()
        if not raw:
            err.update("a limit is required (or press Default)")
            return
        try:
            value = config.normalise_memory_limit(raw)
        except ValueError as exc:
            err.update(str(exc))
            return
        self.dismiss(value)
