"""Spawn detached desktop windows for a project: a terminal running a command inside the container,
or the system file manager on the project's host-side workspace mount.

A SEPARATE OS window (not Textual ``suspend()``), launched detached via
``Popen(..., start_new_session=True)`` so it outlives the TUI and never writes into the TUI's tty.

Which terminal: the operator's ``[terminal]`` preference in ``config.toml`` when set (a built-in
launcher name, or ``program = "custom"`` with an argv template), else auto-detection over the
platform's launcher table below (``ghostty`` then ``alacritty`` first on Linux — the historical
default — with Terminal.app the always-available fallback on macOS and Windows Terminal on WSL2).
On Linux/Wayland the ``--class``/``--app-id`` carries ``window_class(slug)`` so a compositor rule
can place these windows (e.g. Hyprland ``windowrulev2 = float, class:^(claude-man-.*)$``).

Project identity (telling parallel project terminals apart): every window carries ``window_title``,
and the baked shell shows a per-project prompt segment + re-asserts the title (both from the injected
``CLAUDE_MAN_PROJECT`` env). A ``claude``/``nvim`` window's ``docker exec`` never sources that shell,
so its keep-open wrapper stamps the title itself here — and, when ``config terminal-tint on``, a
per-project OSC-11 background tint (``config.project_tint``) that the bashrc applies for shells.

``build_*`` are pure (no process spawn, no filesystem beyond ``shutil.which`` in the pickers) so
they can be unit-tested. ``claude``/shell open in the project's ``launch_workdir`` (``/workspace``
unless overridden) via ``docker exec -w``. ``spawn_path`` opens a HOST directory (the workspace bind
source) in the system file manager (``xdg-open`` / ``open`` / ``wslview`` per platform).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .. import config, hostplatform
from ..registry import projects
from ..registry import settings as settings_registry
from ..registry.schema import ValidationError, validate_slug


#: app_id / WM class applied to every spawned window for a given project.
def window_class(slug: str) -> str:
    return f"{config.CONTAINER_PREFIX}{slug}"


#: Window/tab title applied to every spawned window for a project (and re-asserted in-shell by the
#: baked bashrc). One definition so the launcher `--title` and the OSC title agree.
def window_title(slug: str) -> str:
    return f"claude:{slug}"


def launch_workdir(slug: str) -> str:
    """The container dir to ``docker exec -w`` into for ``slug`` (configured / lone-repo dir / default).

    Read from the registry; falls back to ``/workspace`` for an unknown/malformed project."""
    try:
        return projects.load(slug).launch_workdir
    except (FileNotFoundError, ValidationError, OSError):
        return config.CONTAINER_WORKSPACE


def _window_osc(slug: str, tint_hex: str | None) -> str:
    """A ``printf`` escape prefix (trailing ``'; '``) that stamps the window title — and, when a tint
    hex is given, the OSC-11 background — for a ``claude``/``nvim`` window whose ``docker exec`` never
    sources the container bashrc (so the in-shell identity block can't run for it). Single-quoted so
    bash passes the backslashes to ``printf``; the slug is ``validate_slug``'d and the hex is
    ``#``+hexdigits (``config.project_tint``), so neither can break out of the quotes."""
    seq = f"\\033]0;{window_title(slug)}\\007"
    if tint_hex:
        seq += f"\\033]11;{tint_hex}\\007"
    return f"printf '{seq}'; "


def _inner_exec(slug: str, program: str, *, keep_open: bool, workdir: str,
                tint_hex: str | None = None) -> list[str]:
    # Defence-in-depth for the f-string below (SEC-6's terminal half): the CLI boundary already
    # rejects malformed slugs, but no slug may reach a shell string unvalidated from ANY caller.
    validate_slug(slug)
    container = config.container_name(slug)
    wd = ["-w", workdir] if workdir else []
    if keep_open and program != "bash":
        # keep the window open after `claude` exits by dropping into a shell. Stamp the window title
        # (+ optional tint) FIRST — this exec bypasses the bashrc that names/tints shells, so without
        # this the claude/nvim window would be the one unlabelled window.
        wdq = f"-w {shlex.quote(workdir)} " if workdir else ""
        return ["bash", "-lc",
                f"{_window_osc(slug, tint_hex)}docker exec -it {wdq}{container} {program}; exec bash"]
    return ["docker", "exec", "-it", *wd, container, program]


# ---------------------------------------------------------------------------
# Terminal launcher table — how each supported emulator wraps an inner argv in a new window.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TerminalSpec:
    """One launchable terminal. ``argv`` is a template: ``{class}``/``{title}`` are substituted
    inside elements; the element ``"{argv}"`` splices the inner ``docker exec`` argv. ``kind``
    ``"osascript"`` builds AppleScript instead (the inner argv becomes a quoted shell string).
    ``bundle``: a macOS ``.app`` dir additionally probed for detection (e.g. iTerm)."""

    name: str
    binary: str
    argv: tuple[str, ...] = ()
    kind: str = "argv"          # "argv" | "osascript"
    osa_app: str = ""           # kind="osascript": the scripted application name
    bundle: str = ""

    def available(self) -> bool:
        if not shutil.which(self.binary):
            return False
        return not self.bundle or os.path.isdir(self.bundle)


_LINUX_TERMINALS: tuple[TerminalSpec, ...] = (
    TerminalSpec("ghostty", "ghostty",
                 ("ghostty", "--class={class}", "--title={title}", "-e", "{argv}")),
    TerminalSpec("alacritty", "alacritty",
                 ("alacritty", "--class", "{class},Alacritty", "-T", "{title}", "-e", "{argv}")),
    TerminalSpec("kitty", "kitty",
                 ("kitty", "--class", "{class}", "--title", "{title}", "{argv}")),
    TerminalSpec("wezterm", "wezterm",
                 ("wezterm", "start", "--class", "{class}", "--", "{argv}")),
    TerminalSpec("foot", "foot",
                 ("foot", "--app-id={class}", "--title={title}", "{argv}")),
    TerminalSpec("gnome-terminal", "gnome-terminal",
                 ("gnome-terminal", "--title={title}", "--", "{argv}")),
    TerminalSpec("konsole", "konsole", ("konsole", "-e", "{argv}")),
    TerminalSpec("xterm", "xterm",
                 ("xterm", "-class", "{class}", "-T", "{title}", "-e", "{argv}")),
)

# macOS builds of these emulators reject the Linux-only --class/--app-id (window placement is the
# WM's job there), hence the separate templates. Terminal.app (osascript) is the zero-install
# fallback — always present on macOS — so auto-detection can never fail there.
_DARWIN_TERMINALS: tuple[TerminalSpec, ...] = (
    TerminalSpec("kitty", "kitty", ("kitty", "--title", "{title}", "{argv}")),
    TerminalSpec("alacritty", "alacritty", ("alacritty", "-T", "{title}", "-e", "{argv}")),
    TerminalSpec("wezterm", "wezterm", ("wezterm", "start", "--", "{argv}")),
    TerminalSpec("iterm2", "osascript", kind="osascript", osa_app="iTerm",
                 bundle="/Applications/iTerm.app"),
    TerminalSpec("terminal-app", "osascript", kind="osascript", osa_app="Terminal"),
)

# Windows Terminal, reachable from inside WSL2 via interop; the window runs `wsl.exe -e <argv>` so
# the docker exec happens back inside the distro. Appended after the Linux table (a WSLg-installed
# Linux emulator wins when present; wt.exe is the always-there fallback on modern Windows).
_WSL_EXTRA: tuple[TerminalSpec, ...] = (
    TerminalSpec("wt", "wt.exe",
                 ("wt.exe", "new-tab", "--title", "{title}", "wsl.exe", "-e", "{argv}")),
)

CUSTOM_PROGRAM = "custom"


def platform_terminals(platform: str | None = None, *, wsl: bool | None = None,
                       ) -> tuple[TerminalSpec, ...]:
    """The launcher table for a host platform, in auto-detection preference order."""
    if hostplatform.is_macos(platform):
        return _DARWIN_TERMINALS
    if wsl if wsl is not None else hostplatform.is_wsl(platform):
        return _LINUX_TERMINALS + _WSL_EXTRA
    return _LINUX_TERMINALS


def known_program_names() -> tuple[str, ...]:
    """Every valid ``[terminal] program`` value across all platforms (for set-time validation)."""
    names: list[str] = []
    for spec in (*_LINUX_TERMINALS, *_DARWIN_TERMINALS, *_WSL_EXTRA):
        if spec.name not in names:
            names.append(spec.name)
    return (*names, CUSTOM_PROGRAM)


def _applescript_argv(app: str, inner: list[str]) -> list[str]:
    """osascript argv opening a new ``app`` (Terminal/iTerm) window running ``inner``.

    The inner argv must become a SHELL string inside an AppleScript string — shlex-quote for the
    shell, then escape AppleScript's ``\\`` and ``"`` so no crafted workdir can break out."""
    cmd = shlex.join(inner)
    esc = cmd.replace("\\", "\\\\").replace('"', '\\"')
    if app == "iTerm":
        script = f'tell application "iTerm" to create window with default profile command "{esc}"'
    else:
        script = f'tell application "Terminal" to do script "{esc}"'
    return ["osascript", "-e", script, "-e", f'tell application "{app}" to activate']


def render_spec(spec: TerminalSpec, *, cls: str, title: str, inner: list[str]) -> list[str]:
    """Pure: expand a launcher template around the inner ``docker exec`` argv."""
    if spec.kind == "osascript":
        return _applescript_argv(spec.osa_app, inner)
    out: list[str] = []
    spliced = False
    for el in spec.argv:
        if el == "{argv}":
            out.extend(inner)
            spliced = True
        else:
            out.append(el.replace("{class}", cls).replace("{title}", title))
    if not spliced:
        raise RuntimeError(
            f"terminal template for {spec.name!r} has no '{{argv}}' element — the command to run "
            f"would be dropped; fix the [terminal] command in {config.settings_toml_path()}"
        )
    return out


def _safe_settings():
    try:
        return settings_registry.load()
    except Exception:  # noqa: BLE001 - a bad config must not break spawning a window
        from ..registry.schema import Settings
        return Settings()


def _custom_spec(command: tuple[str, ...]) -> TerminalSpec:
    return TerminalSpec(CUSTOM_PROGRAM, command[0] if command else "", tuple(command))


def resolve_spec(platform: str | None = None) -> TerminalSpec:
    """The launcher to use: the configured ``[terminal] program`` when set, else auto-detection.

    Raises ``RuntimeError`` (caller-surfaced, never a traceback) for an unknown configured name,
    a configured-but-not-installed launcher, or no detectable terminal at all."""
    s = _safe_settings()
    table = platform_terminals(platform)
    if s.terminal_program == CUSTOM_PROGRAM:
        return _custom_spec(s.terminal_command)
    if s.terminal_program:
        for spec in table:
            if spec.name == s.terminal_program:
                if not spec.available():
                    raise RuntimeError(
                        f"configured terminal {spec.name!r} ({spec.binary}) not found on PATH — "
                        f"install it, or `claudemanctl config terminal --auto`"
                    )
                return spec
        raise RuntimeError(
            f"unknown terminal {s.terminal_program!r} for this platform — one of "
            f"{', '.join(sp.name for sp in table)} or 'custom'; "
            f"`claudemanctl config terminal` lists them"
        )
    for spec in table:
        if spec.available():
            return spec
    raise RuntimeError(
        "no supported terminal found (need one of "
        f"{', '.join(sp.name for sp in table)} on PATH) — install one, or set a custom launcher "
        "with `claudemanctl config terminal --custom '…'`"
    )


def build_argv(slug: str, program: str, *, keep_open: bool = True, workdir: str = "",
               tint_hex: str | None = None) -> list[str]:
    spec = resolve_spec()
    inner = _inner_exec(slug, program, keep_open=keep_open, workdir=workdir, tint_hex=tint_hex)
    return render_spec(spec, cls=window_class(slug), title=window_title(slug), inner=inner)


# Named builders kept for direct use/tests; same templates as the table.
def build_ghostty_argv(slug: str, program: str, *, keep_open: bool = True, workdir: str = "",
                       tint_hex: str | None = None) -> list[str]:
    return render_spec(_LINUX_TERMINALS[0], cls=window_class(slug), title=window_title(slug),
                       inner=_inner_exec(slug, program, keep_open=keep_open, workdir=workdir,
                                         tint_hex=tint_hex))


def build_alacritty_argv(slug: str, program: str, *, keep_open: bool = True, workdir: str = "",
                         tint_hex: str | None = None) -> list[str]:
    argv = render_spec(_LINUX_TERMINALS[1], cls=window_class(slug), title=window_title(slug),
                       inner=_inner_exec(slug, program, keep_open=keep_open, workdir=workdir,
                                         tint_hex=tint_hex))
    if keep_open:  # alacritty also holds the window if the inner command somehow fails to exec
        argv.insert(argv.index("-e"), "--hold")
    return argv


def _tint_hex(slug: str) -> str | None:
    """The per-project OSC-11 background hex for ``slug`` when ``config terminal-tint on``, else None.
    Reads the (impure) settings so ``build_argv``/``_inner_exec`` stay pure + unit-testable."""
    return config.project_tint(slug) if _safe_settings().terminal_tint else None


def spawn(slug: str, program: str, *, keep_open: bool = True, workdir: str = "") -> subprocess.Popen:
    """Launch a detached terminal window. ``program`` is typically 'bash' or 'claude'."""
    argv = build_argv(slug, program, keep_open=keep_open, workdir=workdir, tint_hex=_tint_hex(slug))
    return subprocess.Popen(
        argv,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def spawn_shell(slug: str) -> subprocess.Popen:
    return spawn(slug, "bash", workdir=launch_workdir(slug))


def spawn_nvim(slug: str) -> subprocess.Popen:
    """Open neovim (baked read-only into the image — images/nvim) in the project's launch workdir.

    No one-per-container guard: unlike claude (invariant 6), parallel nvim instances share no
    mutable state worth guarding (shada/state live per-container on the .cache tmpfs)."""
    return spawn(slug, "nvim", workdir=launch_workdir(slug))


# One `claude` per container (CLAUDE.md invariant 6 / review SEC-3): a second claude in the same
# container races on `.claude.json`/session writes. The probe walks /proc comm names inside the
# container (no procps dependency); the image's native install runs as a process named `claude`.
_CLAUDE_PROBE_SH = (
    'for c in /proc/[0-9]*/comm; do '
    'read -r n < "$c" 2>/dev/null && [ "$n" = claude ] && exit 0; '
    'done; exit 1'
)


def build_claude_probe_argv(slug: str) -> list[str]:
    """Pure: argv probing for a running ``claude`` process inside the container (rc 0 = running)."""
    validate_slug(slug)
    return ["docker", "exec", config.container_name(slug), "sh", "-c", _CLAUDE_PROBE_SH]


def claude_already_running(slug: str) -> bool:
    """True if a ``claude`` process is already live in the container. Fails OPEN (False) on any
    probe error — a wedged daemon must not lock the operator out of their own project."""
    try:
        cp = subprocess.run(build_claude_probe_argv(slug), capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return cp.returncode == 0


def spawn_claude(slug: str) -> subprocess.Popen:
    if claude_already_running(slug):
        raise RuntimeError(
            f"claude is already running in {slug!r} — one claude per container (a second races "
            f"on .claude.json/session writes). Use the existing window, or open a shell instead."
        )
    return spawn(slug, "claude", workdir=launch_workdir(slug))


# ---------------------------------------------------------------------------
# System file manager — open a HOST directory (the workspace bind source) in the desktop file manager.
# ---------------------------------------------------------------------------
def _pick_opener(platform: str | None = None) -> list[str] | None:
    """The system 'open this path' launcher: the configured ``[opener] command`` when set, else the
    platform default — ``open`` (macOS), ``wslview``/``xdg-open``/``explorer.exe`` (WSL2 — wslview
    from wslu translates the path; explorer.exe is the last-resort interop fallback), or
    ``xdg-open``/``gio open`` (Linux)."""
    s = _safe_settings()
    if s.opener_command:
        return list(s.opener_command)
    if hostplatform.is_macos(platform):
        candidates: tuple[list[str], ...] = (["open"],)
    elif hostplatform.is_wsl(platform):
        candidates = (["wslview"], ["xdg-open"], ["gio", "open"], ["explorer.exe"])
    else:
        candidates = (["xdg-open"], ["gio", "open"])
    for argv in candidates:
        if shutil.which(argv[0]):
            return argv
    return None


def build_open_path_argv(path: str) -> list[str]:
    """Pure: argv to open ``path`` in the system file manager. Raises if no opener is on PATH."""
    opener = _pick_opener()
    if opener is None:
        raise RuntimeError(
            "no file-manager opener found (need xdg-open/gio on Linux, wslview on WSL2) — "
            "or set one with `claudemanctl config opener --command '…'`"
        )
    return [*opener, path]


def spawn_path(path: str) -> subprocess.Popen:
    """Open ``path`` (a host directory) in the system file manager, detached (mirrors ``spawn``)."""
    argv = build_open_path_argv(path)
    if argv[0] == "explorer.exe" and sys.platform == "linux":
        # explorer.exe only understands Windows paths; translate the WSL path via wslpath.
        try:
            win = subprocess.run(["wslpath", "-w", path], capture_output=True, text=True,
                                 check=False, timeout=5).stdout.strip()
            if win:
                argv = [argv[0], win]
        except OSError:
            pass  # fall through with the raw path — explorer opens its default folder
    return subprocess.Popen(
        argv,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
