"""Spawn a detached terminal window running a command inside a project's container.

A SEPARATE OS window (not Textual ``suspend()``), launched detached via
``Popen(..., start_new_session=True)`` so it outlives the TUI and never writes into the
TUI's tty. ``ghostty`` is preferred, ``alacritty`` is the fallback. ``--class`` /
``--app-id`` sets the Wayland app_id so a Hyprland ``windowrulev2`` can place these
windows (e.g. ``windowrulev2 = float, class:^(claude-man-.*)$``).

``build_*_argv`` are pure (no process spawn) so they can be unit-tested.
"""

from __future__ import annotations

import shutil
import subprocess

from .. import config

#: app_id / WM class applied to every spawned window for a given project.
def window_class(slug: str) -> str:
    return f"{config.CONTAINER_PREFIX}{slug}"


def _inner_exec(slug: str, program: str, *, keep_open: bool) -> list[str]:
    container = config.container_name(slug)
    base = ["docker", "exec", "-it", container, program]
    if keep_open and program != "bash":
        # keep the window open after `claude` exits by dropping into a shell
        return ["bash", "-lc", f"docker exec -it {container} {program}; exec bash"]
    return base


def build_ghostty_argv(slug: str, program: str, *, keep_open: bool = True) -> list[str]:
    cls = window_class(slug)
    return [
        "ghostty",
        f"--class={cls}",
        f"--title=claude:{slug}",
        "-e", *_inner_exec(slug, program, keep_open=keep_open),
    ]


def build_alacritty_argv(slug: str, program: str, *, keep_open: bool = True) -> list[str]:
    cls = window_class(slug)
    argv = ["alacritty", "--class", f"{cls},Alacritty", "-T", f"claude:{slug}"]
    if keep_open:
        argv.append("--hold")
    argv += ["-e", *_inner_exec(slug, program, keep_open=keep_open)]
    return argv


def _pick_terminal() -> str | None:
    for term in ("ghostty", "alacritty"):
        if shutil.which(term):
            return term
    return None


def build_argv(slug: str, program: str, *, keep_open: bool = True) -> list[str]:
    term = _pick_terminal()
    if term == "ghostty":
        return build_ghostty_argv(slug, program, keep_open=keep_open)
    if term == "alacritty":
        return build_alacritty_argv(slug, program, keep_open=keep_open)
    raise RuntimeError("no supported terminal found (need ghostty or alacritty on PATH)")


def spawn(slug: str, program: str, *, keep_open: bool = True) -> subprocess.Popen:
    """Launch a detached terminal window. ``program`` is typically 'bash' or 'claude'."""
    argv = build_argv(slug, program, keep_open=keep_open)
    return subprocess.Popen(
        argv,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def spawn_shell(slug: str) -> subprocess.Popen:
    return spawn(slug, "bash")


def spawn_claude(slug: str) -> subprocess.Popen:
    return spawn(slug, "claude")
