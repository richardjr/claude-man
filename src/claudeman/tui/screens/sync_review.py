"""Sync-back accept/reject gate (Phase 5).

A modal (``y`` from the projects table) over a precomputed ``lifecycle.SyncPlan``: a DataTable of
changed artifacts (artifact | kind | change | scope | decision) beside a scrolling pane rendering the
cursor row's SECRET-MASKED diff. Per-row decisions start at the detect defaults (authored text →
accept; settings.json / MCP / conflicts / deletions → reject); nothing is written until ``enter``,
which dismisses with the ``{label: decision}`` map the app applies off-thread via
``lifecycle.sync_apply``. Escape cancels (dismiss ``None``) and writes nothing.

Bindings: ``a`` accept · ``r`` reject · ``s`` skip · ``space`` cycle · ``A`` accept-all-non-reject ·
``enter`` apply · ``esc`` cancel. Every cell and diff line is rendered through ``Text()`` / ``escape()``
— change labels are file paths and diffs are full of ``[...]`` that would otherwise be parsed as Rich
markup (or crash).
"""

from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Static

_COLUMNS = ("Artifact", "Kind", "Change", "Scope", "Decision")
_DECISIONS = ("accept", "reject", "skip")
# Plain Rich color names (NOT Textual `$`-CSS variables — these style rich.text.Text objects rendered
# straight by Rich in the DataTable cells / diff pane, where a `$success` token fails to parse).
_DECISION_CELL = {
    "accept": ("✓ accept", "green"),
    "reject": ("✗ reject", "red"),
    "skip": ("– skip", "dim"),
}


class SyncReviewScreen(ModalScreen[dict | None]):
    """Review ``plan``'s changes and choose per-row accept/reject/skip. Returns the decision map."""

    BINDINGS = [
        Binding("a", "accept", "Accept"),
        Binding("r", "reject", "Reject"),
        Binding("s", "skip", "Skip"),
        Binding("space", "cycle", "Cycle"),
        Binding("A", "accept_all", "Accept non-reject"),
        Binding("enter", "apply", "Apply"),
        Binding("escape", "cancel", "Cancel"),
    ]
    CSS = """
    SyncReviewScreen { align: center middle; }
    #dialog {
        width: 110; height: auto; max-height: 92%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #changes { height: auto; max-height: 12; }
    #diff-wrap { height: auto; max-height: 18; border: round $panel; margin-top: 1; padding: 0 1; }
    #review-status { height: auto; color: $text-muted; padding-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, plan) -> None:
        super().__init__()
        self._plan = plan
        # label -> decision, seeded from the detect defaults
        self._decisions: dict[str, str] = {
            row.change.label: row.change.default_decision for row in plan.changes
        }
        # label -> SyncChange, for the diff pane + cell rebuilds
        self._rows = {row.change.label: row for row in plan.changes}

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Sync-back review · {self._plan.slug} · {self._plan.pending} change(s)",
                        classes="title")
            yield DataTable(id="changes", cursor_type="row")
            with VerticalScroll(id="diff-wrap"):
                yield Static(id="diff")
            yield Label("a accept · r reject · s skip · space cycle · A accept-all-non-reject · "
                        "↵ apply · esc cancel", id="review-status")
            with Horizontal(id="buttons"):
                yield Button("Apply", variant="success", id="apply")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        table = self.query_one("#changes", DataTable)
        # add_columns returns the auto-generated ColumnKeys — keep the Decision one so update_cell can
        # repaint a single cell by key (the column LABEL is not a valid key).
        self._dec_col = table.add_columns(*_COLUMNS)[4]
        self._refresh()
        table.focus()
        self._show_diff()

    # -- data -------------------------------------------------------------
    def _refresh(self) -> None:
        table = self.query_one("#changes", DataTable)
        cursor = table.cursor_row
        table.clear()
        for label, row in self._rows.items():
            c = row.change
            change_cell = f"⚠ {c.change_type}" if c.conflict else c.change_type
            table.add_row(
                Text(c.artifact), Text(c.kind), Text(change_cell), Text(c.scope),
                self._decision_text(label), key=label,
            )
        if table.row_count:
            table.move_cursor(row=min(max(cursor, 0), table.row_count - 1))

    def _decision_text(self, label: str) -> Text:
        text, colour = _DECISION_CELL[self._decisions[label]]
        return Text(text, style=colour)

    def _show_diff(self) -> None:
        pane = self.query_one("#diff", Static)
        label = self._cursor_label()
        if label is None:
            pane.update("")
            return
        out = Text()
        for line in self._rows[label].diff:
            out.append(escape(line) + "\n", style=_diff_style(line))
        pane.update(out if self._rows[label].diff else Text("(no textual diff — binary or identical)"))

    def _cursor_label(self) -> str | None:
        table = self.query_one("#changes", DataTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:
            return None

    # -- per-row decision -------------------------------------------------
    def _set(self, decision: str) -> None:
        label = self._cursor_label()
        if label is not None:
            self._decisions[label] = decision
            self._update_decision_cell(label)

    def _update_decision_cell(self, label: str) -> None:
        table = self.query_one("#changes", DataTable)
        table.update_cell(label, self._dec_col, self._decision_text(label))

    @on(DataTable.RowHighlighted)
    def _on_highlight(self, event: DataTable.RowHighlighted) -> None:
        self._show_diff()

    @on(DataTable.RowSelected)
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter on the focused table is consumed as RowSelected (so the `enter` binding never fires) —
        # route it to apply, the documented gesture once decisions are set.
        self.action_apply()

    # -- actions ----------------------------------------------------------
    def action_accept(self) -> None:
        self._set("accept")

    def action_reject(self) -> None:
        self._set("reject")

    def action_skip(self) -> None:
        self._set("skip")

    def action_cycle(self) -> None:
        label = self._cursor_label()
        if label is None:
            return
        nxt = _DECISIONS[(_DECISIONS.index(self._decisions[label]) + 1) % len(_DECISIONS)]
        self._decisions[label] = nxt
        self._update_decision_cell(label)

    def action_accept_all(self) -> None:
        """Accept every row whose default is NOT reject (the safe authored-text changes), leaving
        settings.json / MCP / conflicts / deletions at their reject default."""
        for label, row in self._rows.items():
            if row.change.default_decision != "reject":
                self._decisions[label] = "accept"
        self._refresh()

    @on(Button.Pressed, "#apply")
    def action_apply(self) -> None:
        self.dismiss(dict(self._decisions))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


def _diff_style(line: str) -> str:
    if line.startswith(("+++", "---")):
        return "bold"
    if line.startswith("@@"):
        return "cyan"
    if line.startswith("+"):
        return "green"
    if line.startswith("-"):
        return "red"
    return ""
