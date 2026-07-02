"""Pick a project's profile (account) — Project… menu → ``f``.

Lists the registry's profiles (``profilesview.rows``) with the project's effective current profile
marked, its account email, the default flag, and a factual token-age hint. Dismisses the chosen
profile name, or ``None`` on cancel OR when the operator picks the profile the project already runs
(a no-op — nothing to recreate).

The app applies a real change off-thread via ``lifecycle.recreate(slug, profile_name=…)``, which
persists the choice and re-seeds identity. Switching to a *different account*'s profile trips the
account-mismatch guard — the app pre-checks that and confirms (re-seed) before forcing, so this
screen stays a pure picker (mirrors ``OverlaySelectScreen``).
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from .. import profilesview

_COLUMNS = ("Profile", "Account", "Default", "Token")


class ProfileSelectScreen(ModalScreen["str | None"]):
    """Select a project's profile (a profile name to switch to, or None for cancel/no-op)."""

    BINDINGS = [("escape", "cancel", "Cancel"), ("enter", "choose", "Select")]
    CSS = """
    ProfileSelectScreen { align: center middle; }
    #dialog {
        width: 72; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #profiles { height: auto; max-height: 14; }
    #profile-note { height: auto; color: $text-muted; padding-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str, current: str) -> None:
        super().__init__()
        self._slug = slug
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Profile (account) · {self._slug}", classes="title")
            yield DataTable(id="profiles", cursor_type="row")
            yield Label(
                "switching profile recreates the container and re-seeds identity; a different "
                "account is confirmed first (the old account's session history stays)",
                id="profile-note",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Select", variant="success", id="choose")

    def on_mount(self) -> None:
        table = self.query_one("#profiles", DataTable)
        table.add_columns(*_COLUMNS)
        for row in profilesview.rows(self._current):
            mark = " ←" if row.marked else ""
            table.add_row(
                f"{row.name}{mark}", row.account, "✓" if row.default else "", row.token, key=row.key
            )
        table.focus()

    def _selected(self) -> str | None:
        table = self.query_one("#profiles", DataTable)
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:  # noqa: BLE001 - empty table / no cursor
            return None

    @on(DataTable.RowSelected)
    @on(Button.Pressed, "#choose")
    def action_choose(self) -> None:
        choice = self._selected()
        # Picking the current profile is a no-op — dismiss None so the app skips the recreate.
        self.dismiss(choice if choice and choice != self._current else None)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
