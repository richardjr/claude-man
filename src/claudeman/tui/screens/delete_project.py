"""Delete-project preview + confirm modal (Phase 3 TUI).

``project delete`` is irreversible — it ``rm -rf``s the ``/workspace`` checkout along with the
container and registry entry — so this modal renders ``lifecycle.delete_plan``'s per-repo SYNC
assessment first: which repos hold uncommitted / unpushed / stashed work the delete would destroy.
When any repo is risky the warning is loud and the action reads "Delete anyway", but it is never
blocked — the operator stays in control (the explicit ask: "delete anyway if there's a sync issue"
must be available). A "keep workspace checkout" toggle is the non-destructive exit: it preserves
``/workspace`` on disk so unsynced work survives while the project still leaves claude-man.

Dismisses the ``keep_workspace`` bool on confirm (``False`` = delete everything, ``True`` = keep the
checkout) or ``None`` on cancel. The app runs ``lifecycle.delete_project`` off the UI thread — this
screen never mutates anything.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Label

from ... import lifecycle

_RISK_COLUMNS = ("Repo", "Sync state")


class DeleteProjectScreen(ModalScreen["bool | None"]):
    """Confirm deleting ``plan.slug``. Dismisses ``keep_workspace`` (bool) on confirm, or ``None``."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    DeleteProjectScreen { align: center middle; }
    #dialog {
        width: 84; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $error; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #risk { height: auto; max-height: 12; }
    .del-warn { color: $warning; height: auto; padding-top: 1; }
    .del-note { color: $text-muted; padding-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, plan: lifecycle.DeletePlan) -> None:
        super().__init__()
        self._plan = plan

    def compose(self) -> ComposeResult:
        plan = self._plan
        with Vertical(id="dialog"):
            yield Label(f"Delete project · {plan.slug}", classes="title")
            if plan.items:
                yield DataTable(id="risk", cursor_type="row")
            else:
                yield Label("no repos — nothing on disk to lose", classes="del-note")
            if plan.risky:
                yield Label(
                    "⚠ UNSYNCED WORK — deleting removes the /workspace checkout permanently. "
                    "Commit & push, or tick 'keep workspace' below, to avoid losing it.",
                    classes="del-warn",
                )
            if plan.running:
                yield Label("container is RUNNING — a claude session may still be active",
                            classes="del-warn")
            yield Checkbox("keep the /workspace checkout on disk (remove container + registry only)",
                           value=False, id="keep")
            yield Label(
                "removes: container (rm -f) · state dir (workspace + claude-config + backups) · "
                "registry entry. Irreversible.",
                classes="del-note",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self._delete_label(False), variant="error", id="delete")

    def on_mount(self) -> None:
        if not self._plan.items:
            return
        table = self.query_one("#risk", DataTable)
        table.add_columns(*_RISK_COLUMNS)
        for it in self._plan.items:
            mark = "⚠" if it.risky else "✓"
            table.add_row(it.dir, f"{mark}  {it.reason}", key=it.dir)

    def _delete_label(self, keep: bool) -> str:
        if keep:
            return "Delete (keep workspace)"
        return "Delete anyway" if self._plan.risky else "Delete"

    # -- handlers ---------------------------------------------------------
    @on(Checkbox.Changed, "#keep")
    def _on_keep_toggle(self, event: Checkbox.Changed) -> None:
        # Keeping the workspace is non-destructive -> soften the button; a full delete stays red.
        btn = self.query_one("#delete", Button)
        btn.label = self._delete_label(event.value)
        btn.variant = "warning" if event.value else "error"

    @on(Button.Pressed, "#delete")
    def _delete(self) -> None:
        self.dismiss(self.query_one("#keep", Checkbox).value)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
