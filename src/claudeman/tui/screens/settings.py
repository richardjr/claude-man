"""Settings screen — global claude-man config (Settings, ``,`` key).

The "general features" surface. First section: the ssh keys claude-man auto-loads into the host agent.
Lists each configured key with its live agent status (loaded / not loaded / missing), and manages them:
add (via ``AddKeyScreen`` — adding also ``ssh-add``s it now), remove (config only), and load-all.

Mirrors ``EnvMountsScreen``: a ModalScreen with its own hotkeys + a status Label, and the slow,
shell-touching work (``ssh-add`` / ``ssh-keygen``) on a thread worker so the UI never blocks (and a
passphrase prompt can never reach this terminal — ``ssh_agent`` runs ssh-add non-interactively).
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from ... import lifecycle, ssh_agent
from ...registry import settings as settings_registry
from .add_key import AddKeyScreen

_KEY_COLUMNS = ("Ssh key", "Status")


class SettingsScreen(ModalScreen[None]):
    """View + manage global settings (ssh keys auto-loaded into the agent)."""

    BINDINGS = [
        Binding("a", "add", "Add key"),
        Binding("x", "remove", "Remove"),
        Binding("l", "load", "Load all"),
        Binding("escape", "close", "Close"),
    ]
    CSS = """
    SettingsScreen { align: center middle; }
    #dialog {
        width: 88; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #keys { height: auto; max-height: 12; }
    #settings-status { height: auto; color: $text-muted; padding-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Settings · ssh keys (auto-loaded into the agent on startup)", classes="title")
            yield DataTable(id="keys", cursor_type="row")
            yield Label("a Add · x Remove · l Load all · esc Close", id="settings-status")
            with Horizontal(id="buttons"):
                yield Button("Add", variant="success", id="add")
                yield Button("Remove", variant="error", id="remove")
                yield Button("Load all", id="load")
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#keys", DataTable).add_columns(*_KEY_COLUMNS)
        self._refresh_status()

    # -- data -------------------------------------------------------------
    def _keys(self) -> tuple[str, ...]:
        try:
            return settings_registry.load().ssh_keys
        except Exception:  # noqa: BLE001 - a bad config must not tear down the app
            return ()

    def _paint_keys(self, statuses: dict[str, str]) -> None:
        table = self.query_one("#keys", DataTable)
        table.clear()
        keys = self._keys()
        if not keys:
            table.add_row("(no keys — press 'a' to add one)", "")
            return
        for k in keys:
            table.add_row(k, statuses.get(k, "…"), key=k)

    @work(thread=True, exclusive=True, group="settings-status")
    def _refresh_status(self) -> None:
        """Compute each key's live agent status off the UI thread (ssh-add -l / ssh-keygen shell out).

        Its own group so a status refresh never cancels (or is cancelled by) an add/load mutation."""
        loaded = ssh_agent.loaded_fingerprints()
        statuses = {k: ssh_agent.key_status(k, loaded) for k in self._keys()}
        self.app.call_from_thread(self._paint_keys, statuses)

    def _status(self, res: lifecycle.Result) -> None:
        colour = "$success" if res.ok else "$warning"
        # Collapse the multi-line per-key detail into one status line.
        self.query_one("#settings-status", Label).update(f"[{colour}]{res.detail.splitlines()[0]}[/]")

    def _selected_key(self) -> str | None:
        table = self.query_one("#keys", DataTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:
            return None

    # -- actions ----------------------------------------------------------
    @on(Button.Pressed, "#add")
    def action_add(self) -> None:
        self.app.push_screen(AddKeyScreen(set(self._keys())), self._on_add)

    def _on_add(self, path) -> None:
        if not path:
            return
        self.query_one("#settings-status", Label).update(f"adding + loading {path} …")
        self._add_worker(path)

    @work(thread=True, group="settings")
    def _add_worker(self, path: str) -> None:
        res = lifecycle.add_ssh_key(path)  # save to config + ssh-add now
        self.app.call_from_thread(self._after_mutation, res)

    @on(Button.Pressed, "#remove")
    def action_remove(self) -> None:
        key = self._selected_key()
        if key is None or key not in self._keys():
            self._status(lifecycle.Result(False, "no key selected"))
            return
        self._status(lifecycle.remove_ssh_key(key))  # config-only; fast, no shell
        self._refresh_status()

    @on(Button.Pressed, "#load")
    def action_load(self) -> None:
        self.query_one("#settings-status", Label).update("loading all configured keys …")
        self._load_worker()

    @work(thread=True, exclusive=True, group="settings")
    def _load_worker(self) -> None:
        res = lifecycle.ensure_ssh_keys(force=True)  # exclusive: a double 'l' can't stack two passes
        self.app.call_from_thread(self._after_mutation, res)

    def _after_mutation(self, res: lifecycle.Result) -> None:
        self._status(res)
        self._refresh_status()

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)
