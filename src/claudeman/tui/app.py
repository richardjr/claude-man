"""The claude-man Textual application.

Phase-1 skeleton: a live projects table (registry JOINed with `docker ps`), bindings to
open a shell / claude in a detached terminal, start/stop, and placeholders for the create
form, log pane, and sync-review gate that later phases fill in.

`textual` is only imported here, so importing the CLI or running the tests never requires it.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, RichLog

from .. import lifecycle
from ..docker import status
from ..registry import projects
from . import terminals

_COLUMNS = ("Project", "Status", "Profile", "Egress", "Repos", "Version", "Detail")


class ClaudeManApp(App):
    TITLE = "claude-man"
    CSS = """
    DataTable { height: 1fr; }
    RichLog { height: 10; border: round $panel; }
    """
    BINDINGS = [
        Binding("n", "new_project", "New"),
        Binding("enter", "open_shell", "Shell"),
        Binding("c", "open_claude", "Claude"),
        Binding("s", "toggle_running", "Start/Stop"),
        Binding("l", "focus_logs", "Logs"),
        Binding("y", "sync_review", "Sync-back"),
        Binding("d", "delete_project", "Delete"),
        Binding("r", "recreate", "Recreate"),
        Binding("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield DataTable(id="projects", cursor_type="row")
            yield RichLog(id="log", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#projects", DataTable)
        table.add_columns(*_COLUMNS)
        self.refresh_projects()
        # Phase 1: poll. Phase 2 upgrades this to a `docker events` worker.
        self.set_interval(2.0, self.refresh_projects)

    # -- data -------------------------------------------------------------
    def _rows(self) -> list[status.Row]:
        defined = [
            (p.slug, p.profile or "(default)", p.egress, len(p.repos))
            for p in projects.list_projects()
        ]
        return status.join(defined, status.query_containers())

    def refresh_projects(self) -> None:
        table = self.query_one("#projects", DataTable)
        # Restore the cursor by SLUG, not integer index — rows are slug-sorted and the set can
        # change between polls, so an index restore could land on the wrong project (review TUI-7).
        prev_slug = self._current_slug()
        table.clear()
        rows = self._rows()
        for row in rows:
            table.add_row(
                row.slug, row.kind, row.profile, row.egress,
                row.repos, row.version or "-", row.status_text or "-",
                key=row.slug,
            )
        if prev_slug is not None:
            slugs = [r.slug for r in rows]
            if prev_slug in slugs:
                table.move_cursor(row=slugs.index(prev_slug))

    def _current_slug(self) -> str | None:
        table = self.query_one("#projects", DataTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:
            return None

    def _log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    # -- actions ----------------------------------------------------------
    def action_open_shell(self) -> None:
        slug = self._current_slug()
        if slug:
            terminals.spawn_shell(slug)
            self._log(f"[green]shell[/] opened for {slug}")

    def action_open_claude(self) -> None:
        slug = self._current_slug()
        if slug:
            terminals.spawn_claude(slug)
            self._log(f"[green]claude[/] launched for {slug}")

    def action_toggle_running(self) -> None:
        slug = self._current_slug()
        if not slug:
            return
        row = next((r for r in self._rows() if r.slug == slug), None)
        if row is None:
            return
        # Branch on the joined state: UP -> stop; STOPPED -> start; DEFINED -> create-then-start.
        # Surface the real returncode/stderr instead of always logging success (review TUI-1).
        if row.kind == status.UP:
            res = lifecycle.stop(slug)
        elif projects.exists(slug):
            res = lifecycle.up(projects.load(slug))
        else:
            self._log(f"[red]{slug}: orphan container (no registry entry) — not managed[/]")
            return
        self._log(f"[{'green' if res.ok else 'red'}]{res.detail}[/]")
        self.refresh_projects()

    def action_focus_logs(self) -> None:
        slug = self._current_slug()
        if slug:
            self.query_one("#log", RichLog).focus()
            self._log(f"(phase 1) live log streaming for {slug} — see screens/logs.py")

    def on_data_table_row_selected(self, event) -> None:
        # Enter on a row opens a shell. The app-level `enter` binding is shadowed by DataTable's
        # own Enter -> RowSelected handling, so we act on the message instead (review TUI-3).
        self.action_open_shell()

    def action_new_project(self) -> None:
        self._log(
            "create a project with `claudemanctl project create <slug>` "
            "(interactive form is a phase-1 follow-up) — it then appears here for start/stop/shell"
        )

    def action_sync_review(self) -> None:
        self._log("(phase 5) sync-back review gate — see screens/sync_review.py")

    def action_delete_project(self) -> None:
        self._log("(phase 3) delete confirm modal — see ROADMAP.md")

    def action_recreate(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):
            return
        self._log(f"recreating {slug} …")
        res = lifecycle.recreate(slug)
        self._log(f"[{'green' if res.ok else 'red'}]{res.detail}[/]")
        self.refresh_projects()


def run() -> None:
    ClaudeManApp().run()
