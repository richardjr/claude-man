"""Boot splash — flashes the logo, runs the sweep, then scrolls up to reveal the UI.

A ModalScreen with a TRANSPARENT screen background and an opaque full-size fill: while the splash
plays it covers the app; the scroll-off animates the fill's offset upward, so the projects table
is literally revealed underneath as it leaves. Any key/click skips instantly. Frame content comes
from the pure ``tui/splash.py`` (unit-tested); this class only owns timing and dismissal — and
every animation step is wrapped so a failure can never block the app from reaching the UI.

Disable with ``[ui] splash = false`` (``claudemanctl config splash off``).
"""

from __future__ import annotations

from time import monotonic

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static

from .. import splash


class SplashScreen(ModalScreen[None]):
    CSS = """
    SplashScreen { background: $background 0%; }
    #splash-fill {
        width: 100%; height: 100%;
        background: $background;
        align: center middle;
    }
    #splash-logo { width: auto; height: auto; }
    """

    def compose(self) -> ComposeResult:
        with Container(id="splash-fill"):
            yield Static(id="splash-logo")

    def on_mount(self) -> None:
        self._t0 = monotonic()
        self._leaving = False
        self._timer = self.set_interval(1 / splash.FPS, self._tick)
        self._tick()

    def _tick(self) -> None:
        t = monotonic() - self._t0
        if t >= splash.DURATION_S:
            self._scroll_off()
            return
        try:
            self.query_one("#splash-logo", Static).update(splash.frame(t))
        except Exception:  # noqa: BLE001 - a bad frame must never wedge startup
            self._finish()

    def _scroll_off(self) -> None:
        if self._leaving:
            return
        self._leaving = True
        self._timer.stop()
        try:
            self.query_one("#splash-fill").styles.animate(
                "offset", value=(0, -self.size.height), duration=splash.SCROLL_S,
                easing="in_cubic", on_complete=self._finish,
            )
        except Exception:  # noqa: BLE001 - if offset animation is unavailable, just reveal the UI
            self._finish()

    def _finish(self) -> None:
        if self.is_attached:
            self.dismiss(None)

    # -- skip: any key or click drops straight into the UI ------------------
    def on_key(self, event) -> None:
        event.stop()
        self._leaving = True
        self._timer.stop()
        self._finish()

    def on_click(self, event) -> None:
        event.stop()
        self._leaving = True
        self._timer.stop()
        self._finish()
