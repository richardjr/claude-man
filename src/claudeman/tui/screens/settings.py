"""Settings screen — global claude-man config (Settings, ``,`` key).

The "general features" surface. First section: the ssh keys claude-man auto-loads into the host agent.
Lists each configured key with its live agent status (loaded / not loaded / missing), and manages them:
add (via ``AddKeyScreen`` — adding also ``ssh-add``s it now), remove (config only), and load-all.
Below it, one line per global preference (git identity, GH token, terminal, container memory cap)
each with its edit hotkey.

Mirrors ``EnvMountsScreen``: a ModalScreen with its own hotkeys + a status Label, and the slow,
shell-touching work (``ssh-add`` / ``ssh-keygen``) on a thread worker so the UI never blocks (and a
passphrase prompt can never reach this terminal — ``ssh_agent`` runs ssh-add non-interactively).
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ItemGrid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from ... import gh_token, gitconfig, lifecycle, ssh_agent
from ...registry import settings as settings_registry
from .. import terminals
from .add_key import AddKeyScreen
from .gh_token import GhTokenScreen
from .git_identity import GitIdentityScreen
from .memory_limit import MemoryLimitScreen
from .setup import SetupWizardScreen
from .terminal_custom import CustomTerminalScreen
from .terminal_select import TerminalSelectScreen

_KEY_COLUMNS = ("Ssh key", "Status")


class SettingsScreen(ModalScreen[None]):
    """View + manage global settings (ssh keys auto-loaded into the agent)."""

    BINDINGS = [
        Binding("a", "add", "Add key"),
        Binding("x", "remove", "Remove"),
        Binding("l", "load", "Load all"),
        Binding("g", "git_identity", "Git identity"),
        Binding("t", "gh_token", "GH token"),
        Binding("e", "terminal", "Terminal"),
        Binding("m", "memory", "Memory"),
        Binding("w", "wizard", "Setup wizard"),
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
    /* ItemGrid wraps the action buttons into rows instead of cropping them off the
       dialog's right edge — see CLAUDE.md "TUI dialog button rows" (reflow, no crop). */
    #buttons { height: auto; padding-top: 1; grid-gutter: 0 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Settings · ssh keys (auto-loaded into the agent on startup)", classes="title")
            yield DataTable(id="keys", cursor_type="row")
            yield Label("", id="git-identity", classes="panel-title")
            yield Label("", id="gh-token", classes="panel-title")
            yield Label("", id="terminal", classes="panel-title")
            yield Label("", id="memory", classes="panel-title")
            yield Label("a Add · x Remove · l Load all · g Git · t GH token · e Terminal · m Memory "
                        "· w Wizard · esc Close", id="settings-status")
            with ItemGrid(id="buttons", min_column_width=16):
                yield Button("Add", variant="success", id="add")
                yield Button("Remove", variant="error", id="remove")
                yield Button("Load all", id="load")
                yield Button("Git", id="git")
                yield Button("GH", id="ghtoken")
                yield Button("Terminal", id="term")
                yield Button("Memory", id="memory-btn")
                yield Button("Wizard", id="wizard")
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#keys", DataTable).add_columns(*_KEY_COLUMNS)
        self._render_git_identity()
        self._render_gh_token()
        self._render_terminal()
        self._render_memory()
        self._refresh_status()

    def _render_memory(self) -> None:
        s = settings_registry.load()
        self.query_one("#memory", Label).update(
            f"Container memory: {s.container_memory} hard cap, no swap · m to change (recreate to apply)"
        )

    def _render_terminal(self) -> None:
        s = settings_registry.load()
        if s.terminal_program == terminals.CUSTOM_PROGRAM:
            label = f"custom: {' '.join(s.terminal_command)}"
        elif s.terminal_program:
            label = s.terminal_program
        else:
            try:
                label = f"auto ({terminals.resolve_spec().name} detected)"
            except RuntimeError:
                label = "auto (none detected!)"
        self.query_one("#terminal", Label).update(
            f"Terminal (shell/claude/nvim windows): {label} · e to change"
        )

    def _render_gh_token(self) -> None:
        state = "set" if gh_token.is_set() else "(none)"
        self.query_one("#gh-token", Label).update(
            f"GH token (→ GH_TOKEN): {state} · t to edit (recreate to apply)"
        )

    def _render_git_identity(self) -> None:
        name, email = gitconfig.resolve_identity()
        s = settings_registry.load()
        src = "config" if (s.git_user_name or s.git_user_email) else ("host" if (name or email) else "unset")
        self.query_one("#git-identity", Label).update(
            f"Git identity: {name or '(unset)'} <{email or '(unset)'}>  [{src}] · g to edit (recreate to apply)"
        )

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

    @on(Button.Pressed, "#git")
    def action_git_identity(self) -> None:
        s = settings_registry.load()
        host_name, host_email = gitconfig._host_git("user.name"), gitconfig._host_git("user.email")
        self.app.push_screen(
            GitIdentityScreen(s.git_user_name, s.git_user_email, host_name, host_email), self._on_git
        )

    def _on_git(self, identity) -> None:
        if identity is None:
            return
        name, email = identity
        settings_registry.set_git_identity(name, email)
        self._render_git_identity()
        self._status(lifecycle.Result(True, "git identity saved — recreate a project to apply"))

    @on(Button.Pressed, "#ghtoken")
    def action_gh_token(self) -> None:
        self.app.push_screen(GhTokenScreen(gh_token.is_set()), self._on_gh_token)

    def _on_gh_token(self, result) -> None:
        if result is None:  # cancelled / empty input
            return
        action, token = result
        if action == "set":
            gh_token.save(token)  # 0600 state-tier; never echoed
            msg = "gh token saved — recreate a project to inject GH_TOKEN"
        else:
            msg = "gh token cleared — recreate to apply" if gh_token.clear() else "no gh token was set"
        self._render_gh_token()
        self._status(lifecycle.Result(True, msg))

    @on(Button.Pressed, "#term")
    def action_terminal(self) -> None:
        s = settings_registry.load()
        self.app.push_screen(
            TerminalSelectScreen(s.terminal_program, s.terminal_command), self._on_terminal
        )

    def _on_terminal(self, choice) -> None:
        if choice is None:  # cancelled
            return
        if choice == terminals.CUSTOM_PROGRAM:
            # A "custom" pick opens the template editor prefilled (that's also how an existing
            # template gets edited); program + template are persisted together on save.
            self.app.push_screen(
                CustomTerminalScreen(settings_registry.load().terminal_command),
                self._on_custom_terminal,
            )
            return
        settings_registry.set_terminal(program=choice)
        self._render_terminal()
        label = choice or "auto-detect"
        self._status(lifecycle.Result(True, f"terminal preference saved: {label}"))

    def _on_custom_terminal(self, template) -> None:
        if template is None:  # cancelled
            return
        try:
            settings_registry.set_terminal(program=terminals.CUSTOM_PROGRAM,
                                           command=list(template))
        except Exception as exc:  # noqa: BLE001 - the modal pre-validates; surface anything else
            self._status(lifecycle.Result(False, f"custom terminal not saved: {exc}"))
            return
        self._render_terminal()
        self._status(lifecycle.Result(True, f"custom terminal saved: {' '.join(template)}"))

    @on(Button.Pressed, "#memory-btn")
    def action_memory(self) -> None:
        s = settings_registry.load()
        self.app.push_screen(MemoryLimitScreen(s.container_memory), self._on_memory)

    def _on_memory(self, limit) -> None:
        if limit is None:  # cancelled
            return
        try:
            s = settings_registry.set_container_memory(limit or None)  # "" -> default
        except Exception as exc:  # noqa: BLE001 - the modal pre-validates; surface anything else
            self._status(lifecycle.Result(False, f"memory limit not saved: {exc}"))
            return
        self._render_memory()
        self._status(lifecycle.Result(
            True, f"container memory cap saved: {s.container_memory} — recreate a project to apply"
        ))

    @on(Button.Pressed, "#wizard")
    def action_wizard(self) -> None:
        """Re-run the first-run setup wizard on demand (checks / terminal / profile / image)."""
        self.app.push_screen(SetupWizardScreen())

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)
