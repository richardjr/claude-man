"""Reusable action-list submenu (Phase 3.x TUI).

The "submenu" primitive: a ``ModalScreen`` whose body IS the second keystroke — press a top-level key
to open it, then one more key to pick an action. This mirrors the established ``EnvMountsScreen``
pattern (a modal with its own hotkeys + a hint Label) so the footer never has to grow a key per verb;
new actions are extra rows here, not new top-level ``Binding``s.

It is deliberately group-agnostic: the app feeds it a title + a list of ``(key, label, token)`` rows
and a per-instance callback dispatches the chosen ``token`` back to an existing ``action_*`` handler.
The screen itself NEVER mutates anything — it only resolves a keystroke to a token and dismisses it.
Keys are handled via ``on_key`` (not class ``BINDINGS``) so the rows can be fully dynamic per group.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

# One menu row: the hotkey, the human label, and the dispatch token the app maps to an action_*.
MenuItem = tuple[str, str, str]


class MenuScreen(ModalScreen["str | None"]):
    """A small modal action-list. Dismisses the chosen item's ``token`` (or ``None`` on Escape).

    ``items`` is ``[(key, label, token), …]``; the app pushes this with a callback that routes the
    dismissed token to the matching ``action_*`` method (those re-resolve the cursor's project
    themselves, so no slug threading is needed here).
    """

    BINDINGS = [("escape", "cancel", "Close")]
    CSS = """
    MenuScreen { align: center middle; }
    #dialog {
        width: 56; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Button { width: 100%; margin-bottom: 1; }
    #menu-hint { color: $text-muted; padding-top: 1; }
    """

    def __init__(self, title: str, items: list[MenuItem]) -> None:
        super().__init__()
        self._title = title
        self._items = items
        self._by_key = {key: token for key, _label, token in items}

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title, classes="title")
            for key, label, token in self._items:
                yield Button(f"{key}   {label}", id=f"item-{token}")
            hint = " · ".join(f"{key} {label}" for key, label, _ in self._items)
            yield Label(f"{hint} · esc Close", id="menu-hint")

    def on_key(self, event) -> None:
        token = self._by_key.get(event.key)
        if token is not None:
            event.stop()
            self.dismiss(token)

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("item-"):
            self.dismiss(bid[len("item-"):])

    def action_cancel(self) -> None:
        self.dismiss(None)
