"""The claude-man Textual application.

Phase-1 skeleton: a live projects table (registry JOINed with `docker ps`), bindings to
open a shell / claude / nvim in a detached terminal, start/stop, a modal new-project form
(slug/profile/overlay/egress; repos are a later increment), and placeholders for the log
pane and sync-review gate that later phases fill in.

`textual` is only imported here, so importing the CLI or running the tests never requires it.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Header, Label, RichLog, Static

from .. import config, lifecycle, usage
from ..checkout import gitstate
from ..checkout import repos as repos_mod
from ..docker import stats, status
from ..registry import profiles as profiles_registry
from ..registry import projects
from ..registry import schema
from . import rowfx
from . import terminals
from .screens.add_repo import AddRepoScreen
from .screens.create import NewProject, NewProjectScreen
from .screens.delete_project import DeleteProjectScreen
from .screens.egress import EgressScreen
from .screens.env_mounts import EnvMountsScreen
from .screens.logs import LogsScreen
from .screens.menu import MenuScreen
from .screens.overlay_select import OverlaySelectScreen
from .screens.model_pin import ModelPinScreen
from .screens.profile_select import ProfileSelectScreen
from .screens.profile_switch_confirm import ProfileSwitchConfirmScreen
from .screens.packs import PacksScreen
from .screens.ports import PortsScreen
from .screens.pull_confirm import PullConfirmScreen
from .screens.shutdown import ShutdownScreen
from .screens.stop_all_confirm import StopAllConfirmScreen
from .screens.models import ModelsScreen
from .screens.remove_repo import RemoveRepoScreen
from .screens.settings import SettingsScreen
from .screens.splash import SplashScreen
from .screens.sync_review import SyncReviewScreen
from .screens.update_confirm import UpdateConfirmScreen
from ..registry import settings as settings_registry
from . import splash as splash_mod

_COLUMNS = ("Project", "Status", "Profile", "Egress", "Model", "Repos", "Version", "Detail")
_REPO_COLUMNS = ("Dir", "Branch", "State", "↑/↓", "Last commit")
# Per-project network panel. Traffic = whole-container NetIO since start (docker stats). Blocked/Allowed
# = distinct destinations from the squid access log — locked projects only (open ones have no sidecar).
_NET_COLUMNS = ("Project", "Egress", "Blocked", "Allowed", "Traffic")
_USAGE_COLUMNS = ("Profile", "Account", "Token", "In", "Out", "Cache", "Total")
# Braille spinner frames for the header "work in progress" indicator (start/stop/recreate/… take time).
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# Band passes for a repo-panel git-change sweep ("a few times" — start/stop row sweeps run once).
_GIT_SWEEP_REPEATS = 3
# Responsive vertical breakpoints (terminal rows). The Projects table is `height: 1fr`, so when the
# panels below it consume all the vertical space it collapses to nothing — and Projects is the most
# important area. Below each threshold the named panel (title + table) is hidden so Projects + Repos
# (the protected top two) keep their rows. Drop order matches operator priority: Network first, then
# Token usage, then the message Log last. Projects + Repos are never dropped. Re-evaluated on resize.
_HIDE_NETWORK_BELOW = 40
_HIDE_TOKENS_BELOW = 33
_HIDE_LOG_BELOW = 26
# The two-line key bar replacing the stock Footer: row 1 = verbs acting on the SELECTED project row,
# row 2 = app-wide verbs — so the scope of every key is explicit at a glance. Display only — key
# dispatch stays in BINDINGS; keep the two in sync when adding a verb.
_KEYBAR = (
    "[dim]project[/] [b]↵[/b] shell  [b]c[/b] claude  [b]e[/b] editor  [b]b[/b] browse  "
    "[b]s[/b] start/stop  [b]g[/b] repos…  [b]p[/b] project…  [b]y[/b] sync-back\n"
    "[dim]global[/]  [b]n[/b] new  [b]S[/b] stop-all  [b]v[/b] view…  [b]m[/b] models  "
    "[b],[/b] settings  [b]q[/b] quit"
)


class ClaudeManApp(App):
    TITLE = "claude-man"
    CSS = """
    /* Projects is the priority area: 1fr to claim slack, min-height so a content-heavy Repos panel
       can never squeeze it to nothing within a breakpoint band (panels still drop on resize — see
       _reflow_panels). */
    #projects { height: 1fr; min-height: 6; }
    /* 13 = 10 repo rows + 1 header + 2 border — sized to show a full realistic repo list. */
    #repos { height: auto; max-height: 13; border: round $panel; }
    #profiles { height: auto; max-height: 10; border: round $panel; }
    #netactivity { height: auto; max-height: 8; border: round $panel; }
    .panel-title { color: $text-muted; padding: 0 1; }
    RichLog { height: 8; border: round $panel; }
    /* height: auto — on a narrow terminal the project row wraps onto a second line; a fixed
       height: 2 would clip the whole global row (including the quit hint) out of view. */
    #keybar { dock: bottom; height: auto; background: $panel; padding: 0 1; }
    /* App-wide cap so every modal's fixed-width #dialog (each screen sets its own `width:`)
       shrinks to fit a terminal narrower than that width instead of being clipped off the
       screen's right edge (there is no horizontal screen scroll, so clipped content is
       unreachable). `width` and `max-width` are different properties, so this leaves the
       designed width intact whenever the terminal has room. Paired with the ItemGrid button
       rows (which reflow inside the narrowed dialog) this makes dialogs never crop — see
       CLAUDE.md "TUI dialog button rows". splash.py uses #splash-fill, not #dialog, so the
       full-screen boot animation is unaffected. */
    #dialog { max-width: 100%; }
    """
    # The key bar stays compact: the highest-frequency verbs are top-level single keys; the
    # lower-frequency repo / lifecycle / view verbs live behind the g/p/v submenus (MenuScreen) so the
    # bar doesn't grow a key per action. Add/Remove-repo, Refresh-git, Pull-all -> g; Env-mounts,
    # Ports, Packs, Egress, Overlay, Model, Profile, Recreate, Delete -> p; Usage/Logs -> v. The action_* handlers reused
    # unchanged, dispatched via _on_menu_pick. Order mirrors _KEYBAR: project-scoped first, then
    # global (the custom #keybar Static renders the grouping; descriptions still feed the palette).
    BINDINGS = [
        # project-scoped — act on the cursor's row
        Binding("enter", "open_shell", "Shell"),
        Binding("c", "open_claude", "Claude"),
        Binding("e", "open_editor", "Editor (nvim)"),
        Binding("b", "browse", "Browse"),
        Binding("s", "toggle_running", "Start/Stop"),
        Binding("g", "repos_menu", "Repos…"),
        Binding("p", "project_menu", "Project…"),
        Binding("y", "sync_review", "Sync-back"),
        # global — app-wide
        Binding("n", "new_project", "New"),
        # Bind BOTH "S" and "shift+s": terminals WITHOUT the Kitty keyboard protocol report Shift+S
        # as the character "S", but terminals WITH it (ghostty/kitty/foot/wezterm) report "shift+s"
        # (textual's _xterm_parser rewrites an uppercase CSI-u name to shift+<lower>). A lone
        # Binding("S") silently never fires under Kitty mode — Shift+S looks dead and stop-all appears
        # frozen. The alias is show=False so it's not a duplicate row in the command palette.
        Binding("S", "stop_all", "Stop all", key_display="S"),
        Binding("shift+s", "stop_all", "Stop all", show=False),
        Binding("v", "view_menu", "View…"),
        Binding("comma", "settings", "Settings", key_display=","),
        Binding("m", "models", "Models"),
        Binding("q", "quit", "Quit"),
    ]
    # Submenu rows: (key, label, token). Tokens are routed to the action_* handlers by the dict in
    # _on_menu_pick.
    _REPOS_MENU = [
        ("a", "Add repo", "add_repo"),
        ("x", "Remove repo", "remove_repo"),
        ("r", "Refresh-git (fetch)", "refresh_git"),
        ("p", "Pull all (ff-only)", "pull_all"),
    ]
    _PROJECT_MENU = [
        ("e", "Env mounts", "env_mounts"),
        ("o", "Ports", "ports"),
        ("p", "Packs…", "packs"),
        ("g", "Egress…", "egress"),
        ("i", "Overlay (image)…", "overlay"),
        ("m", "Model (local)…", "model_pin"),
        ("f", "Profile…", "profile"),
        ("r", "Recreate", "recreate"),
        ("d", "Delete", "delete"),
    ]
    _VIEW_MENU = [
        ("u", "Refresh usage", "refresh_usage"),
        ("l", "Logs (live)", "logs"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield DataTable(id="projects", cursor_type="row")
            yield Label("Repos · —", id="repos-title", classes="panel-title")
            yield DataTable(id="repos")
            yield Label("Token usage per profile (containers)",
                        id="profiles-title", classes="panel-title")
            yield DataTable(id="profiles")
            yield Label("Network · Traffic since container start · Blocked/Allowed = locked projects "
                        "(g for live detail)", id="netactivity-title", classes="panel-title")
            yield DataTable(id="netactivity")
            yield RichLog(id="log", highlight=True, markup=True)
        yield Static(_KEYBAR, id="keybar")

    def on_mount(self) -> None:
        # Slugs with an in-flight create/up/recreate worker. Same-slug lifecycle ops must not overlap
        # (they race on the container name + the image build); different slugs may run in parallel.
        # Touched only on the UI thread (action handlers + `_after_action` via `call_from_thread`),
        # so the set needs no lock.
        self._busy: set[str] = set()
        # slug -> human verb for the in-flight op (start/stop/recreate/…), shown by the header spinner.
        # Read against `_busy` (the source of truth) so a stale entry for a no-longer-busy slug is ignored.
        self._busy_verbs: dict[str, str] = {}
        self._spin = 0  # header spinner frame index (animated by _tick_spinner while anything is busy)
        self._last_rows: list[status.Row] = []  # last polled join; _running_slugs reads this (no docker ps)
        # True once a stop-all pass is active (confirm modal open, or the stop-all worker running), so a
        # second `S` doesn't stack another confirm or a second concurrent stop-all pass.
        self._stopping_all = False
        self._shutdown_screen: ShutdownScreen | None = None  # the stop-all progress modal, while running
        # slug -> last git-state scan summary (UI-thread only). Host-FS state on a deliberate refresh
        # cadence — distinct from container liveness (never cached); see refresh_gitstate.
        self._gitstate: dict[str, gitstate.ProjectGitSummary] = {}
        self._gitstate_at: float | None = None  # monotonic of the last completed scan, for the panel title
        self._gitstate_seq = 0       # dispatch counter; the latest-dispatched scan wins the cache merge
        self._gitstate_applied = 0   # seq of the last applied batch (drops a slower batch finishing late)
        # slug -> monotonic start for project rows mid state-change sweep, and the plain cell texts
        # the frames are built from (also the settle repaint once the band exits). Both are
        # rebuilt/pruned by _render_projects, so a deleted or reused slug can't replay a stale sweep.
        self._rowfx: dict[str, float] = {}
        self._row_cells: dict[str, list[str]] = {}
        # slug -> the Project name's stable identity colour (config.project_name_color — a SHA-256 pick
        # from the curated palette, the SAME per-project colour the spawned-terminal identity uses, so a
        # project reads as one colour everywhere). Rebuilt by _render_projects; read by _settle_row +
        # _fx_styles so the resting paint, the post-sweep settle, and the sweep base all agree.
        self._name_colors: dict[str, str] = {}
        # (slug, repo dir) -> monotonic start for repo-panel rows whose git state just changed (went
        # dirty / ahead-behind moved / new commit). Repeated passes (_GIT_SWEEP_REPEATS) — the scan is
        # a 30 s background tick, so one quick band is easy to miss. Armed by _apply_gitstate's diff;
        # frames paint only while that project is under the cursor (the panel shows one project).
        self._repofx: dict[tuple[str, str], float] = {}
        table = self.query_one("#projects", DataTable)
        self._proj_cols = table.add_columns(*_COLUMNS)
        self._repos_col = self._proj_cols[_COLUMNS.index("Repos")]
        self._repo_cols = self.query_one("#repos", DataTable).add_columns(*_REPO_COLUMNS)
        self.query_one("#profiles", DataTable).add_columns(*_USAGE_COLUMNS)
        self.query_one("#netactivity", DataTable).add_columns(*_NET_COLUMNS)
        # The side panels are read-only displays. Keep them OUT of the focus chain so the global
        # single-key verbs only ever fire from the projects table — never from a panel the operator
        # tabbed into. Otherwise focus could land on (say) the Log panel and `enter` -> open a Shell,
        # or `c`/`s` act with the panel focused. #projects stays the one interactive table, so it
        # keeps focus and the global bindings always have the right project context.
        for pid in ("#repos", "#profiles", "#netactivity", "#log"):
            self.query_one(pid).can_focus = False
        # Hide the global keybar whenever a sub-menu / dialog is on top, so the bottom bar never
        # advertises the global keys over a modal. They are already inert there (Textual's modal
        # binding chain excludes the App), but showing them reads as "still live" — misleading. Each
        # modal renders its own keys inside its dialog. Restored on return to the base screen. Driven
        # by screen_change_signal (fires on every push/pop) — see _sync_keybar.
        self.screen_change_signal.subscribe(self, self._sync_keybar)
        self._reflow_panels()  # set the initial panel set for the starting terminal size
        self.refresh_projects()
        self.refresh_usage()
        self._dispatch_gitstate(fetch=False)
        self._render_repo_detail()
        self._bootstrap_env()  # load configured ssh keys into the agent so containers can use them
        # Poll container liveness OFF the UI thread (refresh_projects is a thread worker). 10s is plenty
        # for a status column — every lifecycle action triggers an immediate refresh too. Phase 2 would
        # replace the poll with a `docker events` worker.
        self.set_interval(10.0, self.refresh_projects)
        self.set_interval(15.0, self.refresh_usage)                          # usage changes slowly; off thread
        self.set_interval(30.0, lambda: self._dispatch_gitstate(fetch=False))  # fetch-less git scan, off thread
        self.set_interval(0.2, self._tick_spinner)  # animate the header spinner while any op is in flight
        # Row state-change sweeps: 30 fps only while one is live (resumed by _render_projects when a
        # status flip is detected, paused again by _tick_rowfx once the last band exits).
        self._fx_timer = self.set_interval(1 / rowfx.FPS, self._tick_rowfx, pause=True)
        # Boot splash, pushed LAST: everything above is already scheduled, so the table fills
        # underneath while the logo plays and the scroll-off reveals live data, not an empty shell.
        # Skipped when disabled (`config splash off`) or the terminal can't fit the logo.
        try:
            show_splash = settings_registry.load().ui_splash
        except Exception:  # noqa: BLE001 - a bad config must not block startup
            show_splash = True
        if show_splash and self.size.width > len(splash_mod.LOGO[0]) + 2 \
                and self.size.height > len(splash_mod.LOGO) + 5:
            self.push_screen(SplashScreen())

    def _sync_keybar(self, _screen: object = None) -> None:
        """Show the global keybar only on the base screen; hide it under any pushed modal/sub-menu.
        Subscribed to ``screen_change_signal`` (on_mount), so it fires on every push/pop. The stack
        is already settled at publish time, so depth 1 == base screen, deeper == a modal is on top."""
        try:
            keybar = self.query_one("#keybar", Static)
        except Exception:  # noqa: BLE001 - keybar absent (teardown) must never crash a screen change
            return
        keybar.display = len(self.screen_stack) <= 1

    def on_resize(self, _event: object = None) -> None:
        self._reflow_panels()

    def _reflow_panels(self) -> None:
        """Drop the lower-priority panels as the terminal shrinks so the Projects + Repos tables (the
        protected top two) always keep their rows. Order: Network -> Token usage -> Log. See the
        ``_HIDE_*_BELOW`` constants. Hidden tables still receive their refresh writes — they just
        reclaim their rows for Projects until the terminal is tall enough to show them again."""
        h = self.size.height
        for wid, show in (
            ("#netactivity-title", h >= _HIDE_NETWORK_BELOW),
            ("#netactivity", h >= _HIDE_NETWORK_BELOW),
            ("#profiles-title", h >= _HIDE_TOKENS_BELOW),
            ("#profiles", h >= _HIDE_TOKENS_BELOW),
            ("#log", h >= _HIDE_LOG_BELOW),
        ):
            try:
                self.query_one(wid).display = show
            except Exception:  # noqa: BLE001 - a panel absent mid-teardown must not crash a resize
                pass

    # -- data -------------------------------------------------------------
    def _rows(self) -> list[status.Row]:
        defined = [
            (p.slug, p.profile or "(default)", p.egress, len(p.repos), p.model)
            for p in projects.list_projects()
        ]
        return status.join(defined, status.query_containers())

    @work(thread=True, exclusive=True, group="status")
    def refresh_projects(self) -> None:
        """Query docker (`docker ps`) + the registry OFF the UI thread, then repaint on it.

        The status query is a hundreds-of-ms `docker ps` subprocess; running it on the event loop
        every poll froze all input (the lag that made the modals feel unresponsive — TUI-2). Mirrors
        refresh_usage/refresh_gitstate: the thread worker computes the join, `call_from_thread` paints.
        ``exclusive`` drops a still-pending poll if a fresh one (e.g. a post-action refresh) supersedes it.
        """
        rows = self._rows()
        self.call_from_thread(self._render_projects, rows)

    def _render_projects(self, rows: list[status.Row]) -> None:
        """Paint the projects table from a freshly-polled join (UI thread only)."""
        table = self.query_one("#projects", DataTable)
        # Restore the cursor by SLUG, not integer index — rows are slug-sorted and the set can
        # change between polls, so an index restore could land on the wrong project (review TUI-7).
        prev_slug = self._current_slug()
        # Status flips since the LAST paint trigger the row sweep — one diff point covers every path
        # (s/start-stop, stop-all, an external `docker stop`), since lifecycle workers and the 10 s poll
        # all repaint through here. Diffed before _last_rows is overwritten; only UP transitions animate
        # (DEFINED<->STOPPED is container bookkeeping, not a start/stop).
        prev_kinds = {r.slug: r.kind for r in self._last_rows}
        table.clear()
        # Cache for _running_slugs (quit) AND the action handlers — so neither blocks on a fresh docker ps.
        self._last_rows = rows
        cells_map: dict[str, list[str]] = {}
        # Per-project name colour: the stable, unique identity colour keyed on the slug
        # (config.project_name_color — the same generation the spawned-terminal tint uses). Stored per
        # slug so the sweep + settle repaints reuse the exact same colour.
        self._name_colors = {r.slug: config.project_name_color(r.slug) for r in rows}
        for row in rows:
            cells = [row.slug, row.kind, row.profile, row.egress, row.model or "-",
                     self._repos_cell(row), row.version or "-", row.status_text or "-"]
            cells_map[row.slug] = cells
            # Colour the Project name with its gradient tint, and the Status cell green = UP /
            # red = STOPPED / yellow = DEFINED (both obvious at a glance).
            name = Text(cells[0], style=self._name_colors[row.slug])
            kind = Text(row.kind, style=status.status_style(row.kind))
            table.add_row(name, kind, *cells[2:], key=row.slug)
            prev = prev_kinds.get(row.slug)
            if prev is not None and prev != row.kind and status.UP in (prev, row.kind):
                self._rowfx[row.slug] = time.monotonic()
        self._row_cells = cells_map
        self._rowfx = {s: v for s, v in self._rowfx.items() if s in cells_map}
        if self._rowfx:
            self._fx_timer.resume()
            self._tick_rowfx()  # paint the first frame NOW — the repaint above drew the plain row
        if prev_slug is not None:
            slugs = [r.slug for r in rows]
            if prev_slug in slugs:
                table.move_cursor(row=slugs.index(prev_slug))
        # Repaint the Network panel from the SAME fresh join — it reads `_last_rows` for running-state +
        # egress, so driving it from here (rather than its own timer) means it never reads a stale set,
        # and it tracks every status flip / post-action refresh automatically. `exclusive` drops overlaps.
        self.refresh_net()

    # -- network activity panel (per-project traffic + blocked/allowed) ----
    @work(thread=True, exclusive=True, group="net")
    def refresh_net(self) -> None:
        """Build the per-project Network panel off the UI thread (mirrors refresh_usage/refresh_gitstate).

        Traffic is the whole-container NetIO since start, read for every RUNNING agent in ONE
        ``docker stats`` call. Blocked/Allowed are the distinct denied/allowed destinations from the
        squid access log — only for running *locked* projects (open ones have no sidecar). Both reads
        are time-bounded so a wedged daemon can't stall the worker; failures degrade to ``—``/``?``.
        """
        from ..network import egress

        rows_snapshot = list(self._last_rows)  # bind the reference; it's replaced wholesale on the UI thread
        running = [r for r in rows_snapshot if r.kind == status.UP]
        netio = stats.container_net_io([config.container_name(r.slug) for r in running])

        dim = lambda s: Text(s, style="dim")  # noqa: E731
        out: list[tuple] = []
        for r in rows_snapshot:
            if r.kind != status.UP:
                out.append((r.slug, r.egress, dim("—"), dim("—"), dim("—")))
                continue
            traffic = netio.get(config.container_name(r.slug)) or dim("—")
            if r.egress != "strict":
                out.append((r.slug, r.egress, dim("—"), dim("—"), traffic))
                continue
            try:
                summary = egress.summarize_access(egress.access_log(r.slug))
                n_blocked = sum(1 for s in summary if s.denied_count)
                n_allowed = sum(1 for s in summary if s.allowed_count)
            except Exception:  # noqa: BLE001 - a log read must never tear down the worker
                out.append((r.slug, r.egress, dim("?"), dim("?"), traffic))
                continue
            blocked_cell = Text(str(n_blocked), style="red bold") if n_blocked else dim("0")
            allowed_cell = Text(str(n_allowed), style="green") if n_allowed else dim("0")
            out.append((r.slug, r.egress, blocked_cell, allowed_cell, traffic))
        self.call_from_thread(self._render_net, out)

    def _render_net(self, rows: list[tuple]) -> None:
        table = self.query_one("#netactivity", DataTable)
        table.clear()
        for row in rows:
            table.add_row(*row)

    @work(thread=True, exclusive=True, group="usage")
    def refresh_usage(self) -> None:
        """Scan transcripts + aggregate per-profile usage off the UI thread (review TUI-2 pattern)."""
        data = usage.usage_by_profile()
        h = usage.human
        rows: list[tuple] = []
        for name in sorted(data):
            u = data[name]
            try:
                acct = profiles_registry.load(name).account_email or "-"
            except FileNotFoundError:
                acct = "-"
            age = profiles_registry.token_age_days(name)
            tok = "none" if age is None else (f"{int(age)}d" + ("!" if age > 330 else ""))
            rows.append((name, acct, tok, h(u.input), h(u.output),
                         h(u.cache_creation + u.cache_read), h(u.total)))
        self.call_from_thread(self._render_usage, rows)

    def _render_usage(self, rows: list[tuple]) -> None:
        table = self.query_one("#profiles", DataTable)
        table.clear()
        for row in rows:
            table.add_row(*row)

    # -- git state (repos column + detail panel) --------------------------
    def _repos_cell(self, row: status.Row) -> str:
        """The Repos column: the live git-state summary if scanned, else the registry count + '…'."""
        summary = self._gitstate.get(row.slug)
        return summary.line if summary is not None else f"{row.repos} …"

    def _dispatch_gitstate(self, *, fetch: bool) -> None:
        """Kick off a scan from the UI thread, stamping it with a monotonic dispatch seq.

        ``exclusive=True`` only cancels the *awaiting* wrapper — it can't preempt a thread worker that
        is already running its (possibly fetch-ful, multi-second) body. So a fetch-ful ``g`` and the
        next fetch-less 30 s tick can run in parallel; the seq lets ``_apply_gitstate`` keep the
        latest-dispatched batch and drop a slower older one that finishes late.
        """
        self._gitstate_seq += 1
        self.refresh_gitstate(self._gitstate_seq, fetch)

    @work(thread=True, exclusive=True, group="gitstate")
    def refresh_gitstate(self, seq: int, fetch: bool = False) -> None:
        """Scan every project's repos host-side off the UI thread (mirrors refresh_usage).

        The 30 s background tick runs **fetch-less** (all-local porcelain status, sub-ms per repo) so it
        never blocks; the on-demand ``g`` action passes ``fetch=True`` so ahead/behind reflects the
        remote. ``live`` is returned alongside the results so ``_apply_gitstate`` can prune slugs gone
        from the registry. Container *liveness* (the Status column) stays fresh every 2 s and uncached —
        only this host-FS git state is cached between scans (invariant 4).
        """
        live = projects.list_slugs()
        results: dict[str, gitstate.ProjectGitSummary] = {}
        for slug in live:
            try:
                project = projects.load(slug)
                if fetch and project.repos:
                    repos_mod.fetch_all(project)  # network; failures are fine — state reflects the fetch
                results[slug] = gitstate.summarize(gitstate.project_states(project))
            except Exception:  # noqa: BLE001 - never tear down the app from a worker
                continue
        self.call_from_thread(self._apply_gitstate, seq, tuple(live), results)

    def _apply_gitstate(
        self, seq: int, live: tuple[str, ...], results: dict[str, gitstate.ProjectGitSummary]
    ) -> None:
        if seq < self._gitstate_applied:
            return  # a newer scan already won (last-writer-by-dispatch-seq; threads can't be preempted)
        self._gitstate_applied = seq
        live_set = set(live)
        # Arm a repeated repo-row sweep for every repo whose VISIBLE row changed (went dirty,
        # ahead/behind moved, new commit, branch switch) — diffed against the previous scan BEFORE
        # the merge below overwrites it. A slug's first scan arms nothing (no baseline to differ
        # from), so startup never plays a wall of sweeps.
        now = time.monotonic()
        for slug, new_summary in results.items():
            prev = self._gitstate.get(slug)
            if prev is None:
                continue
            prev_by_dir = {s.dir: s for s in prev.states}
            for s in new_summary.states:
                p = prev_by_dir.get(s.dir)
                if p is not None and self._repo_cells(p) != self._repo_cells(s):
                    self._repofx[(slug, s.dir)] = now
        # Merge keeps last-good for a slug whose scan threw this pass; the prune drops slugs gone from
        # the registry so a reused slug never renders the old project's repos (and the cache can't grow).
        self._gitstate = {k: v for k, v in {**self._gitstate, **results}.items() if k in live_set}
        self._gitstate_at = time.monotonic()
        live_fx = {(slug, s.dir) for slug, summ in self._gitstate.items() for s in summ.states}
        self._repofx = {k: v for k, v in self._repofx.items() if k in live_fx}
        self._paint_repos_column()  # update only the Repos cells from cache — no UI-thread docker ps
        self._render_repo_detail()
        if self._repofx:
            self._fx_timer.resume()
            self._tick_rowfx()  # paint the first frame NOW — the panel render above drew base rows

    def _paint_repos_column(self) -> None:
        """Repaint the Repos column from the cache without a full ``refresh_projects`` (no docker ps)."""
        table = self.query_one("#projects", DataTable)
        for slug, summary in self._gitstate.items():
            if (cells := self._row_cells.get(slug)) is not None:
                cells[_COLUMNS.index("Repos")] = summary.line  # keep sweep frames + settle in sync
            if slug in self._rowfx:
                continue  # mid-sweep — the 30 fps tick paints this row from the updated cache
            try:
                table.update_cell(slug, self._repos_col, summary.line, update_width=True)
            except Exception:  # noqa: BLE001 - row not rendered yet; the 2 s poll paints it from cache
                pass

    def _render_repo_detail(self) -> None:
        """Render the per-repo detail panel for the cursor's project (pure — reads the cache only)."""
        panel = self.query_one("#repos", DataTable)
        title = self.query_one("#repos-title", Label)
        panel.clear()
        slug = self._current_slug()
        if slug is None:
            title.update("Repos · —")
            return
        summary = self._gitstate.get(slug)
        if summary is None:
            title.update(f"Repos · {slug}  (scanning…)")
            return
        age = "" if self._gitstate_at is None else f"  (scanned {int(time.monotonic() - self._gitstate_at)}s ago · g)"
        title.update(f"Repos · {slug}{age}")
        if not summary.states:
            panel.add_row("(no repos — press 'a' to add one)", "", "", "", "")
            return
        for s in summary.states:
            panel.add_row(*self._repo_row_values(s), key=s.dir)

    @staticmethod
    def _repo_cells(s: gitstate.RepoState) -> tuple[list[str], list[str | None]]:
        """One repo-panel row as (plain cell texts, base styles) — the single source for the resting
        paint, the sweep frames, AND the change diff in _apply_gitstate (cells differ == row visibly
        changed). State is green/red/yellow (clean/dirty/advisory); ↑/↓ pops yellow when non-0/0."""
        cells = [s.dir, gitstate.branch_label(s), gitstate.state_label(s),
                 gitstate.ab_label(s), gitstate.commit_label(s)]
        return cells, [None, None, gitstate.state_style(s), gitstate.ab_style(s), None]

    def _repo_row_values(self, s: gitstate.RepoState) -> list:
        """The repo-panel row's resting cell values (styled Text where a base style applies)."""
        cells, styles = self._repo_cells(s)
        return [Text(c, style=st) if st else c for c, st in zip(cells, styles)]

    def on_data_table_row_highlighted(self, event) -> None:
        # Cursor moved on the projects table -> repaint the detail panel for the newly selected slug.
        # Filter to #projects so the profiles/repos panels' own cursors don't trigger it.
        if getattr(event, "data_table", None) is not None and event.data_table.id == "projects":
            self._render_repo_detail()

    def _current_slug(self) -> str | None:
        table = self.query_one("#projects", DataTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:
            return None

    def _log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def _thread_log(self, message: str) -> None:
        """Forward a progress line from a worker thread to the log pane (UI-thread safe).

        Passed as ``on_progress`` to the lifecycle so a one-time ``docker build`` streams into the
        log instead of blocking silently.
        """
        self.call_from_thread(self._log, message)

    def _reserve(self, slug: str, verb: str) -> bool:
        """Claim ``slug`` for a lifecycle worker, or refuse if one is already running for it.

        Prevents a second create/up/recreate (or a stop) from racing an in-flight one on the same
        project — e.g. hitting Recreate mid-build would ``docker rm -f`` the container another worker
        is still creating. Cleared in ``_after_action``.
        """
        if slug in self._busy:
            self._log(f"[yellow]{slug}: {verb} skipped — an operation is already running[/]")
            return False
        self._busy.add(slug)
        self._busy_verbs[slug] = verb  # for the header spinner
        return True

    def _tick_spinner(self) -> None:
        """Animate a header spinner listing in-flight lifecycle ops (start/stop/recreate/…); clear when
        idle. Cheap: a no-op when nothing is busy. Reads ``_busy`` (the source of truth) for membership."""
        if not self._busy:
            if self.sub_title:
                self.sub_title = ""
            return
        self._spin = (self._spin + 1) % len(_SPINNER)
        ops = ", ".join(f"{self._busy_verbs.get(s, 'working')} {s}" for s in sorted(self._busy))
        self.sub_title = f"{_SPINNER[self._spin]} {ops}"

    @staticmethod
    def _fx_styles(cells: list[str], name_style: str | None) -> list[str | None]:
        """Base styles for sweep frames: the Project name (index 0) in its gradient tint and the
        Status cell (index 1) in green/red/yellow; the rest render unstyled outside the band."""
        return [name_style, status.status_style(cells[1])] + [None] * (len(cells) - 2)

    def _tick_rowfx(self) -> None:
        """Advance every live sweep one frame (30 fps; paused when none are live).

        Drives both the project-row state-change sweeps and the repo-panel git-change sweeps.
        Frames come from the pure ``rowfx.frame_cells`` (splash pattern) and land via per-cell
        ``update_cell`` — never a row rebuild, so cursors and row keys are untouched. A row that
        vanished mid-sweep (delete / cursor moved off the project) is dropped or painted blind;
        a finished sweep settles the row back to its resting paint.
        """
        if not self._rowfx and not self._repofx:
            self._fx_timer.pause()
            return
        now = time.monotonic()
        if self._rowfx:
            self._tick_project_sweeps(now)
        if self._repofx:
            self._tick_repo_sweeps(now)

    def _tick_project_sweeps(self, now: float) -> None:
        table = self.query_one("#projects", DataTable)
        for slug, t0 in list(self._rowfx.items()):
            cells = self._row_cells.get(slug)
            if cells is None or slug not in table.rows:
                del self._rowfx[slug]
                continue
            frame = rowfx.frame_cells(cells, now - t0,
                                      styles=self._fx_styles(cells, self._name_colors.get(slug)))
            if frame is None:  # band has exited — settle to the resting paint
                del self._rowfx[slug]
                self._settle_row(table, slug, cells)
                continue
            for col, markup in zip(self._proj_cols, frame):
                table.update_cell(slug, col, Text.from_markup(markup))

    def _tick_repo_sweeps(self, now: float) -> None:
        """Repo-panel sweeps tick on TIME for every armed (slug, dir) but paint only the rows the
        panel is actually showing (the cursor's project) — moving the cursor onto a project
        mid-sweep picks the animation up in flight; an expired hidden sweep just drops."""
        panel = self.query_one("#repos", DataTable)
        shown = self._current_slug()
        for (slug, dir_), t0 in list(self._repofx.items()):
            summary = self._gitstate.get(slug)
            s = next((x for x in summary.states if x.dir == dir_), None) if summary else None
            if s is None:  # repo/project gone from the cache mid-sweep
                del self._repofx[(slug, dir_)]
                continue
            cells, styles = self._repo_cells(s)
            frame = rowfx.frame_cells(cells, now - t0, styles=styles, repeats=_GIT_SWEEP_REPEATS)
            visible = slug == shown and dir_ in panel.rows
            if frame is None:
                del self._repofx[(slug, dir_)]
                if visible:  # settle to the resting paint (hidden rows are already at rest)
                    for col, value in zip(self._repo_cols, self._repo_row_values(s)):
                        panel.update_cell(dir_, col, value)
                continue
            if visible:
                for col, markup in zip(self._repo_cols, frame):
                    panel.update_cell(dir_, col, Text.from_markup(markup))

    def _settle_row(self, table: DataTable, slug: str, cells: list[str]) -> None:
        """Repaint one row's cells to their resting values (post-sweep; mirrors _render_projects)."""
        name = Text(cells[0], style=self._name_colors.get(slug) or "")
        values = [name, Text(cells[1], style=status.status_style(cells[1])), *cells[2:]]
        for col, value in zip(self._proj_cols, values):
            table.update_cell(slug, col, value)

    # -- actions ----------------------------------------------------------
    def action_open_shell(self) -> None:
        self._open_terminal("bash")

    def action_open_claude(self) -> None:
        self._open_terminal("claude")

    def action_open_editor(self) -> None:
        self._open_terminal("nvim")

    def action_browse(self) -> None:
        """Open the project's workspace mount (the host-side `/workspace` bind dir) in the system file
        manager. Works regardless of container state — the workspace is a host dir that exists once the
        project is created (pre-made here if absent; it's operator-owned, which the create flow expects)."""
        slug = self._current_slug()
        if not slug or not projects.exists(slug):  # TUI-6: act on real registry entries only
            self._log("[red]browse: select a defined project (orphan rows aren't managed)[/]")
            return
        ws = config.workspace_dir(slug)
        try:
            ws.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._log(f"[red]browse: cannot access {ws}: {exc}[/]")
            return
        try:
            terminals.spawn_path(str(ws))
        except (RuntimeError, OSError) as exc:
            self._log(f"[red]browse failed: {exc}[/]")
            return
        self._log(f"[green]browsing[/] {ws}")

    def _open_terminal(self, program: str) -> None:
        """Open a detached terminal running ``program`` (bash/claude/nvim) in the cursor's project.

        A ``docker exec`` needs a RUNNING container, so a STOPPED/DEFINED project is started first —
        in a worker, since the start may have to build the image — and the terminal is spawned only
        once it's up. Previously this opened a window whose ``docker exec`` failed against a dead
        container. An already-UP project takes the fast path (exec straight in); an orphan container
        with no registry entry can't be started by us, so it's declined like action_toggle_running.
        """
        slug = self._current_slug()
        if not slug:
            return
        # Refuse while any lifecycle op is mid-flight for this slug (mirrors action_toggle_running).
        # Critical for the start path below: a press during an in-flight _up_then_spawn_worker must
        # not take the UP fast path the 2 s poll has just exposed and spawn a duplicate window — the
        # worker opens the terminal itself once the container is up.
        if slug in self._busy:
            verb = {"claude": "claude", "nvim": "editor"}.get(program, "shell")
            self._log(f"[yellow]{slug}: {verb} skipped — an operation is already running[/]")
            return
        row = next((r for r in self._last_rows if r.slug == slug), None)  # cached join — no UI-thread docker ps
        if row is None:
            return
        if row.kind == status.UP:
            self._spawn_terminal(slug, program)  # already running — exec straight in
            return
        if not projects.exists(slug):
            self._log(f"[red]{slug}: orphan container (no registry entry) — not managed[/]")
            return
        if not self._reserve(slug, f"start+{program}"):
            return
        self._log(f"starting {slug} before opening {program} …")
        self._up_then_spawn_worker(slug, program)

    def _spawn_terminal(self, slug: str, program: str) -> None:
        """Spawn the detached terminal window (UI thread). Wrapped so a missing terminal binary
        (RuntimeError from build_argv) or a spawn failure logs instead of bubbling up."""
        spawn, verb, past = {
            "claude": (terminals.spawn_claude, "claude", "launched"),
            "nvim": (terminals.spawn_nvim, "editor", "opened"),
        }.get(program, (terminals.spawn_shell, "shell", "opened"))
        try:
            spawn(slug)
        except (RuntimeError, OSError) as exc:
            self._log(f"[red]{verb} for {slug} failed: {exc}[/]")
            return
        self._log(f"[green]{verb}[/] {past} for {slug}")

    @work(thread=True, group="create")
    def _up_then_spawn_worker(self, slug: str, program: str) -> None:
        """Start (create-if-needed) off the UI thread, then spawn the terminal once it's running."""
        try:
            res = lifecycle.up(projects.load(slug), on_progress=self._thread_log)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"start failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_up_then_spawn, slug, res, program)

    def _after_up_then_spawn(self, slug: str, res: lifecycle.Result, program: str) -> None:
        self._busy.discard(slug)
        self._log(f"[{'green' if res.ok else 'red'}]{res.detail}[/]")
        self.refresh_projects()
        if res.ok:
            self._spawn_terminal(slug, program)  # container is up now — exec in

    # -- quit / stop-all --------------------------------------------------
    def action_quit(self) -> None:
        """Quit immediately, leaving any running containers up (they persist — a later `up` reattaches).

        Quitting never stops containers or syncs assets: that used to block the close behind a serial,
        unabortable `docker stop` of every container, which made `q` feel like it locked the app. The
        end-of-day "stop + sync everything" is now the separate `S` (stop-all) command."""
        self.exit()

    def _running_slugs(self) -> list[str]:
        """Registry-defined, UP, NOT mid-lifecycle slugs — what stop-all may stop.

        Three filters: ``UP`` (only running containers), ``not in _busy`` (a create/recreate in flight
        must not be stopped out from under its worker — mirrors action_toggle_running's guard), and
        ``projects.exists`` (skip ORPHAN containers — a claude-man-labelled container with no registry
        entry; the registry is the source of truth and every other action refuses orphans, so stop-all
        must not silently stop one either — invariant 4 / TUI-6).

        Reads the CACHED rows from the 2 s poll, not a fresh ``docker ps`` — so ``action_stop_all`` (a
        UI-thread handler) never blocks on a subprocess while deciding what to stop."""
        return [r.slug for r in self._last_rows
                if r.kind == status.UP and r.slug not in self._busy and projects.exists(r.slug)]

    def action_stop_all(self) -> None:
        """End-of-day command: stop + sync-out every running container, then optionally quit. Confirms
        first (it closes detached claude/shell/nvim windows), then runs off-thread behind a progress modal."""
        if self._stopping_all:
            return  # a stop-all pass is already active — ignore a second `S`
        running = self._running_slugs()
        if not running:
            self._log("stop-all: no running containers")
            return
        self.push_screen(StopAllConfirmScreen(running), self._on_stop_all_confirm)

    def _on_stop_all_confirm(self, choice) -> None:
        if not choice:  # cancelled (Escape / Cancel)
            return
        then_exit = choice == "stop_quit"
        # Re-snapshot — the modal sat open, so liveness/busy may have moved.
        slugs = self._running_slugs()
        if not slugs:
            if then_exit:
                self.exit()
            else:
                self._log("stop-all: nothing left running")
            return
        self._stopping_all = True
        # Spinner + live status off-thread (docker stop + a shutil sync-out would freeze the UI thread).
        # The dismiss callback (_on_shutdown_dismissed) clears the guard + ref on EITHER exit path — the
        # worker finishing, OR the operator pressing Esc to hide a stalled modal — so the TUI always
        # returns to a usable state and `S` works again.
        self._shutdown_screen = ShutdownScreen(len(slugs))
        self.push_screen(self._shutdown_screen, self._on_shutdown_dismissed)
        self._stop_all_worker(slugs, then_exit=then_exit)

    def _on_shutdown_dismissed(self, _result) -> None:
        """Fires when the progress modal is dismissed — by the worker (done) or the operator (Esc-hide).
        Either way the stop-all guard is cleared so a wedged background worker can't leave `S` dead."""
        self._shutdown_screen = None
        self._stopping_all = False

    @work(thread=True, group="stop_all")
    def _stop_all_worker(self, slugs: list[str], *, then_exit: bool) -> None:
        total = len(slugs)
        # `finally` so the terminal step (exit, or drop-the-modal + clear `_stopping_all`) always runs
        # even if a mid-loop `call_from_thread` raises — otherwise the stay path could wedge the modal
        # up with `_stopping_all` stuck True, leaving `S` permanently dead.
        try:
            for i, slug in enumerate(slugs, 1):
                self.call_from_thread(self._set_shutdown_status, f"stopping {slug}  ({i}/{total}) …")
                try:
                    res = lifecycle.stop(slug, on_progress=self._thread_log)
                except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
                    res = lifecycle.Result(False, f"stop failed for {slug!r}: {exc!r}")
                self.call_from_thread(self._log, f"[{'green' if res.ok else 'red'}]{res.detail}[/]")
        finally:
            if then_exit:
                self.call_from_thread(self.exit)
            else:
                self.call_from_thread(self._after_stop_all, total)

    def _after_stop_all(self, total: int) -> None:
        """Stop-all worker finished (stay path): drop the progress modal if it's still up, refresh, log.

        The operator may have already Esc-hidden the modal (``_shutdown_screen`` is then None and the
        guard already cleared via ``_on_shutdown_dismissed``) — in that case just refresh + log. When
        the modal IS still up, dismissing it triggers ``_on_shutdown_dismissed`` which clears the guard."""
        if self._shutdown_screen is not None:
            try:
                self._shutdown_screen.dismiss()  # -> _on_shutdown_dismissed clears the guard + ref
            except Exception:  # noqa: BLE001 - the screen may already be gone; clear the guard directly
                self._shutdown_screen = None
                self._stopping_all = False
        else:
            self._stopping_all = False  # modal already hidden by the operator
        self.refresh_projects()
        self._log(f"[green]stop-all: {total} container(s) stopped + assets synced out[/]")

    def _set_shutdown_status(self, text: str) -> None:
        if self._shutdown_screen is not None:
            try:
                self._shutdown_screen.set_status(text)
            except Exception:  # noqa: BLE001 - the screen may already be tearing down
                pass

    def action_toggle_running(self) -> None:
        slug = self._current_slug()
        if not slug:
            return
        row = next((r for r in self._last_rows if r.slug == slug), None)  # cached join — no UI-thread docker ps
        if row is None:
            return
        # Branch on the joined state: UP -> stop; STOPPED/DEFINED -> start. BOTH run in a worker — a
        # `docker stop` waits the SIGTERM grace (the `sleep infinity` PID 1 ignores it) and now also
        # syncs assets out, and a start can build the image; neither must block the UI thread (the old
        # synchronous stop froze the TUI for ~10s). The header spinner shows the in-flight verb.
        if row.kind == status.UP:
            if not self._reserve(slug, "stop"):  # also blocks stopping out from under a create/recreate
                return
            self._log(f"stopping {slug} …")
            self._stop_worker(slug)
        elif projects.exists(slug):
            if not self._reserve(slug, "start"):
                return
            self._log(f"starting {slug} …")
            # Check for a newer claude first (off-thread). If one exists, the modal asks before the
            # rebuild; otherwise it goes straight to the up worker (the slug stays reserved throughout).
            self._update_check_worker(slug)
        else:
            self._log(f"[red]{slug}: orphan container (no registry entry) — not managed[/]")

    @work(thread=True, group="create")
    def _stop_worker(self, slug: str) -> None:
        """Stop (+ asset sync-out) off the UI thread; ``_after_action`` releases the slug + refreshes."""
        try:
            res = lifecycle.stop(slug, on_progress=self._thread_log)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"stop failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_action, slug, res)

    def action_logs(self) -> None:
        slug = self._current_slug()
        if not slug:
            return
        row = next((r for r in self._last_rows if r.slug == slug), None)  # cached join — no UI-thread docker ps
        if row is not None and row.kind == status.DEFINED:
            self._log(f"[yellow]{slug}: no container yet — start it first to see logs[/]")
            return
        self.push_screen(LogsScreen(slug))

    def on_data_table_row_selected(self, event) -> None:
        # Enter on a row opens a shell. The app-level `enter` binding is shadowed by DataTable's
        # own Enter -> RowSelected handling, so we act on the message instead (review TUI-3).
        self.action_open_shell()

    def action_refresh_usage(self) -> None:
        self.refresh_usage()
        self._log("refreshing token usage …")

    def action_settings(self) -> None:
        self.push_screen(SettingsScreen())

    def action_models(self) -> None:
        self.push_screen(ModelsScreen())

    @work(thread=True, group="bootstrap")
    def _bootstrap_env(self) -> None:
        """On startup, load the configured ssh keys into the agent (host-side) so a container with an
        ssh env-mount can authenticate without the operator running ssh-add first. Off the UI thread
        (shells out to ssh-add); only surfaces a line when there's something to report."""
        res = lifecycle.ensure_ssh_keys()
        if "no ssh keys configured" not in res.detail:
            self.call_from_thread(self._log, f"[{'green' if res.ok else 'yellow'}]{res.detail}[/]")

    def action_refresh_gitstate(self) -> None:
        # On-demand: a *fetch-ful* rescan (the 30 s background tick is fetch-less).
        self._dispatch_gitstate(fetch=True)
        self._log("fetching + rescanning repos …")

    # -- submenus ---------------------------------------------------------
    def action_repos_menu(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):  # TUI-6: act on real registry entries only
            self._log("[red]repos: select a defined project (orphan rows aren't managed)[/]")
            return
        self.push_screen(MenuScreen(f"Repos · {slug}", self._REPOS_MENU), self._on_menu_pick)

    def action_project_menu(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):
            self._log("[red]project: select a defined project (orphan rows aren't managed)[/]")
            return
        self.push_screen(MenuScreen(f"Project · {slug}", self._PROJECT_MENU), self._on_menu_pick)

    def action_view_menu(self) -> None:
        self.push_screen(MenuScreen("View", self._VIEW_MENU), self._on_menu_pick)

    def _on_menu_pick(self, token) -> None:
        """Route a submenu's dismissed token to the matching action_* handler (which re-resolves the
        cursor's project itself, so no slug threading is needed)."""
        if not token:
            return  # closed without a choice
        handler = {
            "add_repo": self.action_add_repo,
            "remove_repo": self.action_remove_repo,
            "refresh_git": self.action_refresh_gitstate,
            "pull_all": self.action_pull_all,
            "env_mounts": self.action_env_mounts,
            "ports": self.action_ports,
            "packs": self.action_packs,
            "egress": self.action_egress,
            "overlay": self.action_overlay,
            "model_pin": self.action_model_pin,
            "profile": self.action_profile,
            "recreate": self.action_recreate,
            "delete": self.action_delete_project,
            "refresh_usage": self.action_refresh_usage,
            "logs": self.action_logs,
        }.get(token)
        if handler is not None:
            handler()

    # -- repo pull (ff-only) ----------------------------------------------
    def action_pull_all(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):
            self._log("[red]pull: select a defined project (orphan rows aren't managed)[/]")
            return
        if not gitstate.host_uid_matches_container():
            self._log(f"[red]pull: host uid != container uid {config.CONTAINER_UID}; a host-side pull "
                      f"would trip 'dubious ownership' — run claude-man as uid {config.CONTAINER_UID}[/]")
            return
        if not projects.load(slug).repos:  # no-op fetch + empty modal otherwise (mirror the CLI guard)
            self._log(f"{slug}: no repos to pull")
            return
        # Reserve up-front (not just check _busy): the plan phase runs a multi-second network fetch, so
        # claim the slug now to stop a second g->p stacking another fetch + confirm modal. Released on
        # the cancel / plan-error paths; the apply path's _after_repo_action releases it at the end.
        if not self._reserve(slug, "pull"):
            return
        self._log(f"planning pull for {slug} (fetch + ff-only preview) …")
        self._pull_plan_worker(slug)

    @work(thread=True, exclusive=True, group="pull")
    def _pull_plan_worker(self, slug: str) -> None:
        """Fetch + build the read-only pull plan off the UI thread, then raise the confirm modal."""
        try:
            plan = lifecycle.pull_plan(slug)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            self.call_from_thread(self._pull_plan_failed, slug, exc)
            return
        self.call_from_thread(self._show_pull_confirm, plan)

    def _pull_plan_failed(self, slug: str, exc: Exception) -> None:
        self._busy.discard(slug)  # release the up-front reservation — the pull never reached apply
        self._log(f"[red]pull plan failed for {slug!r}: {exc!r}[/]")

    def _show_pull_confirm(self, plan: lifecycle.PullPlan) -> None:
        self.push_screen(PullConfirmScreen(plan), lambda dirs: self._on_pull_confirm(plan.slug, dirs))

    def _on_pull_confirm(self, slug: str, dirs) -> None:
        if not dirs:
            self._busy.discard(slug)  # cancelled — release the slug reserved in action_pull_all
            self._log(f"{slug}: pull cancelled")
            return
        # slug already reserved up-front in action_pull_all — go straight to apply
        self._log(f"pulling {len(dirs)} repo(s) for {slug} (ff-only) …")
        self._pull_apply_worker(slug, dirs)

    @work(thread=True, group="create")
    def _pull_apply_worker(self, slug: str, dirs: list[str]) -> None:
        """Apply the ff-only pull off the UI thread; _after_repo_action repaints the Repos column."""
        try:
            res = lifecycle.pull_apply(slug, dirs, on_progress=self._thread_log)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"pull failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_repo_action, slug, res)

    def action_new_project(self) -> None:
        self.push_screen(NewProjectScreen(), self._on_new_project)

    def _on_new_project(self, data: NewProject | None) -> None:
        if not data:
            return  # cancelled
        slug, profile, overlay, egress, language, ssh_auto_trust = data
        if not self._reserve(slug, "create"):
            return
        self._log(f"creating {slug} …")
        self._create_project_worker(slug, profile, overlay, egress, language, ssh_auto_trust)

    @work(thread=True, group="create")
    def _create_project_worker(
        self, slug: str, profile: str | None, overlay: str, egress: str, language: str,
        ssh_auto_trust: bool = False,
    ) -> None:
        """Run the blocking create (image build + registry write + seed + `docker create`) off the
        UI thread, streaming build progress to the log.

        A worker exception would otherwise propagate to Textual's default ``exit_on_error`` handler
        and tear the whole TUI down, so every failure is converted to a red ``Result`` instead:
        ValidationError (review SEC-6) for a bad slug, OSError for filesystem faults while seeding
        the config dir (and a missing ``docker`` binary, which ``runner._run`` already maps to a
        non-zero result), RuntimeError for the tomlkit-missing registry-save path, and a last-resort
        ``Exception`` backstop so nothing unexpected can crash the app mid-create.
        """
        try:
            res = lifecycle.create_project(
                slug, profile=profile, overlay=overlay, egress=egress, language=language or None,
                ssh_auto_trust=ssh_auto_trust, on_progress=self._thread_log,
            )
        except schema.ValidationError as exc:
            res = lifecycle.Result(False, f"invalid project {slug!r}: {exc}")
        except (OSError, RuntimeError) as exc:
            res = lifecycle.Result(False, f"create failed for {slug!r}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a background worker must never tear down the app
            res = lifecycle.Result(False, f"create failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_action, slug, res)

    @work(thread=True, group="create")
    def _update_check_worker(self, slug: str, action: str = "start") -> None:
        """Before start OR recreate: check (host-side GET) whether a newer claude than the project's
        image exists. If so, raise the confirm modal; otherwise go straight to the action (``build_to``
        set for a fresh project so its first build tracks the channel). Fails open — any check fault
        just proceeds. The slug stays reserved (claimed in the caller) through to the worker. ``action``
        is ``"start"`` (the ``s`` path) or ``"recreate"`` (the Recreate path) — both offer the update."""
        try:
            chk = lifecycle.check_update(projects.load(slug))
        except Exception as exc:  # noqa: BLE001 - never tear down the app; fail open to the plain action
            self.call_from_thread(self._log, f"[yellow]{slug}: update check failed ({exc!r}); {action}ing[/]")
            self.call_from_thread(self._dispatch_after_update, slug, "", action)
            return
        if chk.prompt:
            self.call_from_thread(self._show_update_confirm, slug, chk, action)
        else:
            if chk.note:
                self.call_from_thread(self._log, f"{slug}: {chk.note}")
            self.call_from_thread(self._dispatch_after_update, slug, chk.build_to, action)

    def _show_update_confirm(self, slug: str, chk: lifecycle.UpdateCheck, action: str) -> None:
        self.push_screen(
            UpdateConfirmScreen(slug, chk.current, chk.target, verb=action),
            lambda decision: self._on_update_confirm(slug, chk, action, decision),
        )

    def _on_update_confirm(self, slug: str, chk: lifecycle.UpdateCheck, action: str, decision) -> None:
        if decision is None:  # Esc / Cancel — abandon the action entirely, release the reservation
            self._busy.discard(slug)
            self._log(f"{slug}: {action} cancelled")
            return
        if decision == "rebuild":
            self._log(f"rebuilding {slug} → claude {chk.target}, then {action}ing …")
            self._dispatch_after_update(slug, chk.target, action)
        else:  # "skip" — proceed on the existing image
            self._log(f"{slug}: skipping update; {action}ing on claude {chk.current}")
            self._dispatch_after_update(slug, "", action)

    def _dispatch_after_update(self, slug: str, rebuild_to: str, action: str) -> None:
        # slug already reserved by the caller — go straight to the matching worker.
        if action == "recreate":
            self._recreate_worker(slug, rebuild_to)
        else:
            self._up_worker(slug, rebuild_to)

    @work(thread=True, group="create")
    def _up_worker(self, slug: str, rebuild_to: str = "") -> None:
        """Start (create-if-needed) off the UI thread; may rebuild the image to a newer claude first,
        then build/create (all streamed)."""
        try:
            res = lifecycle.up(projects.load(slug), on_progress=self._thread_log, rebuild_to=rebuild_to)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"start failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_action, slug, res)

    @work(thread=True, group="create")
    def _recreate_worker(self, slug: str, rebuild_to: str = "") -> None:
        """Recreate off the UI thread; may rebuild the image to a newer claude first (the same update
        offer start makes), and rebuilds a missing image too (all streamed)."""
        try:
            res = lifecycle.recreate(slug, rebuild_to=rebuild_to, on_progress=self._thread_log)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"recreate failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_action, slug, res)

    def _after_action(self, slug: str, res: lifecycle.Result) -> None:
        # Release the slug reservation, then log + refresh. Log detail even on ok=True so advisory
        # notes (no token, clone failures) are visible.
        self._busy.discard(slug)
        self._log(f"[{'green' if res.ok else 'red'}]{res.detail}[/]")
        self.refresh_projects()

    # -- repo add / remove ------------------------------------------------
    def action_add_repo(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):  # TUI-6: act on real registry entries only
            self._log("[red]add-repo: select a defined project (orphan rows aren't managed)[/]")
            return
        existing = {r.resolved_dir() for r in projects.load(slug).repos}
        self.push_screen(AddRepoScreen(slug, existing), lambda data: self._on_add_repo(slug, data))

    def _on_add_repo(self, slug: str, data) -> None:
        if not data:
            return  # cancelled
        if not self._reserve(slug, "add-repo"):
            return
        url, branch, dir_ = data
        self._log(f"adding repo to {slug} …")
        self._add_repo_worker(slug, url, branch, dir_)

    @work(thread=True, group="create")
    def _add_repo_worker(self, slug: str, url: str, branch: str, dir_: str) -> None:
        """Mutate the registry + host-clone off the UI thread via lifecycle.add_repo (which holds the
        per-slug flock). Never reimplements the registry write inline — one mutator contract."""
        try:
            res = lifecycle.add_repo(slug, url, branch=branch, dir=dir_)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"add-repo failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_repo_action, slug, res)

    def action_remove_repo(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):
            self._log("[red]remove-repo: select a defined project (orphan rows aren't managed)[/]")
            return
        dirs = [r.resolved_dir() for r in projects.load(slug).repos]
        if not dirs:
            self._log(f"{slug}: no repos to remove")
            return
        self.push_screen(RemoveRepoScreen(slug, dirs), lambda data: self._on_remove_repo(slug, data))

    def _on_remove_repo(self, slug: str, data) -> None:
        if not data:
            return
        if not self._reserve(slug, "remove-repo"):
            return
        dir_, purge = data
        self._log(f"removing {dir_} from {slug} …")
        self._remove_repo_worker(slug, dir_, purge)

    @work(thread=True, group="create")
    def _remove_repo_worker(self, slug: str, dir_: str, purge: bool) -> None:
        try:
            res = lifecycle.remove_repo(slug, dir_, purge=purge)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"remove-repo failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_repo_action, slug, res)

    def _after_repo_action(self, slug: str, res: lifecycle.Result) -> None:
        self._busy.discard(slug)
        self._log(f"[{'green' if res.ok else 'red'}]{res.detail}[/]")
        self.refresh_projects()             # one docker ps to reflect the new registry count (user action)
        self._dispatch_gitstate(fetch=False)  # rescan to pick up the new/removed repo's state

    # -- env mounts -------------------------------------------------------
    def action_env_mounts(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):  # TUI-6: act on real registry entries only
            self._log("[red]env: select a defined project (orphan rows aren't managed)[/]")
            return
        self._log(f"managing env mounts for {slug} (add/remove need a recreate to apply)")
        self.push_screen(EnvMountsScreen(slug))

    # -- published ports --------------------------------------------------
    def action_ports(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):  # TUI-6: act on real registry entries only
            self._log("[red]ports: select a defined project (orphan rows aren't managed)[/]")
            return
        self._log(f"managing published ports for {slug} (add/remove need a recreate to apply)")
        self.push_screen(PortsScreen(slug))

    # -- curated packs ------------------------------------------------------
    def action_packs(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):  # TUI-6: act on real registry entries only
            self._log("[red]packs: select a defined project (orphan rows aren't managed)[/]")
            return
        self._log(f"managing curated packs for {slug} (toggles apply immediately — no recreate)")
        self.push_screen(PacksScreen(slug))

    # -- egress (strict firewall + allowlist) -----------------------------
    def action_egress(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):  # TUI-6: act on real registry entries only
            self._log("[red]egress: select a defined project (orphan rows aren't managed)[/]")
            return
        # Allowlist add/remove apply inline (fast registry writes); lock/unlock/apply recreate, so the
        # screen dismisses the TARGET mode and the worker below runs the heavy recreate off-thread.
        self._log(f"managing egress for {slug} (allowlist edits inline; lock/unlock recreate to apply)")
        self.push_screen(EgressScreen(slug), lambda mode: self._on_egress(slug, mode))

    def _on_egress(self, slug: str, mode) -> None:
        if mode is None:  # closed after inline allowlist edits only — nothing to recreate
            self.refresh_projects()
            return
        if not self._reserve(slug, "egress"):
            return
        verb = "locking" if mode == "strict" else "unlocking"
        self._log(f"{verb} {slug} (recreating to apply egress) …")
        self._egress_worker(slug, mode)

    @work(thread=True, group="create")
    def _egress_worker(self, slug: str, mode: str) -> None:
        """Apply an egress mode change off the UI thread (set_egress recreates + re-renders squid.conf;
        unlock also tears the sidecar/net down). Streams build progress to the log, like the recreate
        worker."""
        try:
            res = lifecycle.set_egress(slug, mode, on_progress=self._thread_log)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"egress change failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_action, slug, res)

    # -- overlay (image variant) ------------------------------------------
    def action_overlay(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):  # TUI-6: act on real registry entries only
            self._log("[red]overlay: select a defined project (orphan rows aren't managed)[/]")
            return
        current = projects.load(slug).overlay
        self._log(f"changing overlay for {slug} (recreates to apply; builds the image if missing)")
        self.push_screen(OverlaySelectScreen(slug, current), lambda ov: self._on_overlay(slug, ov))

    def _on_overlay(self, slug: str, overlay) -> None:
        if overlay is None:  # cancelled, or picked the current overlay — nothing to recreate
            return
        if not self._reserve(slug, "overlay"):
            return
        self._log(f"switching {slug} to overlay {overlay!r} (recreating to apply) …")
        self._overlay_worker(slug, overlay)

    @work(thread=True, group="create")
    def _overlay_worker(self, slug: str, overlay: str) -> None:
        """Apply an overlay switch off the UI thread (recreate persists the choice + rebuilds the image
        via ensure_chain if it's missing). Streams build progress to the log, like the egress worker."""
        try:
            res = lifecycle.recreate(slug, overlay=overlay, on_progress=self._thread_log)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"overlay change failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_action, slug, res)

    # -- model (local hybrid pin) -----------------------------------------
    def action_model_pin(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):  # TUI-6: act on real registry entries only
            self._log("[red]model: select a defined project (orphan rows aren't managed)[/]")
            return
        current = projects.load(slug).model
        self._log(f"pinning a local model for {slug} (recreates to apply; needs host Ollama)")
        self.push_screen(ModelPinScreen(slug, current), lambda ref: self._on_model_pin(slug, ref))

    def _on_model_pin(self, slug: str, ref) -> None:
        if ref is None:  # cancelled, or picked the current pin — nothing to recreate
            return
        model = "" if ref == ModelPinScreen.CLEAR else ref
        # Locked + hybrid is refused at `up` (ROADMAP 9c). Guard BEFORE persist/recreate: otherwise
        # set_model would persist the pin and the recreate would `docker rm -f` the running container
        # only for up() to refuse the restart — a healthy container destroyed by one keystroke. Unpin
        # (model == "") stays allowed. (registry.set_model enforces the same, this is the clean-message
        # front so the operator never sees a half-applied failure.)
        if model and projects.load(slug).egress == "strict":
            self._log(f"[red]model: {slug} is locked (strict egress) — locked + hybrid isn't supported "
                      f"yet (ROADMAP 9c); unlock it first (Project… → Egress…)[/]")
            return
        if not self._reserve(slug, "model"):
            return
        desc = "subscription-direct" if model == "" else f"local model {model!r}"
        self._log(f"switching {slug} to {desc} (recreating to apply) …")
        self._model_pin_worker(slug, model)

    @work(thread=True, group="create")
    def _model_pin_worker(self, slug: str, model: str) -> None:
        """Apply a local-model pin off the UI thread: persist the registry pin, then recreate (the
        gateway sidecar comes up/down at the container boundary). set_model validates the tag SHAPE
        before writing, so a malformed raw tag surfaces here as a failure and never recreates. Mirrors
        the overlay/egress workers."""
        try:
            projects.set_model(slug, model)
            res = lifecycle.recreate(slug, on_progress=self._thread_log)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"model pin failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_action, slug, res)

    # -- profile (account) ------------------------------------------------
    def action_profile(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):  # TUI-6: act on real registry entries only
            self._log("[red]profile: select a defined project (orphan rows aren't managed)[/]")
            return
        project = projects.load(slug)
        eff = lifecycle.effective_profile(project)  # mark the EFFECTIVE profile (None = inherits default)
        current = eff.name if eff else (project.profile or "")
        self._log(f"changing profile for {slug} (recreates to apply; re-seeds identity)")
        self.push_screen(ProfileSelectScreen(slug, current), lambda name: self._on_profile(slug, name))

    def _on_profile(self, slug: str, name) -> None:
        if name is None:  # cancelled, or picked the current profile — nothing to recreate
            return
        try:
            project = projects.load(slug)
            target = profiles_registry.load(name)
        except FileNotFoundError:
            self._log(f"[red]profile: {name!r} is not a defined profile[/]")
            return
        # A cross-account switch trips recreate's mismatch guard (re-seed → the old account's session
        # history stays). Confirm BEFORE forcing so the operator acknowledges the re-seed instead of
        # hitting an opaque refusal in the log; a same-account switch (or a never-seeded config) needs no
        # force. recreate refuses cleanly before any teardown/persist, so the running container is
        # untouched until the operator confirms.
        conflict = lifecycle.account_mismatch(project, target)
        if conflict:
            self.push_screen(
                ProfileSwitchConfirmScreen(slug, name, conflict, target.account_email),
                lambda res: self._on_profile_confirm(slug, name, res),
            )
            return
        self._start_profile_switch(slug, name, force=False)

    def _on_profile_confirm(self, slug: str, name: str, res) -> None:
        if res == "force":
            self._start_profile_switch(slug, name, force=True)
        else:
            self._log(f"profile switch for {slug} cancelled")

    def _start_profile_switch(self, slug: str, name: str, *, force: bool) -> None:
        if not self._reserve(slug, "profile"):
            return
        self._log(f"switching {slug} to profile {name!r} (recreating to apply) …")
        self._profile_worker(slug, name, force)

    @work(thread=True, group="create")
    def _profile_worker(self, slug: str, name: str, force: bool) -> None:
        """Apply a profile switch off the UI thread: recreate persists the new profile and re-seeds the
        container identity (with ``force`` past a cross-account guard). Mirrors the overlay/model workers."""
        try:
            res = lifecycle.recreate(slug, profile_name=name, force=force, on_progress=self._thread_log)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"profile switch failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_action, slug, res)

    # -- sync-back (review-gated three-way merge into the host ~/.claude) --
    def action_sync_review(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):
            self._log("[red]sync-back: select a defined project (orphan rows aren't managed)[/]")
            return
        # Reserve up-front (like delete): the plan worker scans, then the modal sits open — claim the
        # slug so a second sync-review/recreate can't stack behind it. Released on every exit path
        # (plan error, no-changes, cancel, and _after_action on apply).
        if not self._reserve(slug, "sync-review"):
            return
        self._log(f"scanning {slug} for sync-back changes …")
        self._sync_plan_worker(slug)

    @work(thread=True, group="sync")
    def _sync_plan_worker(self, slug: str) -> None:
        """Compute the plan (detect + per-change masked diffs) off the UI thread, then raise the gate."""
        try:
            plan = lifecycle.sync_plan(slug)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            self.call_from_thread(self._sync_plan_failed, slug, exc)
            return
        self.call_from_thread(self._show_sync_review, plan)

    def _sync_plan_failed(self, slug: str, exc: Exception) -> None:
        self._busy.discard(slug)
        self._log(f"[red]sync-back plan failed for {slug!r}: {exc!r}[/]")

    def _show_sync_review(self, plan: lifecycle.SyncPlan) -> None:
        if not plan.changes:
            self._busy.discard(plan.slug)
            self._log(f"{plan.slug}: no sync-back changes pending")
            return
        self.push_screen(SyncReviewScreen(plan),
                         lambda decisions: self._on_sync_review(plan.slug, decisions))

    def _on_sync_review(self, slug: str, decisions) -> None:
        if decisions is None:  # escape/cancel — nothing written (an all-reject map is still an apply)
            self._busy.discard(slug)
            self._log(f"{slug}: sync-back review cancelled")
            return
        self._log(f"applying sync-back for {slug} …")
        self._sync_apply_worker(slug, decisions)

    @work(thread=True, group="sync")
    def _sync_apply_worker(self, slug: str, decisions: dict) -> None:
        """Apply accepted decisions off the UI thread (global merge lock → host ~/.claude write)."""
        try:
            res = lifecycle.sync_apply(slug, decisions)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"sync-back apply failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_action, slug, res)

    # -- delete (sync-checked teardown) -----------------------------------
    def action_delete_project(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):
            self._log("[red]delete: select a defined project (orphan rows aren't managed)[/]")
            return
        # Reserve up-front (like pull): the plan phase scans every repo, and the confirm modal then
        # sits open — claim the slug now so a second delete/recreate can't stack behind it. Released on
        # the plan-error / cancel paths and at the end of the delete worker (_after_delete).
        if not self._reserve(slug, "delete"):
            return
        self._log(f"assessing {slug} for unsynced work …")
        self._delete_plan_worker(slug)

    # NOT exclusive: the per-slug _busy reservation already blocks a second delete on this slug, and a
    # cross-slug exclusive cancel could kill another slug's awaiting plan worker and leak its
    # reservation. Different slugs may scan + confirm in parallel.
    @work(thread=True, group="delete")
    def _delete_plan_worker(self, slug: str) -> None:
        """Scan each repo's sync risk off the UI thread (fetch-less git status), then raise the modal."""
        try:
            plan = lifecycle.delete_plan(slug)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            self.call_from_thread(self._delete_plan_failed, slug, exc)
            return
        self.call_from_thread(self._show_delete_confirm, plan)

    def _delete_plan_failed(self, slug: str, exc: Exception) -> None:
        self._busy.discard(slug)  # release the up-front reservation — the delete never reached confirm
        self._log(f"[red]delete plan failed for {slug!r}: {exc!r}[/]")

    def _show_delete_confirm(self, plan: lifecycle.DeletePlan) -> None:
        self.push_screen(DeleteProjectScreen(plan), lambda res: self._on_delete_confirm(plan.slug, res))

    def _on_delete_confirm(self, slug: str, res) -> None:
        if res is None:  # cancelled (res is the keep_workspace bool on confirm — False is NOT a cancel)
            self._busy.discard(slug)
            self._log(f"{slug}: delete cancelled")
            return
        self._log(f"deleting {slug}{' (keeping workspace)' if res else ''} …")
        self._delete_worker(slug, res)

    @work(thread=True, group="delete")
    def _delete_worker(self, slug: str, keep_workspace: bool) -> None:
        try:
            res = lifecycle.delete_project(slug, keep_workspace=keep_workspace)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"delete failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_delete, slug, res)

    def _after_delete(self, slug: str, res: lifecycle.Result) -> None:
        self._busy.discard(slug)
        self._log(f"[{'green' if res.ok else 'red'}]{res.detail}[/]")
        self._gitstate.pop(slug, None)  # drop the deleted slug's cached repo state (don't render stale)
        self.refresh_projects()
        self._render_repo_detail()

    def action_recreate(self) -> None:
        slug = self._current_slug()
        if not slug or not projects.exists(slug):
            return
        if not self._reserve(slug, "recreate"):
            return
        self._log(f"recreating {slug} …")
        # Offer the same on-start claude update that `s` does: check the channel first (off-thread); if a
        # newer claude exists the modal asks before rebuilding, otherwise it recreates straight away.
        self._update_check_worker(slug, "recreate")


def run() -> None:
    ClaudeManApp().run()
