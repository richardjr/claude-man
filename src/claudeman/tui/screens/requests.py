"""Relay requests screen (global ``r``) — the TUI twin of ``claudemanctl relay list|show|approve|deny|
cancel`` (docs/RELAY.md).

Lists cross-project requests (awaiting-approval first; ``f`` toggles finished ones), shows the selected
request's params / note / reply / events, and approves, denies or cancels it in place. The verbs are the
same compare-and-set ``relay.engine`` calls the CLI uses (fast record writes, inline); the relay acts on
the new state at its next tick. Refreshes itself every couple of seconds while open.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ItemGrid, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Static

from ... import lifecycle
from ...relay import engine, store
from .. import relayview
from .deny_request import DenyRequestScreen


class RequestsScreen(ModalScreen[None]):
    """Review + act on relay requests. ``relay_here`` reports whether THIS TUI holds the relay lock."""

    BINDINGS = [
        Binding("a", "approve", "Approve"),
        Binding("d", "deny", "Deny"),
        Binding("c", "cancel_request", "Cancel request"),
        Binding("f", "toggle_finished", "Finished"),
        Binding("escape", "close", "Close"),
    ]
    CSS = """
    RequestsScreen { align: center middle; }
    #dialog {
        width: 132; height: 90%;
        padding: 1 2;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; }
    #relay-status { color: $text-muted; padding-bottom: 1; }
    #requests { height: 1fr; min-height: 5; }
    #detail-box { height: 1fr; min-height: 5; border: round $panel; }
    #req-status { height: auto; width: 100%; color: $text-muted; }
    /* ItemGrid wraps the action buttons into rows instead of cropping them — CLAUDE.md
       "TUI dialog button rows" (reflow, no crop). */
    #buttons { height: auto; padding-top: 1; grid-gutter: 0 1; }
    """

    def __init__(self, relay_here: Callable[[], bool]) -> None:
        super().__init__()
        self._relay_here = relay_here
        self._show_all = False
        self._shown: tuple[str, str] | None | bool = False   # (id, updated) in the detail pane

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Relay · cross-project requests", classes="title")
            yield Label("", id="relay-status")
            yield DataTable(id="requests", cursor_type="row")
            with VerticalScroll(id="detail-box"):
                yield Static("", id="detail")
            yield Label("a Approve · d Deny · c Cancel (stops a running one) · f show/hide finished · "
                        "esc Close",
                        id="req-status")
            with ItemGrid(id="buttons", min_column_width=16):
                yield Button("Approve", variant="success", id="approve")
                yield Button("Deny", variant="error", id="deny")
                yield Button("Cancel request", id="cancel")
                yield Button("Show finished", id="finished")
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#requests", DataTable).add_columns(*relayview.REQUEST_COLUMNS)
        self._refresh()
        self.query_one("#requests", DataTable).focus()
        self.set_interval(engine.TICK_S, self._refresh)

    # -- data ---------------------------------------------------------------------
    def _selected(self) -> str | None:
        table = self.query_one("#requests", DataTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:  # noqa: BLE001
            return None

    def _refresh(self) -> None:
        keep = self._selected()
        records = store.list_records()
        table = self.query_one("#requests", DataTable)
        table.clear()
        rows = relayview.request_rows(records, show_all=self._show_all)
        for key, cells in rows:
            # Every cell is a Text (never a str): DataTable parses a str as Rich markup, and a
            # request's detail can carry agent-chosen text like "[/]" — it must render literally.
            styled = [Text(c, style=relayview.status_style(c) if i == 3 else "")
                      for i, c in enumerate(cells)]
            table.add_row(*styled, key=key)
        keys = [k for k, _ in rows]
        if keep in keys:
            table.move_cursor(row=keys.index(keep))
        n = relayview.counts(records)
        tally = " · ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(n.items())) or "no requests yet"
        self.query_one("#relay-status", Label).update(
            f"{relayview.relay_status(here=self._relay_here(), holder_pid=store.relay_holder_pid())}"
            f"   ·   {tally}")
        self.query_one("#finished", Button).label = "Hide finished" if self._show_all else "Show finished"
        self._render_detail()

    def _render_detail(self) -> None:
        rid = self._selected()
        rec = store.load(rid) if rid else None
        shown = (rec.id, rec.updated) if rec else None
        if shown == self._shown:
            return          # unchanged — skip re-reading the event log on every refresh
        self._shown = shown
        text = ("\n".join(relayview.detail_lines(rec, store.events_for(rec.id))) if rec
                else "(select a request)")
        self.query_one("#detail", Static).update(Text(text))

    @on(DataTable.RowHighlighted, "#requests")
    def _on_highlight(self) -> None:
        self._render_detail()

    def _status(self, res: lifecycle.Result) -> None:
        colour = "$success" if res.ok else "$error"
        self.query_one("#req-status", Label).update(f"[{colour}]{escape(res.detail)}[/]")

    def _act(self, verb: str) -> str | None:
        rid = self._selected()
        rec = store.load(rid) if rid else None
        if rec is None:
            self._status(lifecycle.Result(False, "no request selected"))
            return None
        if not relayview.can_act(rec.status)[verb]:
            self._status(lifecycle.Result(False, f"{rec.id} is {rec.status} — can't {verb} it"))
            return None
        return rec.id

    # -- actions --------------------------------------------------------------------
    @on(Button.Pressed, "#approve")
    def action_approve(self) -> None:
        rid = self._act("approve")
        if rid:
            self._status(engine.approve(rid))
            self._refresh()

    @on(Button.Pressed, "#deny")
    def action_deny(self) -> None:
        rid = self._act("deny")
        if rid:
            self.app.push_screen(DenyRequestScreen(rid), lambda reason: self._on_deny(rid, reason))

    def _on_deny(self, rid: str, reason) -> None:
        if reason is None:
            return
        self._status(engine.deny(rid, reason))
        self._refresh()

    @on(Button.Pressed, "#cancel")
    def action_cancel_request(self) -> None:
        rid = self._act("cancel")
        if rid:
            self._status(engine.cancel(rid))
            self._refresh()

    @on(Button.Pressed, "#finished")
    def action_toggle_finished(self) -> None:
        self._show_all = not self._show_all
        self._refresh()

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)
