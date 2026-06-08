"""Env-mounts management screen (Phase 3.x TUI).

A single ``e``-opened modal that lists a project's env mounts and manages them in place — add (via
``AddMountScreen``), remove, and resync — matching the operator's "config options where you can add /
delete one or more" model. Registry mutations (``lifecycle.add_mount``/``remove_mount``) are fast
(flocked TOML writes, no subprocess) so they run inline; ``resync`` shells out to docker (re-seeds the
ssh tmpfs) so it runs on a thread worker. Mounts are fixed at container create, so each result reminds
that a ``recreate`` is needed to apply — surfaced in the status line.
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from ... import config, lifecycle
from ...registry import projects
from .add_mount import AddMountScreen

_MOUNT_COLUMNS = ("Kind", "Source", "Dest", "Mode")


class EnvMountsScreen(ModalScreen[None]):
    """Manage ``slug``'s ssh + file env mounts (list / add / remove / resync)."""

    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("x", "remove", "Remove"),
        Binding("s", "resync", "Resync"),
        Binding("escape", "close", "Close"),
    ]
    CSS = """
    EnvMountsScreen { align: center middle; }
    #dialog {
        width: 92; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #mounts { height: auto; max-height: 12; }
    #mount-status { height: auto; color: $text-muted; padding-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str) -> None:
        super().__init__()
        self._slug = slug

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Env mounts · {self._slug}", classes="title")
            yield DataTable(id="mounts", cursor_type="row")
            yield Label("a Add · x Remove · s Resync · esc Close", id="mount-status")
            with Horizontal(id="buttons"):
                yield Button("Add", variant="success", id="add")
                yield Button("Remove", variant="error", id="remove")
                yield Button("Resync", id="resync")
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#mounts", DataTable).add_columns(*_MOUNT_COLUMNS)
        self._refresh()
        self.query_one("#mounts", DataTable).focus()

    # -- data -------------------------------------------------------------
    def _refresh(self) -> None:
        table = self.query_one("#mounts", DataTable)
        table.clear()
        try:
            mounts = projects.load(self._slug).env_mount
        except Exception as exc:  # noqa: BLE001 - never tear down the app on a bad project load
            self._status(lifecycle.Result(False, f"failed to load {self._slug}: {exc}"))
            return
        invalid = 0
        for m in mounts:
            if m.error:
                invalid += 1
                table.add_row(m.kind or "?", m.src or "-", m.dst or "-",
                              f"⚠ INVALID: {m.error}", key=(m.target or m.dst or f"bad{invalid}"))
            elif m.kind == "ssh":
                table.add_row("ssh", "host ssh-agent + ~/.ssh config,known_hosts",
                              config.CONTAINER_SSH_DIR, "agent-forward", key="ssh")
            elif m.kind == "env":
                table.add_row("env", "(value hidden — 0600 state tier)", m.name, "pass-through",
                              key=m.name)
            else:
                table.add_row("file", m.src, m.dst, "ro" if m.ro else "rw", key=m.dst)
        if invalid:
            self._status(lifecycle.Result(
                False, f"{invalid} mount(s) invalid — select the row and press x to remove, then re-add"))

    def _status(self, res: lifecycle.Result) -> None:
        colour = "$success" if res.ok else "$error"
        self.query_one("#mount-status", Label).update(f"[{colour}]{res.detail}[/]")

    def _selected_target(self) -> str | None:
        table = self.query_one("#mounts", DataTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:
            return None

    # -- actions ----------------------------------------------------------
    @on(Button.Pressed, "#add")
    def action_add(self) -> None:
        self.app.push_screen(AddMountScreen(self._slug), self._on_add)

    def _on_add(self, result) -> None:
        if result is None:
            return
        mount, value = result
        if mount.kind == "env":  # value -> 0600 state store; the registry holds only the name
            self._status(lifecycle.add_env_var(self._slug, mount.name, value))
        else:
            self._status(lifecycle.add_mount(self._slug, mount))
        self._refresh()

    @on(Button.Pressed, "#remove")
    def action_remove(self) -> None:
        target = self._selected_target()
        if target is None:
            self._status(lifecycle.Result(False, "no env mount selected"))
            return
        self._status(lifecycle.remove_mount(self._slug, target))
        self._refresh()

    @on(Button.Pressed, "#resync")
    def action_resync(self) -> None:
        self.query_one("#mount-status", Label).update("resyncing …")
        self._resync_worker()

    @work(thread=True, exclusive=True)
    def _resync_worker(self) -> None:
        res = lifecycle.resync(self._slug)
        self.app.call_from_thread(self._status, res)

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)
