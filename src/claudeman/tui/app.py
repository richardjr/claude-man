"""The claude-man Textual application.

Phase-1 skeleton: a live projects table (registry JOINed with `docker ps`), bindings to
open a shell / claude in a detached terminal, start/stop, a modal new-project form
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
from textual.widgets import DataTable, Footer, Header, Label, RichLog

from .. import config, lifecycle, usage, usage_api
from ..checkout import gitstate
from ..checkout import repos as repos_mod
from ..docker import status
from ..registry import profiles as profiles_registry
from ..registry import projects
from ..registry import schema
from . import terminals
from .screens.add_repo import AddRepoScreen
from .screens.create import NewProject, NewProjectScreen
from .screens.delete_project import DeleteProjectScreen
from .screens.env_mounts import EnvMountsScreen
from .screens.menu import MenuScreen
from .screens.pull_confirm import PullConfirmScreen
from .screens.quit_confirm import QuitConfirmScreen
from .screens.shutdown import ShutdownScreen
from .screens.remove_repo import RemoveRepoScreen
from .screens.settings import SettingsScreen

_COLUMNS = ("Project", "Status", "Profile", "Egress", "Repos", "Version", "Detail")
_REPO_COLUMNS = ("Dir", "Branch", "State", "↑/↓", "Last commit")
# "5h"/"Week" are ACCOUNT-wide subscription windows from /api/oauth/usage (not container-scoped).
_USAGE_COLUMNS = ("Profile", "Account", "Token", "In", "Out", "Cache", "Total", "5h", "Week")
# Bar colour by utilization band (usage_api.level): green < 70% < yellow < 90% < red.
_USAGE_LEVEL_STYLE = {"ok": "green", "warn": "yellow", "crit": "red", "none": "dim"}
# Braille spinner frames for the header "work in progress" indicator (start/stop/recreate/… take time).
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class ClaudeManApp(App):
    TITLE = "claude-man"
    CSS = """
    #projects { height: 1fr; }
    #repos { height: auto; max-height: 8; border: round $panel; }
    #profiles { height: auto; max-height: 10; border: round $panel; }
    .panel-title { color: $text-muted; padding: 0 1; }
    RichLog { height: 8; border: round $panel; }
    """
    # Footer stays compact: the highest-frequency verbs are top-level single keys; the lower-frequency
    # repo / lifecycle / view verbs live behind the g/p/v submenus (MenuScreen) so the footer doesn't
    # grow a key per action. Refresh-git, Add/Remove-repo, Pull-all -> g; Recreate/Delete -> p;
    # Usage/Logs -> v. The action_* handlers are reused unchanged, dispatched via _on_menu_pick.
    BINDINGS = [
        Binding("n", "new_project", "New"),
        Binding("g", "repos_menu", "Repos…"),
        Binding("e", "env_mounts", "Env…"),
        Binding("p", "project_menu", "Project…"),
        Binding("v", "view_menu", "View…"),
        Binding("enter", "open_shell", "Shell"),
        Binding("c", "open_claude", "Claude"),
        Binding("s", "toggle_running", "Start/Stop"),
        Binding("y", "sync_review", "Sync-back"),
        Binding("comma", "settings", "Settings", key_display=","),
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
        ("r", "Recreate", "recreate"),
        ("d", "Delete", "delete"),
    ]
    _VIEW_MENU = [
        ("u", "Refresh usage", "refresh_usage"),
        ("l", "Focus logs", "focus_logs"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield DataTable(id="projects", cursor_type="row")
            yield Label("Repos · —", id="repos-title", classes="panel-title")
            yield DataTable(id="repos")
            yield Label("Token usage per profile (containers) · 5h/Week = account subscription limits",
                        classes="panel-title")
            yield DataTable(id="profiles")
            yield RichLog(id="log", highlight=True, markup=True)
        yield Footer()

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
        # True once a quit flow is active (confirm modal open, or stop-all-then-exit worker running),
        # so a second `q` doesn't stack another confirm or a second stop-all pass.
        self._quitting = False
        self._shutdown_screen: ShutdownScreen | None = None  # the shutdown progress modal, while quitting
        # slug -> last git-state scan summary (UI-thread only). Host-FS state on a deliberate refresh
        # cadence — distinct from container liveness (never cached); see refresh_gitstate.
        self._gitstate: dict[str, gitstate.ProjectGitSummary] = {}
        self._gitstate_at: float | None = None  # monotonic of the last completed scan, for the panel title
        self._gitstate_seq = 0       # dispatch counter; the latest-dispatched scan wins the cache merge
        self._gitstate_applied = 0   # seq of the last applied batch (drops a slower batch finishing late)
        # profile name -> last subscription-usage result (5h/weekly). Fetched off-thread on a gentle
        # cadence (external endpoint); the usage panel reads this cache for the bar cells.
        self._util: dict[str, usage_api.UsageResult] = {}
        table = self.query_one("#projects", DataTable)
        self._repos_col = table.add_columns(*_COLUMNS)[_COLUMNS.index("Repos")]
        self.query_one("#repos", DataTable).add_columns(*_REPO_COLUMNS)
        self.query_one("#profiles", DataTable).add_columns(*_USAGE_COLUMNS)
        self.refresh_projects()
        self.refresh_usage()
        self.refresh_utilization()
        self._dispatch_gitstate(fetch=False)
        self._render_repo_detail()
        self._bootstrap_env()  # load configured ssh keys into the agent so containers can use them
        # Phase 1: poll. Phase 2 upgrades this to a `docker events` worker.
        self.set_interval(2.0, self.refresh_projects)
        self.set_interval(15.0, self.refresh_usage)                          # usage changes slowly; off thread
        self.set_interval(60.0, self.refresh_utilization)                     # external endpoint — gentle cadence
        self.set_interval(30.0, lambda: self._dispatch_gitstate(fetch=False))  # fetch-less git scan, off thread
        self.set_interval(0.2, self._tick_spinner)  # animate the header spinner while any op is in flight

    # -- data -------------------------------------------------------------
    def _rows(self) -> list[status.Row]:
        defined = [
            (p.slug, p.profile or "(default)", p.egress, len(p.repos))
            for p in projects.list_projects()
        ]
        return status.join(defined, status.query_containers())

    def refresh_projects(self) -> None:
        table = self.query_one("#projects", DataTable)
        # Restore the cursor by SLUG, not integer index — rows are slug-sorted and the set can
        # change between polls, so an index restore could land on the wrong project (review TUI-7).
        prev_slug = self._current_slug()
        table.clear()
        rows = self._rows()
        self._last_rows = rows  # cache for _running_slugs so quit needn't block on a fresh docker ps
        for row in rows:
            table.add_row(
                row.slug, row.kind, row.profile, row.egress,
                self._repos_cell(row), row.version or "-", row.status_text or "-",
                key=row.slug,
            )
        if prev_slug is not None:
            slugs = [r.slug for r in rows]
            if prev_slug in slugs:
                table.move_cursor(row=slugs.index(prev_slug))

    @work(thread=True, exclusive=True, group="usage")
    def refresh_usage(self) -> None:
        """Scan transcripts + aggregate per-profile usage off the UI thread (review TUI-2 pattern).

        The 5h/Week cells come from the ``_util`` cache (populated by ``refresh_utilization`` on a
        slower cadence — the external usage endpoint shouldn't be polled at the 15 s transcript rate)."""
        data = usage.usage_by_profile()
        util = self._util  # snapshot the cache reference (replaced wholesale on the UI thread)
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
            five, week = self._usage_bars(util.get(name))
            rows.append((name, acct, tok, h(u.input), h(u.output),
                         h(u.cache_creation + u.cache_read), h(u.total), five, week))
        self.call_from_thread(self._render_usage, rows)

    @staticmethod
    def _bar(pct: float | None) -> Text:
        return Text(usage_api.render_bar(pct), style=_USAGE_LEVEL_STYLE.get(usage_api.level(pct), "dim"))

    def _usage_bars(self, res) -> tuple[Text, Text]:
        """The (5h, Week) cells for one profile: coloured bars, a dim note (``re-mint``/``offline``),
        or ``…`` before the first utilization fetch has landed."""
        if res is None:
            return Text("…", style="dim"), Text("…", style="dim")
        if res.util is None:
            return Text(res.note or "—", style="dim"), Text("", style="dim")
        return self._bar(res.util.five_hour.pct), self._bar(res.util.seven_day.pct)

    @work(thread=True, exclusive=True, group="util")
    def refresh_utilization(self) -> None:
        """Fetch each profile's 5h/weekly subscription usage off the UI thread (gentle cadence).

        Folds every failure into a note inside ``UsageResult`` (never raises), then repaints the panel.
        A 403 (token minted without the ``user:profile`` scope) shows as ``re-mint``."""
        results = {name: usage_api.fetch_for_profile(name) for name in profiles_registry.list_names()}
        self.call_from_thread(self._apply_util, results)

    def _apply_util(self, results: dict) -> None:
        self._util = results
        self.refresh_usage()  # repaint the panel with the fresh bars

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
        # Merge keeps last-good for a slug whose scan threw this pass; the prune drops slugs gone from
        # the registry so a reused slug never renders the old project's repos (and the cache can't grow).
        self._gitstate = {k: v for k, v in {**self._gitstate, **results}.items() if k in live_set}
        self._gitstate_at = time.monotonic()
        self._paint_repos_column()  # update only the Repos cells from cache — no UI-thread docker ps
        self._render_repo_detail()

    def _paint_repos_column(self) -> None:
        """Repaint the Repos column from the cache without a full ``refresh_projects`` (no docker ps)."""
        table = self.query_one("#projects", DataTable)
        for slug, summary in self._gitstate.items():
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
            panel.add_row(s.dir, gitstate.branch_label(s), gitstate.state_label(s),
                          gitstate.ab_label(s), gitstate.commit_label(s))

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

    # -- actions ----------------------------------------------------------
    def action_open_shell(self) -> None:
        self._open_terminal("bash")

    def action_open_claude(self) -> None:
        self._open_terminal("claude")

    def _open_terminal(self, program: str) -> None:
        """Open a detached terminal running ``program`` (bash/claude) in the cursor's project.

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
            verb = "claude" if program == "claude" else "shell"
            self._log(f"[yellow]{slug}: {verb} skipped — an operation is already running[/]")
            return
        row = next((r for r in self._rows() if r.slug == slug), None)
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
        spawn, verb, past = (
            (terminals.spawn_claude, "claude", "launched") if program == "claude"
            else (terminals.spawn_shell, "shell", "opened")
        )
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

    # -- quit (stop + sync-out all on close) ------------------------------
    def action_quit(self) -> None:
        """Override the default quit: if containers are running, confirm before stopping them all.

        Stopping each container runs its asset sync-out (the per-project asset-sync model), so a clean
        close persists CLAUDE.md + skills/agents back to the synced config tier. With nothing running,
        quit immediately."""
        if self._quitting:
            return  # a quit flow is already active — ignore a second `q`
        running = self._running_slugs()
        if not running:
            self.exit()
            return
        self._quitting = True
        # Feedback in the log too (in case the modal is missed): the confirm is keyboard-first.
        self._log(f"quit: {len(running)} container(s) running — "
                  f"[b]Enter[/]/[b]s[/] stop+sync · [b]l[/] leave running · [b]esc[/] cancel")
        self.push_screen(QuitConfirmScreen(running), self._on_quit_confirm)

    def _running_slugs(self) -> list[str]:
        """UP rows NOT mid-lifecycle — a `_busy` slug (create/recreate in flight) must not be stopped
        out from under its worker (mirrors action_toggle_running's busy guard).

        Reads the CACHED rows from the 2 s poll, not a fresh ``docker ps`` — so ``action_quit`` (a UI-
        thread handler) never blocks on a subprocess while deciding whether to confirm."""
        return [r.slug for r in self._last_rows if r.kind == status.UP and r.slug not in self._busy]

    def _on_quit_confirm(self, choice) -> None:
        if not choice:                       # cancelled (Escape / Cancel)
            self._quitting = False           # allow quitting again later
            return
        if choice == "leave":
            self._quitting = False           # defensive: reset before exit in case exit is deferred
            self.exit()                      # leave containers running — no stop, no sync-out
            return
        # "stop_all": stop each off-thread (docker stop + a shutil sync-out would freeze the UI
        # thread), then exit. Re-snapshot — the modal sat open, so liveness/busy may have moved.
        slugs = self._running_slugs()
        if not slugs:
            self._quitting = False
            self.exit()
            return
        # Show a spinner + live status (the old behaviour dropped to a frozen-looking main screen).
        self._shutdown_screen = ShutdownScreen(len(slugs))
        self.push_screen(self._shutdown_screen)
        self._stop_all_then_exit_worker(slugs)

    @work(thread=True, group="quit")
    def _stop_all_then_exit_worker(self, slugs: list[str]) -> None:
        total = len(slugs)
        for i, slug in enumerate(slugs, 1):
            self.call_from_thread(self._set_shutdown_status, f"stopping {slug}  ({i}/{total}) …")
            try:
                res = lifecycle.stop(slug, on_progress=self._thread_log)
            except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
                res = lifecycle.Result(False, f"stop failed for {slug!r}: {exc!r}")
            self.call_from_thread(self._log, f"[{'green' if res.ok else 'red'}]{res.detail}[/]")
        self.call_from_thread(self.exit)

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
        row = next((r for r in self._rows() if r.slug == slug), None)
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
            self._up_worker(slug)
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

    def action_focus_logs(self) -> None:
        slug = self._current_slug()
        if slug:
            self.query_one("#log", RichLog).focus()
            self._log(f"(phase 1) live log streaming for {slug} — see screens/logs.py")

    def on_data_table_row_selected(self, event) -> None:
        # Enter on a row opens a shell. The app-level `enter` binding is shadowed by DataTable's
        # own Enter -> RowSelected handling, so we act on the message instead (review TUI-3).
        self.action_open_shell()

    def action_refresh_usage(self) -> None:
        self.refresh_usage()
        self.refresh_utilization()
        self._log("refreshing token usage + subscription limits …")

    def action_settings(self) -> None:
        self.push_screen(SettingsScreen())

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
            "recreate": self.action_recreate,
            "delete": self.action_delete_project,
            "refresh_usage": self.action_refresh_usage,
            "focus_logs": self.action_focus_logs,
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
        slug, profile, overlay, egress = data
        if not self._reserve(slug, "create"):
            return
        self._log(f"creating {slug} …")
        self._create_project_worker(slug, profile, overlay, egress)

    @work(thread=True, group="create")
    def _create_project_worker(
        self, slug: str, profile: str | None, overlay: str, egress: str
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
                slug, profile=profile, overlay=overlay, egress=egress,
                on_progress=self._thread_log,
            )
        except schema.ValidationError as exc:
            res = lifecycle.Result(False, f"invalid project {slug!r}: {exc}")
        except (OSError, RuntimeError) as exc:
            res = lifecycle.Result(False, f"create failed for {slug!r}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a background worker must never tear down the app
            res = lifecycle.Result(False, f"create failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_action, slug, res)

    @work(thread=True, group="create")
    def _up_worker(self, slug: str) -> None:
        """Start (create-if-needed) off the UI thread; may build the image first (streamed)."""
        try:
            res = lifecycle.up(projects.load(slug), on_progress=self._thread_log)
        except Exception as exc:  # noqa: BLE001 - never tear down the app from a worker
            res = lifecycle.Result(False, f"start failed for {slug!r}: {exc!r}")
        self.call_from_thread(self._after_action, slug, res)

    @work(thread=True, group="create")
    def _recreate_worker(self, slug: str) -> None:
        """Recreate off the UI thread; rebuilds the image if it's gone missing (streamed)."""
        try:
            res = lifecycle.recreate(slug, on_progress=self._thread_log)
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

    def action_sync_review(self) -> None:
        self._log("(phase 5) sync-back review gate — see screens/sync_review.py")

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
        self._recreate_worker(slug)


def run() -> None:
    ClaudeManApp().run()
