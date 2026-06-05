"""Pull-all preview + confirm modal (Phase 3.x TUI).

Renders the read-only ``lifecycle.PullPlan`` (already fetched) as a per-repo plan — which repos will
fast-forward and which are skipped and why — so the operator commits with eyes open BEFORE any working
tree changes. Dismisses the list of eligible repo dirs to pull (the app then runs ``lifecycle.pull_apply``
off the UI thread), or ``None`` on cancel. The Pull button is disabled when nothing is eligible.

ff-only + the dirty-skip mean a pull can never clobber a live ``claude``'s uncommitted edits; when the
container is running we still surface a non-blocking advisory. No recreate is needed — ``/workspace`` is
a live bind — which the modal states explicitly so the operator doesn't restart "to make docker notice".
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from ... import lifecycle

_PLAN_COLUMNS = ("Repo", "Plan")


class PullConfirmScreen(ModalScreen["list[str] | None"]):
    """Confirm an ff-only pull of ``plan``'s eligible repos. Dismisses the eligible dirs, or None."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    PullConfirmScreen { align: center middle; }
    #dialog {
        width: 84; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #plan { height: auto; max-height: 14; }
    #pull-note { color: $text-muted; padding-top: 1; }
    #pull-warn { color: $warning; height: auto; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, plan: lifecycle.PullPlan) -> None:
        super().__init__()
        self._plan = plan

    def compose(self) -> ComposeResult:
        plan = self._plan
        n = len(plan.eligible)
        with Vertical(id="dialog"):
            yield Label(f"Pull all (ff-only) · {plan.slug}", classes="title")
            yield DataTable(id="plan", cursor_type="row")
            if plan.fetch_errors:
                yield Label("fetch issues: " + "; ".join(plan.fetch_errors), id="pull-warn")
            if plan.running:
                yield Label(
                    "container is RUNNING — a claude session may be editing these files; "
                    "ff-only skips any repo with local changes",
                    id="pull-warn",
                )
            yield Label(
                "host-side fast-forward only — no merge commits, no recreate (the /workspace bind "
                "updates live in the container)",
                id="pull-note",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(
                    f"Pull {n} repo(s)" if n else "Nothing to pull",
                    variant="success", id="pull", disabled=n == 0,
                )

    def on_mount(self) -> None:
        table = self.query_one("#plan", DataTable)
        table.add_columns(*_PLAN_COLUMNS)
        for it in self._plan.items:
            mark = "✓ pull" if it.eligible else "· skip"
            table.add_row(it.dir, f"{mark}  {it.reason}", key=it.dir)

    # -- handlers ---------------------------------------------------------
    @on(Button.Pressed, "#pull")
    def _pull(self) -> None:
        self.dismiss(self._plan.eligible)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
