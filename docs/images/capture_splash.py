"""Regenerate the boot-splash doc screenshots (SVG, via Textual's headless renderer).

Run from the repo with the dev env::

    uv run python docs/images/capture_splash.py
    # then, for portable PNGs (GitHub + PyPI render PNG everywhere; SVG not on PyPI):
    rsvg-convert -z 2 -o docs/images/splash-intro.svg.png docs/images/splash-intro.svg

Faithful to ``tui/screens/splash.py``: a ``Static`` fed ``splash.frame(t)``, centered on the
default (textual-dark) theme background — exactly what the operator sees mid-splash. We freeze a
chosen frame instead of animating, so the capture is deterministic. No real terminal needed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from claudeman.tui import splash

OUT = Path(__file__).resolve().parent


class Shot(App):
    TITLE = "claude-man"
    CSS = """
    Screen { align: center middle; }
    #splash-logo { width: auto; height: auto; }
    """

    def __init__(self, t: float) -> None:
        super().__init__()
        self._t = t

    def compose(self) -> ComposeResult:
        yield Static(id="splash-logo")

    def on_mount(self) -> None:
        self.query_one("#splash-logo", Static).update(splash.frame(self._t))


async def shoot(t: float, filename: str, size=(80, 24)) -> None:
    app = Shot(t)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        app.save_screenshot(filename=filename, path=str(OUT))
    print(f"wrote {OUT / filename}  (t={t:.2f})")


async def main() -> None:
    # Settled hold frame: full logo + tagline + version, the sweep band already off-screen.
    await shoot(1.0, "splash-intro.svg")
    # Mid-sweep frame: the bright highlight band crossing the gradient diagonally.
    await shoot(splash.REVEAL_S + splash.SWEEP_S * 0.5, "splash-sweep.svg")


if __name__ == "__main__":
    asyncio.run(main())
