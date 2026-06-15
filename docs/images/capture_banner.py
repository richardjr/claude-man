"""Regenerate the dev-shell banner doc screenshot (SVG via Textual's headless renderer → PNG).

Run from the repo with the dev env::

    uv run python docs/images/capture_banner.py
    # then, for a portable PNG (GitHub + PyPI render PNG everywhere; SVG not on PyPI):
    rsvg-convert -z 2 -o docs/images/shell-banner.png docs/images/shell-banner.svg

Captures the REAL banner (``images/bash/motd`` — the exact script baked into the image) under a pty
so its truecolor ANSI is emitted, renders it through a Textual ``Static`` (``Text.from_ansi``) so the
window chrome + "claude-man" title match the boot-splash screenshots byte-for-byte, and appends a
starship-style prompt line so it reads like a freshly-opened shell. Deterministic — no real terminal.
"""
from __future__ import annotations

import asyncio
import os
import pty
import subprocess
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Static

ROOT = Path(__file__).resolve().parents[2]
MOTD = ROOT / "images" / "bash" / "motd"
OUT = Path(__file__).resolve().parent


def _capture_ansi() -> str:
    """Run the banner under a pty (satisfying its ``-t 1`` colour gate) and capture its ANSI bytes."""
    master, slave = pty.openpty()
    env = {**os.environ, "TERM": "xterm-256color"}
    env.pop("CLAUDEMAN_HISTFILE", None)   # ephemeral history line (the default-shell case)
    env.pop("NO_COLOR", None)
    proc = subprocess.Popen(["bash", str(MOTD)], stdout=slave, stderr=slave, env=env)
    os.close(slave)
    chunks: list[bytes] = []
    while True:
        try:
            data = os.read(master, 65536)
        except OSError:                   # EIO on the master when the child exits — normal EOF
            break
        if not data:
            break
        chunks.append(data)
    proc.wait()
    os.close(master)
    return b"".join(chunks).decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "")


def _banner_text() -> Text:
    text = Text.from_ansi(_capture_ansi().rstrip("\n"))
    text.append("\n\n")                   # a freshly-opened shell prompt, like the operator's config
    text.append("/workspace ", style="cyan")
    text.append("❯ ", style="bold cyan")
    return text


class Shot(App):
    TITLE = "claude-man"
    CSS = "Static { padding: 1 2; }"

    def compose(self) -> ComposeResult:
        yield Static(_banner_text(), id="banner")


async def main() -> None:
    app = Shot()
    async with app.run_test(size=(86, 33)) as pilot:
        await pilot.pause()
        app.save_screenshot(filename="shell-banner.svg", path=str(OUT))
    print(f"wrote {OUT / 'shell-banner.svg'}")


if __name__ == "__main__":
    asyncio.run(main())
