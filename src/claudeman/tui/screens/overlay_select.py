"""Pick a project's overlay (container image variant) — Project… menu → ``i``.

Lists ``config.OVERLAYS`` with a one-line description and the project's current overlay marked.
Dismisses the chosen overlay name, or ``None`` on cancel OR when the operator picks the overlay the
project already uses (a no-op — nothing to recreate). The app applies a real change off-thread via
``lifecycle.recreate(slug, overlay=…)``, which persists the choice and rebuilds the image if missing
(recreate-to-apply, exactly like the egress lock/unlock flow).
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from ... import config

_COLUMNS = ("Overlay", "Toolchain")

# Display-only blurbs (the screen is the only consumer). Unknown overlays fall back to "" so a future
# config.OVERLAYS entry without a blurb still lists cleanly rather than breaking.
_OVERLAY_DESC = {
    "base": "node + claude only (no extra toolchain)",
    "python": "+ python3 + uv",
    "rust": "+ rustup toolchain",
    "node": "+ corepack yarn/pnpm",
    "python-node": "+ python3/uv AND yarn/pnpm (polyglot)",
    "terraform": "+ terraform + packer (HashiCorp infra)",
}


class OverlaySelectScreen(ModalScreen["str | None"]):
    """Select a project's overlay/image (an overlay name to switch to, or None for cancel/no-op)."""

    BINDINGS = [("escape", "cancel", "Cancel"), ("enter", "choose", "Select")]
    CSS = """
    OverlaySelectScreen { align: center middle; }
    #dialog {
        width: 64; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #overlays { height: auto; max-height: 14; }
    #overlay-note { height: auto; color: $text-muted; padding-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str, current: str) -> None:
        super().__init__()
        self._slug = slug
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Overlay (image) · {self._slug}", classes="title")
            yield DataTable(id="overlays", cursor_type="row")
            yield Label("changing the overlay recreates the container (and builds the image if "
                        "missing) to apply", id="overlay-note")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Select", variant="success", id="choose")

    def on_mount(self) -> None:
        table = self.query_one("#overlays", DataTable)
        table.add_columns(*_COLUMNS)
        for overlay in config.OVERLAYS:
            mark = " ←" if overlay == self._current else ""
            table.add_row(f"{overlay}{mark}", _OVERLAY_DESC.get(overlay, ""), key=overlay)
        table.focus()

    def _selected(self) -> str | None:
        table = self.query_one("#overlays", DataTable)
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:  # noqa: BLE001 - empty table / no cursor
            return None

    @on(DataTable.RowSelected)
    @on(Button.Pressed, "#choose")
    def action_choose(self) -> None:
        choice = self._selected()
        # Picking the current overlay is a no-op — dismiss None so the app skips the recreate.
        self.dismiss(choice if choice and choice != self._current else None)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
