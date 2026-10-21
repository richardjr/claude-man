"""Add / edit a helpdesk task (Helpdesk screen → ``t`` / ``e``) — the TUI twin of ``claudemanctl
project helpdesk task add [--replace]``. Validates by constructing the ``HelpTask`` (so every schema
guard — placeholders, permission, timeout — fires in the modal) and dismisses ``(task, replace)`` or
``None``."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, TextArea

from ... import agents
from ...registry.schema import HELP_TIMEOUT_DEFAULT, HelpTask, ValidationError, parse_duration
from .. import relayview


class HelpTaskScreen(ModalScreen["tuple[HelpTask, bool] | None"]):
    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    HelpTaskScreen { align: center middle; }
    #dialog {
        width: 96; height: auto; max-height: 95%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; width: 100%; }
    #params { height: 4; }
    #prompt { height: 9; }
    #task-error { color: $error; height: auto; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str, task: HelpTask | None) -> None:
        super().__init__()
        self._slug = slug
        self._editing = task

    def compose(self) -> ComposeResult:
        t = self._editing
        with Vertical(id="dialog"):
            yield Label(f"{'Edit' if t else 'Add'} a helpdesk task for {self._slug}", classes="title")
            yield Label("Name (lowercase, e.g. dev-up)")
            yield Input(value=t.name if t else "", placeholder="dev-up", id="name", disabled=t is not None)
            yield Label("Summary (one line shown to requesting projects)")
            yield Input(value=t.summary if t else "", placeholder="Start the dev stack on a branch",
                        id="summary")
            yield Label("Params — one per line: name=description (all required; used as {name} below)")
            yield TextArea(relayview.params_text(t), id="params")
            yield Label("Prompt template — what this project's agent is asked to do. Leave empty for a "
                        "free-form task whose note IS the request. {{ }} = literal braces.")
            yield TextArea(t.prompt if t else "", id="prompt")
            yield Label("Permission — default: read-only consult · edits: auto-accept edits · full: "
                        "every tool call (inside the hardened container)")
            yield Select([(p, p) for p in agents.PERMISSIONS], value=t.permission if t else "default",
                         allow_blank=False, id="permission")
            yield Label("Approval — inherit the helpdesk default, or set it for this task")
            yield Select([("inherit", ""), ("operator", "operator"), ("auto", "auto")],
                         value=t.approve if t else "", allow_blank=False, id="approve")
            yield Label("Timeout (e.g. 90s / 20m / 2h)")
            yield Input(value=relayview.timeout_text(t.timeout if t else HELP_TIMEOUT_DEFAULT), id="timeout")
            yield Label("", id="task-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="success", id="save")

    def on_mount(self) -> None:
        self.query_one("#summary" if self._editing else "#name").focus()

    @on(Input.Changed)
    def _clear_error(self) -> None:
        self.query_one("#task-error", Label).update("")

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        err = self.query_one("#task-error", Label)
        try:
            task = HelpTask(
                name=self.query_one("#name", Input).value.strip(),
                summary=self.query_one("#summary", Input).value,
                params=relayview.parse_params(self.query_one("#params", TextArea).text),
                prompt=self.query_one("#prompt", TextArea).text,
                permission=str(self.query_one("#permission", Select).value),
                approve=str(self.query_one("#approve", Select).value),
                timeout=parse_duration(self.query_one("#timeout", Input).value),
            )
        except ValidationError as exc:
            err.update(str(exc))
            return
        self.dismiss((task, self._editing is not None))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
