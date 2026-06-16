"""Egress (strict-firewall) management screen.

A ``g``-from-the-Project-menu modal that surfaces a project's network boundary in one place:

* **Lock / Unlock** — flip strict egress (the squid allowlist proxy on a no-route ``--internal`` net)
  on or off. This recreates the container, so it does NOT run inline — the screen dismisses the target
  egress mode (``'strict'``/``'open'``) and the app applies it off the UI thread via
  ``lifecycle.set_egress`` (mirrors the ``recreate`` worker).
* **Allowlist extras** — add/remove the project's extra ``dstdomain`` entries on top of the always-on
  base allowlist. These are fast, flocked registry writes (``lifecycle.add_allow``/``remove_allow``), so
  they run inline like ``PortsScreen``; each reminds that a recreate re-renders squid.conf to apply.
* **Promote a blocked host** — pick a destination the sidecar actually BLOCKED (parsed from its access
  log via ``egress.summarize_access``) straight into the allowlist, the common allowlist-tuning loop.

Distinct from the always-on **Network panel** on the main screen (which shows live blocked/allowed
counts + traffic) — this screen is where you *change* the policy those counts reflect.
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ItemGrid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

from ... import lifecycle
from ...network import allowlist as allowlist_mod
from ...registry import projects
from .add_allow import AddAllowScreen

# How many base-allowlist example domains to name in the header note (the full set is long).
_BASE_SAMPLE = ("claude.ai", ".anthropic.com", ".github.com", "registry.npmjs.org", "pypi.org")
_BLOCKED_COLUMNS = ("Domain", "Blocked", "Allowed", "Ports", "Last tried")


def _fmt_ts(ts: str) -> str:
    """A squid ``%ts.%03tu`` epoch.ms field → local ``HH:MM:SS`` (best-effort; the raw string on a
    parse miss). The TUI may use real ``datetime`` (unlike the workflow sandbox)."""
    import datetime
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return ts


class EgressScreen(ModalScreen["str | None"]):
    """Manage ``slug``'s egress: lock/unlock + allowlist extras + promote-blocked.

    Dismisses a target egress mode (``'strict'`` to lock / re-apply, ``'open'`` to unlock) for the app
    to apply off-thread, or ``None`` when only inline allowlist edits were made (already persisted)."""

    BINDINGS = [
        Binding("l", "toggle", "Lock/Unlock"),
        Binding("a", "add", "Add"),
        Binding("x", "remove", "Remove"),
        Binding("b", "blocked", "Promote blocked"),
        Binding("r", "apply", "Apply (recreate)"),
        Binding("escape", "close", "Close"),
    ]
    CSS = """
    EgressScreen { align: center middle; }
    #dialog {
        width: 92; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #egress-mode { height: auto; padding-bottom: 1; }
    #egress-base { height: auto; color: $text-muted; padding-bottom: 1; }
    #allowlist { height: auto; max-height: 12; }
    #egress-status { height: auto; color: $text-muted; padding-top: 1; }
    /* ItemGrid wraps the action buttons into rows instead of cropping them off the
       dialog's right edge — see CLAUDE.md "TUI dialog button rows" (reflow, no crop). */
    #buttons { height: auto; padding-top: 1; grid-gutter: 0 1; }
    """

    def __init__(self, slug: str) -> None:
        super().__init__()
        self._slug = slug
        self._mode = "open"

    def compose(self) -> ComposeResult:
        base = allowlist_mod.build_allowlist(())
        with Vertical(id="dialog"):
            yield Label(f"Egress · {self._slug}", classes="title")
            yield Label("", id="egress-mode")
            yield Label(
                f"Base allowlist always permits {len(base)} domains "
                f"({', '.join(_BASE_SAMPLE)}, …). Extras below are this project's additions.",
                id="egress-base",
            )
            yield DataTable(id="allowlist", cursor_type="row")
            yield Label("", id="egress-status")
            with ItemGrid(id="buttons", min_column_width=16):
                yield Button("Lock", id="toggle", variant="primary")
                yield Button("Apply", id="apply")
                yield Button("Add", variant="success", id="add")
                yield Button("Remove", variant="error", id="remove")
                yield Button("Blocked…", id="blocked")
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#allowlist", DataTable).add_columns("Domain (extra)")
        self._refresh()
        self.query_one("#allowlist", DataTable).focus()

    # -- data -------------------------------------------------------------
    def _refresh(self) -> None:
        table = self.query_one("#allowlist", DataTable)
        table.clear()
        try:
            project = projects.load(self._slug)
        except Exception as exc:  # noqa: BLE001 - never tear down the app on a bad project load
            self._status(lifecycle.Result(False, f"failed to load {self._slug}: {exc}"))
            return
        self._mode = project.egress
        for host in project.allowlist:
            table.add_row(host, key=host)
        locked = self._mode == "strict"
        mode_txt = ("[$success]STRICT[/] — locked to the allowlist via the squid sidecar"
                    if locked else
                    "[$warning]OPEN[/] — no egress filtering (the allowlist isn't consulted)")
        self.query_one("#egress-mode", Label).update(f"Mode: {mode_txt}")
        self.query_one("#toggle", Button).label = "Unlock" if locked else "Lock"
        if not project.allowlist:
            table.add_row("(none — only the base allowlist applies)", key="")

    def _status(self, res: lifecycle.Result) -> None:
        colour = "$success" if res.ok else "$error"
        self.query_one("#egress-status", Label).update(f"[{colour}]{res.detail}[/]")

    def _selected_host(self) -> str | None:
        table = self.query_one("#allowlist", DataTable)
        if table.row_count == 0:
            return None
        try:
            key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:
            return None
        return key or None  # the "(none)" placeholder row has an empty key

    # -- inline allowlist edits (fast, registry-only) ---------------------
    @on(Button.Pressed, "#add")
    def action_add(self) -> None:
        self.app.push_screen(AddAllowScreen(self._slug), self._on_add)

    def _on_add(self, host) -> None:
        if not host:
            return
        self._status(lifecycle.add_allow(self._slug, host))
        self._refresh()

    @on(Button.Pressed, "#remove")
    def action_remove(self) -> None:
        host = self._selected_host()
        if host is None:
            self._status(lifecycle.Result(False, "no allowlist extra selected (base domains can't be removed)"))
            return
        self._status(lifecycle.remove_allow(self._slug, host))
        self._refresh()

    # -- promote a blocked destination ------------------------------------
    @on(Button.Pressed, "#blocked")
    def action_blocked(self) -> None:
        if self._mode != "strict":
            self._status(lifecycle.Result(False, "open egress — lock the project first so the sidecar "
                                                  "logs blocked destinations"))
            return
        self.query_one("#egress-status", Label).update("reading blocked destinations …")
        self._load_blocked()

    @work(thread=True, exclusive=True)
    def _load_blocked(self) -> None:
        """Fetch the sidecar's blocked destinations off the UI thread (a ``docker logs`` read), then
        raise the picker. Reuses the same ``egress.summarize_access`` aggregation the Network panel does."""
        from ...network import egress
        try:
            blocked = [s for s in egress.summarize_access(egress.access_log(self._slug)) if s.denied_count]
        except Exception as exc:  # noqa: BLE001 - a log read must never tear down the screen
            self.app.call_from_thread(self._status, lifecycle.Result(False, f"reading blocked log failed: {exc}"))
            return
        self.app.call_from_thread(self._show_blocked, blocked)

    def _show_blocked(self, blocked) -> None:
        if not blocked:
            self._status(lifecycle.Result(True, "no blocked destinations logged (nothing to promote)"))
            return
        self.app.push_screen(PromoteBlockedScreen(self._slug, blocked), self._on_promote)

    def _on_promote(self, host) -> None:
        if not host:
            return
        self._status(lifecycle.add_allow(self._slug, host))
        self._refresh()

    # -- lock/unlock/apply (heavy: recreate — handed to the app worker) ---
    @on(Button.Pressed, "#toggle")
    def action_toggle(self) -> None:
        self.dismiss("open" if self._mode == "strict" else "strict")

    @on(Button.Pressed, "#apply")
    def action_apply(self) -> None:
        if self._mode != "strict":
            self._status(lifecycle.Result(False, "open egress — press l to Lock (apply re-renders a "
                                                  "locked project's allowlist)"))
            return
        self.dismiss("strict")

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)


class PromoteBlockedScreen(ModalScreen["str | None"]):
    """Pick one destination the sidecar BLOCKED to add to the allowlist. Dismisses the chosen host
    string (a bare/dotted domain, port already stripped by ``egress.parse_access``) or ``None``."""

    BINDINGS = [
        Binding("enter,a", "promote", "Add to allowlist"),
        Binding("escape", "cancel", "Cancel"),
    ]
    CSS = """
    PromoteBlockedScreen { align: center middle; }
    #dialog {
        width: 88; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #blocked { height: auto; max-height: 16; }
    #blocked-hint { height: auto; color: $text-muted; padding-top: 1; }
    """

    def __init__(self, slug: str, blocked) -> None:
        super().__init__()
        self._slug = slug
        self._blocked = blocked

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Blocked destinations · {self._slug}", classes="title")
            yield DataTable(id="blocked", cursor_type="row")
            yield Label("enter / a Add the highlighted domain to the allowlist · esc Cancel "
                        "(recreate to apply)", id="blocked-hint")

    def on_mount(self) -> None:
        table = self.query_one("#blocked", DataTable)
        table.add_columns(*_BLOCKED_COLUMNS)
        for s in self._blocked:
            ports = ",".join(str(p) for p in s.ports) or "—"
            table.add_row(s.host, str(s.denied_count), str(s.allowed_count) or "0",
                          ports, _fmt_ts(s.last_seen), key=s.host)
        table.focus()

    def action_promote(self) -> None:
        table = self.query_one("#blocked", DataTable)
        if table.row_count == 0:
            self.dismiss(None)
            return
        try:
            host = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:
            host = None
        self.dismiss(host)

    def action_cancel(self) -> None:
        self.dismiss(None)
