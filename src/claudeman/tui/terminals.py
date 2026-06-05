"""Spawn a detached terminal window running a command inside a project's container.

A SEPARATE OS window (not Textual ``suspend()``), launched detached via
``Popen(..., start_new_session=True)`` so it outlives the TUI and never writes into the
TUI's tty. ``ghostty`` is preferred, ``alacritty`` is the fallback. ``--class`` /
``--app-id`` sets the Wayland app_id so a Hyprland ``windowrulev2`` can place these
windows (e.g. ``windowrulev2 = float, class:^(claude-man-.*)$``).

``build_*_argv`` are pure (no process spawn) so they can be unit-tested. ``claude``/shell open in the
project's ``launch_workdir`` (a lone repo's dir by default) via ``docker exec -w``.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess

from .. import config
from ..registry import projects
from ..registry.schema import ValidationError


#: app_id / WM class applied to every spawned window for a given project.
def window_class(slug: str) -> str:
    return f"{config.CONTAINER_PREFIX}{slug}"


def launch_workdir(slug: str) -> str:
    """The container dir to ``docker exec -w`` into for ``slug`` (configured / lone-repo dir / default).

    Read from the registry; falls back to ``/workspace`` for an unknown/malformed project."""
    try:
        return projects.load(slug).launch_workdir
    except (FileNotFoundError, ValidationError, OSError):
        return config.CONTAINER_WORKSPACE


def _inner_exec(slug: str, program: str, *, keep_open: bool, workdir: str) -> list[str]:
    container = config.container_name(slug)
    wd = ["-w", workdir] if workdir else []
    if keep_open and program != "bash":
        # keep the window open after `claude` exits by dropping into a shell
        wdq = f"-w {shlex.quote(workdir)} " if workdir else ""
        return ["bash", "-lc", f"docker exec -it {wdq}{container} {program}; exec bash"]
    return ["docker", "exec", "-it", *wd, container, program]


def build_ghostty_argv(slug: str, program: str, *, keep_open: bool = True, workdir: str = "") -> list[str]:
    cls = window_class(slug)
    return [
        "ghostty",
        f"--class={cls}",
        f"--title=claude:{slug}",
        "-e", *_inner_exec(slug, program, keep_open=keep_open, workdir=workdir),
    ]


def build_alacritty_argv(slug: str, program: str, *, keep_open: bool = True, workdir: str = "") -> list[str]:
    cls = window_class(slug)
    argv = ["alacritty", "--class", f"{cls},Alacritty", "-T", f"claude:{slug}"]
    if keep_open:
        argv.append("--hold")
    argv += ["-e", *_inner_exec(slug, program, keep_open=keep_open, workdir=workdir)]
    return argv


def _pick_terminal() -> str | None:
    for term in ("ghostty", "alacritty"):
        if shutil.which(term):
            return term
    return None


def build_argv(slug: str, program: str, *, keep_open: bool = True, workdir: str = "") -> list[str]:
    term = _pick_terminal()
    if term == "ghostty":
        return build_ghostty_argv(slug, program, keep_open=keep_open, workdir=workdir)
    if term == "alacritty":
        return build_alacritty_argv(slug, program, keep_open=keep_open, workdir=workdir)
    raise RuntimeError("no supported terminal found (need ghostty or alacritty on PATH)")


def spawn(slug: str, program: str, *, keep_open: bool = True, workdir: str = "") -> subprocess.Popen:
    """Launch a detached terminal window. ``program`` is typically 'bash' or 'claude'."""
    argv = build_argv(slug, program, keep_open=keep_open, workdir=workdir)
    return subprocess.Popen(
        argv,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def spawn_shell(slug: str) -> subprocess.Popen:
    return spawn(slug, "bash", workdir=launch_workdir(slug))


def spawn_claude(slug: str) -> subprocess.Popen:
    return spawn(slug, "claude", workdir=launch_workdir(slug))
