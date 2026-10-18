"""Helpdesk screen (Project menu → ``h``) — the TUI twin of ``claudemanctl project helpdesk …``
(docs/RELAY.md).

Manages which other projects may send this one requests and the tasks it offers. Every change is a
fast flocked registry write + an immediate re-stamp of the affected projects' relay context (plain bind
files), so they run inline — no recreate, ever.
"""

from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ItemGrid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from ... import lifecycle
from ...registry import projects
from ...relay import policy
from .. import relayview
from .allow_sender import AllowSenderScreen
from .help_task import HelpTaskScreen


class HelpdeskScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("a", "allow", "Allow sender"),
        Binding("x", "disallow", "Disallow sender"),
        Binding("t", "add_task", "Add task"),
        Binding("e", "edit_task", "Edit task"),
        Binding("r", "remove_task", "Remove task"),
        Binding("o", "toggle_approve", "Default approval"),
        Binding("s", "toggle_autostart", "Autostart"),
        Binding("escape", "close", "Close"),
    ]
    CSS = """
    HelpdeskScreen { align: center middle; }
    #dialog {
        width: 110; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; }
    #hd-state { color: $text-muted; padding-bottom: 1; }
    #senders { height: auto; max-height: 7; }
    #tasks { height: auto; max-height: 10; margin-top: 1; }
    #hd-asks { color: $text-muted; padding-top: 1; }
    #hd-status { height: auto; width: 100%; color: $text-muted; padding-top: 1; }
    /* ItemGrid: reflow, never crop — CLAUDE.md "TUI dialog button rows". */
    #buttons { height: auto; padding-top: 1; grid-gutter: 0 1; }
    """

    def __init__(self, slug: str) -> None:
        super().__init__()
        self._slug = slug

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Helpdesk · {self._slug} — requests other projects may send it", classes="title")
            yield Label("", id="hd-state")
            yield DataTable(id="senders", cursor_type="row")
            yield DataTable(id="tasks", cursor_type="row")
            yield Label("", id="hd-asks")
            yield Label("a allow · x disallow · t add task · e edit · r remove task · o approval · "
                        "s autostart · esc close   (applies at once — no recreate)", id="hd-status")
            with ItemGrid(id="buttons", min_column_width=16):
                yield Button("Allow…", variant="success", id="allow")
                yield Button("Disallow", variant="error", id="disallow")
                yield Button("Add task…", variant="success", id="add-task")
                yield Button("Edit task…", id="edit-task")
                yield Button("Remove task", variant="error", id="remove-task")
                yield Button("Approval", id="approve")
                yield Button("Autostart", id="autostart")
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#senders", DataTable).add_columns(*relayview.SENDER_COLUMNS)
        self.query_one("#tasks", DataTable).add_columns(*relayview.TASK_COLUMNS)
        self._refresh()
        self.query_one("#tasks", DataTable).focus()

    # -- data --------------------------------------------------------------------
    def _project(self):
        try:
            return projects.load(self._slug)
        except Exception as exc:  # noqa: BLE001 - never tear down the app on a bad project load
            self._status(lifecycle.Result(False, f"failed to load {self._slug}: {exc}"))
            return None

    def _refresh(self) -> None:
        project = self._project()
        if project is None:
            return
        h = project.helpdesk
        self.query_one("#hd-state", Label).update(escape(relayview.helpdesk_status(h)))
        senders = self.query_one("#senders", DataTable)
        senders.clear()
        for s in h.accepts_from:
            senders.add_row(Text(s if projects.exists(s) else f"{s} (no such project)"), key=s)
        tasks = self.query_one("#tasks", DataTable)
        tasks.clear()
        for key, cells in relayview.task_rows(h):
            tasks.add_row(*(Text(c) for c in cells), key=key)   # Text: never parsed as markup
        offers = policy.offers_for(project, projects.list_projects())
        self.query_one("#hd-asks", Label).update(
            f"{self._slug} itself can ask: {', '.join(o.target for o in offers) or '(no project accepts it)'}")

    def _status(self, res: lifecycle.Result) -> None:
        colour = "$success" if res.ok else "$error"
        self.query_one("#hd-status", Label).update(f"[{colour}]{escape(res.detail)}[/]")

    def _apply(self, res: lifecycle.Result) -> None:
        self._status(res)
        self._refresh()

    @staticmethod
    def _key(table: DataTable) -> str | None:
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:  # noqa: BLE001
            return None

    # -- actions -------------------------------------------------------------------
    @on(Button.Pressed, "#allow")
    def action_allow(self) -> None:
        project = self._project()
        if project is None:
            return
        choices = [s for s in projects.list_slugs()
                   if s != self._slug and s not in project.helpdesk.accepts_from]
        if not choices:
            self._status(lifecycle.Result(False, "no other project left to allow"))
            return
        self.app.push_screen(AllowSenderScreen(self._slug, choices), self._on_allow)

    def _on_allow(self, sender) -> None:
        if sender:
            self._apply(lifecycle.helpdesk_allow(self._slug, sender))

    @on(Button.Pressed, "#disallow")
    def action_disallow(self) -> None:
        sender = self._key(self.query_one("#senders", DataTable))
        if sender is None:
            self._status(lifecycle.Result(False, "no sender selected"))
            return
        self._apply(lifecycle.helpdesk_disallow(self._slug, sender))

    @on(Button.Pressed, "#add-task")
    def action_add_task(self) -> None:
        self.app.push_screen(HelpTaskScreen(self._slug, None), self._on_task)

    @on(Button.Pressed, "#edit-task")
    def action_edit_task(self) -> None:
        project = self._project()
        name = self._key(self.query_one("#tasks", DataTable))
        task = project.helpdesk.task(name) if project and name else None
        if task is None:
            self._status(lifecycle.Result(False, "no task selected"))
            return
        self.app.push_screen(HelpTaskScreen(self._slug, task), self._on_task)

    def _on_task(self, result) -> None:
        if result:
            task, replace = result
            self._apply(lifecycle.helpdesk_task_add(self._slug, task, replace=replace))

    @on(Button.Pressed, "#remove-task")
    def action_remove_task(self) -> None:
        name = self._key(self.query_one("#tasks", DataTable))
        if name is None:
            self._status(lifecycle.Result(False, "no task selected"))
            return
        self._apply(lifecycle.helpdesk_task_rm(self._slug, name))

    @on(Button.Pressed, "#approve")
    def action_toggle_approve(self) -> None:
        project = self._project()
        if project is None:
            return
        new = "auto" if project.helpdesk.approve == "operator" else "operator"
        self._apply(lifecycle.helpdesk_set(self._slug, approve=new))

    @on(Button.Pressed, "#autostart")
    def action_toggle_autostart(self) -> None:
        project = self._project()
        if project is None:
            return
        self._apply(lifecycle.helpdesk_set(self._slug, autostart=not project.helpdesk.autostart))

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)
