"""Live container-log viewer (Phase 1 / TUI-5).

A near-full-screen ``RichLog`` fed by an ``@work(thread=True, exclusive, group='logs')`` worker
running ``runner.build_logs_argv`` (``docker logs --tail 200 --timestamps -f <container>``) and
pushing each line back to the UI thread via ``call_from_thread``. The follower subprocess is reaped
(``proc.terminate()`` → ``kill()``) in ``on_unmount`` so dismissing the screen — or shutting the app
down — never leaks a ``docker logs -f``. Read-only: it streams output, it never writes to the
container (so no invariant-2/6 surface).
"""

from __future__ import annotations

import subprocess

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, RichLog
from textual.worker import get_current_worker

from ...docker import runner

# Bound the initial backfill: enough to see what just happened without flooding the pane on a
# long-lived container; new lines stream in live after it.
_TAIL = 200


class LogsScreen(ModalScreen[None]):
    """Stream ``slug``'s container logs until dismissed (escape/q). Dismisses ``None``."""

    BINDINGS = [
        ("escape", "close", "Close"),
        ("q", "close", "Close"),
    ]
    CSS = """
    LogsScreen { align: center middle; }
    #dialog {
        width: 90%; height: 90%;
        padding: 1 2;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #logview { height: 1fr; border: round $panel; }
    #logs-hint { color: $text-muted; padding-top: 1; }
    """

    def __init__(self, slug: str) -> None:
        super().__init__()
        self._slug = slug
        self._proc: subprocess.Popen | None = None
        self._view: RichLog | None = None  # captured on the UI thread in on_mount; written via call_from_thread

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Logs · {self._slug}  (docker logs -f)", classes="title")
            yield RichLog(id="logview", highlight=True, markup=False, wrap=True, max_lines=5000)
            yield Label("escape / q to close", id="logs-hint")

    def on_mount(self) -> None:
        self._view = self.query_one("#logview", RichLog)  # resolve on the UI thread, not in the worker
        self._stream_logs()

    @work(thread=True, exclusive=True, group="logs")
    def _stream_logs(self) -> None:
        """Spawn ``docker logs -f`` and pump its lines into the viewer off the UI thread."""
        view = self._view
        assert view is not None  # set in on_mount before this worker is dispatched
        argv = runner.build_logs_argv(self._slug, tail=_TAIL)
        try:
            # stderr→stdout so a "No such container" / permission error lands in the pane in order;
            # line-buffered text so each line surfaces as the container emits it.
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            # Publish the handle the instant the child exists — before any other statement — so
            # on_unmount can always reap it even if the screen is dismissed in this same tick.
            self._proc = proc
        except FileNotFoundError:
            self.app.call_from_thread(view.write, "[docker not found on PATH]")
            return
        except OSError as exc:  # spawn failure must not tear the app down
            self.app.call_from_thread(view.write, f"[could not start docker logs: {exc!r}]")
            return

        worker = get_current_worker()
        try:
            # Iterating the pipe blocks on each readline; on_unmount's terminate() closes it, ending
            # the loop. is_cancelled is a belt-and-braces check for the worker being cancelled directly.
            for line in proc.stdout:  # type: ignore[union-attr]
                if worker.is_cancelled:
                    break
                self.app.call_from_thread(view.write, line.rstrip("\n"))
        except (OSError, ValueError):
            pass  # pipe closed under us (terminate during read) — expected on dismiss
        finally:
            rc = proc.poll()
            if not worker.is_cancelled:
                note = "— stream ended —" if rc in (0, None) else f"— docker logs exited {rc} —"
                self.app.call_from_thread(view.write, note)

    def on_unmount(self) -> None:
        """Reap the follower so no ``docker logs -f`` outlives the screen."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

    def action_close(self) -> None:
        self.dismiss(None)
