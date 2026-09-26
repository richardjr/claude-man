"""Run-prompt modal (Project menu -> ``u`` Run prompt (headless)…) — the TUI twin of
``claudemanctl project run`` (the Phase 7-run seam).

Collects one prompt + a permission level; dismisses ``(prompt, permission)`` or ``None`` on cancel.
The app runs ``lifecycle.run`` off-thread and streams the normalised events into the log pane.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, TextArea

from ... import agents

RunPrompt = tuple[str, str]


class RunPromptScreen(ModalScreen["RunPrompt | None"]):
    """Dismisses ``(prompt, permission)`` | ``None``."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    RunPromptScreen { align: center middle; }
    #dialog {
        width: 90; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    #prompt { height: 8; }
    #run-error { color: $error; height: auto; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str, agent: str) -> None:
        super().__init__()
        self._slug = slug
        self._agent = agent

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Run a headless {self._agent} session in {self._slug}", classes="title")
            yield Label("Prompt (one non-interactive session; the final message + tool calls stream "
                        "to the log pane)")
            yield TextArea(id="prompt")
            yield Label("Permission — default: approval-needing tool calls are refused · edits: "
                        "auto-accept file edits · full: every tool call auto-approved (inside the "
                        "hardened container, the real sandbox)")
            yield Select([(p, p) for p in agents.PERMISSIONS], value="default", allow_blank=False,
                         id="permission")
            yield Label("", id="run-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Run", variant="success", id="run")

    def on_mount(self) -> None:
        self.query_one("#prompt", TextArea).focus()

    @on(Button.Pressed, "#run")
    def _run(self) -> None:
        prompt = self.query_one("#prompt", TextArea).text.strip()
        if not prompt:
            self.query_one("#run-error", Label).update("a prompt is required")
            return
        self.dismiss((prompt, str(self.query_one("#permission", Select).value)))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
