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
unless overridden) via ``docker exec -w``; a ``claude`` window additionally carries the project's
claude-model pin as ``--model <ref>`` (registry ``claude_model`` via ``claude_model_args`` —
launch-time only, so the pin needs no recreate). ``spawn_path`` opens a HOST directory (the
workspace bind source) in the system file manager (``xdg-open`` / ``open`` / ``wslview`` per
platform).

``spawn`` returns a ``SpawnHandle`` (Popen + a stderr capture) that every caller hands to
``watch_spawn``: a short post-spawn wait classifies what the launcher did (still running /
exited 0 / failed with a stderr tail), so a launcher that starts and then fails is surfaced
instead of logged as success (issue #31).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from typing import IO, NamedTuple

from .. import agents, config, hostplatform, statusbar
from ..agents import AgentProvider
from ..registry import profiles, projects
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

    Read from the registry; falls back to ``/workspace`` for an unknown/malformed project (a
    syntactically corrupt TOML raises ``TOMLDecodeError``, not ``ValidationError`` — both count)."""
    try:
        return projects.load(slug).launch_workdir
    except (FileNotFoundError, ValidationError, tomllib.TOMLDecodeError, OSError):
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
                tint_hex: str | None = None, args: tuple[str, ...] = (),
                bar: statusbar.BarSpec | None = None) -> list[str]:
    # Defence-in-depth for the f-string below (SEC-6's terminal half): the CLI boundary already
    # rejects malformed slugs, but no slug may reach a shell string unvalidated from ANY caller.
    validate_slug(slug)
    container = config.container_name(slug)
    wd = ["-w", workdir] if workdir else []
    if bar is not None and (_is_agent(program) or program == "bash"):
        # Status bar (issue #37): exec the baked tmux launcher instead of the bare program, with the
        # rendered bar strings as EXEC-time env (never argv to tmux — the conf/launcher read them from
        # the environment). Both programs take the keep-open wrapper shape so the printf stamps the
        # OUTER window's title/tint — tmux swallows the in-pane bashrc's OSC. Only claude keeps the
        # window open after exit (a shell exiting closes it, as before). tmux's socket is the only
        # write, on the /tmp tmpfs — no runner change, floor byte-identical (invariant 2).
        session = statusbar.CLAUDE_SESSION if _is_agent(program) else statusbar.SHELL_SESSION
        env = " ".join(f"-e {shlex.quote(f'{k}={v}')}" for k, v in bar.env().items())
        wdq = f"-w {shlex.quote(workdir)} " if workdir else ""
        prog = shlex.join([statusbar.LAUNCHER, session, program, *args])
        tail = "; exec bash" if (keep_open and _is_agent(program)) else ""
        return ["bash", "-lc",
                f"{_window_osc(slug, tint_hex)}docker exec -it {env} {wdq}{container} {prog}{tail}"]
    if keep_open and program != "bash":
        # keep the window open after `claude` exits by dropping into a shell. Stamp the window title
        # (+ optional tint) FIRST — this exec bypasses the bashrc that names/tints shells, so without
        # this the claude/nvim window would be the one unlabelled window.
        wdq = f"-w {shlex.quote(workdir)} " if workdir else ""
        # shlex.join so every extra arg is shell-safe inside the -lc string (a `--model` ref like
        # `claude-sonnet-5[1m]` contains glob characters that must not hit pathname expansion).
        prog = shlex.join([program, *args])
        return ["bash", "-lc",
                f"{_window_osc(slug, tint_hex)}docker exec -it {wdq}{container} {prog}; exec bash"]
    return ["docker", "exec", "-it", *wd, container, program, *args]


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
    # Ptyxis: GNOME's default terminal since Ubuntu 25.10 / Fedora 41 (issue #31). No
    # --class/--app-id flag exists (every window is app-id org.gnome.Ptyxis), so per-project
    # identity rides the title only — same shape as gnome-terminal/konsole below. Placed before
    # gnome-terminal so a stock GNOME box auto-detects its actual desktop default.
    TerminalSpec("ptyxis", "ptyxis",
                 ("ptyxis", "--new-window", "-T", "{title}", "--", "{argv}")),
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
        spec = _custom_spec(s.terminal_command)
        # Probe the custom launcher like any named one (issue #31: a stale custom template whose
        # binary is gone otherwise fails silently at Popen, after a green "opened" log line).
        if not spec.available():
            raise RuntimeError(
                f"configured custom terminal {spec.binary or '(empty command)'!r} not found on "
                f"PATH — install it, fix the [terminal] command in {config.settings_toml_path()}, "
                f"or `claudemanctl config terminal --auto`"
            )
        return spec
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
               tint_hex: str | None = None, args: tuple[str, ...] = (),
               bar: statusbar.BarSpec | None = None) -> list[str]:
    spec = resolve_spec()
    inner = _inner_exec(slug, program, keep_open=keep_open, workdir=workdir, tint_hex=tint_hex,
                        args=args, bar=bar)
    return render_spec(spec, cls=window_class(slug), title=window_title(slug), inner=inner)


# Named builders kept for direct use/tests; same templates as the table.
def build_ghostty_argv(slug: str, program: str, *, keep_open: bool = True, workdir: str = "",
                       tint_hex: str | None = None, args: tuple[str, ...] = ()) -> list[str]:
    return render_spec(_LINUX_TERMINALS[0], cls=window_class(slug), title=window_title(slug),
                       inner=_inner_exec(slug, program, keep_open=keep_open, workdir=workdir,
                                         tint_hex=tint_hex, args=args))


def build_alacritty_argv(slug: str, program: str, *, keep_open: bool = True, workdir: str = "",
                         tint_hex: str | None = None, args: tuple[str, ...] = ()) -> list[str]:
    argv = render_spec(_LINUX_TERMINALS[1], cls=window_class(slug), title=window_title(slug),
                       inner=_inner_exec(slug, program, keep_open=keep_open, workdir=workdir,
                                         tint_hex=tint_hex, args=args))
    if keep_open:  # alacritty also holds the window if the inner command somehow fails to exec
        argv.insert(argv.index("-e"), "--hold")
    return argv


def _tint_hex(slug: str) -> str | None:
    """The per-project OSC-11 background hex for ``slug`` when ``config terminal-tint on``, else None.
    Reads the (impure) settings so ``build_argv``/``_inner_exec`` stay pure + unit-testable."""
    return config.project_tint(slug) if _safe_settings().terminal_tint else None


# ---------------------------------------------------------------------------
# Spawn outcome — a launcher that starts and then fails must not look like success (issue #31:
# stdout/stderr were DEVNULL'd and nothing checked the exit status, so a broken launcher produced
# a green "opened" log line and no window). Client-server terminals (gnome-terminal, ptyxis,
# konsole, wt) exit almost immediately — 0 on success, non-zero on failure — so a short post-spawn
# wait catches them; window-lifetime terminals (ghostty, alacritty, …) are still running at the
# probe and count as success. Fails OPEN: a launcher failing after the probe window stays silent
# (same as before), and the watcher itself never raises.
# ---------------------------------------------------------------------------
SPAWN_PROBE_S = 1.5   # post-spawn grace: client-server launchers exit well within this


@dataclass(frozen=True)
class SpawnOutcome:
    ok: bool
    state: str              # "running" | "exited" | "failed"
    returncode: int | None  # None = still running at probe time
    stderr_tail: str = ""   # last lines of the launcher's stderr (failed only)


def classify_spawn(returncode: int | None, stderr_tail: str = "") -> SpawnOutcome:
    """Pure: map a launcher's exit status at probe time to an outcome."""
    if returncode is None:
        return SpawnOutcome(True, "running", None)
    if returncode == 0:
        return SpawnOutcome(True, "exited", 0)
    return SpawnOutcome(False, "failed", returncode, stderr_tail)


class SpawnHandle(NamedTuple):
    """A spawned launcher plus its stderr capture; hand it to ``watch_spawn`` (which closes it).
    ``note`` is a non-fatal operator notice about HOW the window was launched (e.g. the status bar
    fell back to a plain launch because the image lacks the launcher) — callers surface it so a
    degraded launch is never silent."""

    proc: subprocess.Popen
    stderr_file: IO[bytes]
    note: str = ""


def _stderr_tail(f: IO[bytes], *, max_bytes: int = 2048, max_lines: int = 6) -> str:
    """The last non-blank lines of a captured-stderr file. Never raises."""
    try:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - max_bytes))
        lines = [ln for ln in f.read(max_bytes).decode("utf-8", "replace").splitlines()
                 if ln.strip()]
        return "\n".join(lines[-max_lines:])
    except (OSError, ValueError):
        return ""


def watch_spawn(handle: SpawnHandle, *, timeout: float = SPAWN_PROBE_S) -> SpawnOutcome:
    """Wait briefly for a spawned launcher and classify what happened. Never raises; always
    closes the handle's stderr capture. Blocks up to ``timeout`` — call off the UI thread."""
    rc: int | None
    try:
        rc = handle.proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        rc = None
    except OSError:
        rc = handle.proc.poll()
    tail = _stderr_tail(handle.stderr_file)
    try:
        handle.stderr_file.close()
    except OSError:
        pass
    return classify_spawn(rc, tail)


# ---------------------------------------------------------------------------
# Status bar (issue #37) — the per-project tmux bar on the top row of claude/shell windows.
# ---------------------------------------------------------------------------
def _bar_enabled() -> bool:
    return _safe_settings().terminal_status_bar


def build_bar_probe_argv(slug: str) -> list[str]:
    """Pure: argv probing the container for the baked ``claude-man-bar`` launcher (rc 0 = present)."""
    validate_slug(slug)
    return ["docker", "exec", config.container_name(slug), "sh", "-c",
            f"command -v {statusbar.LAUNCHER}"]


def bar_available(slug: str) -> bool:
    """True if the container's image bakes the bar launcher. Fails CLOSED (False -> the plain launch,
    which always works) — an image built before issue #37 must not break opening a window."""
    try:
        cp = subprocess.run(build_bar_probe_argv(slug), capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return cp.returncode == 0


def build_claude_session_probe_argv(slug: str) -> list[str]:
    """Pure: argv asking the container's tmux whether the ``claude`` bar session exists (rc 0 = yes)."""
    validate_slug(slug)
    return ["docker", "exec", config.container_name(slug), "tmux", "has-session", "-t",
            f"={statusbar.CLAUDE_SESSION}"]


def claude_session_exists(slug: str) -> bool:
    """True if a ``claude`` tmux bar session is live in the container (so a new claude window can
    RE-ATTACH to it — the invariant-6 amendment). Fails CLOSED (False -> the guard refuses, the
    pre-#37 behaviour)."""
    try:
        cp = subprocess.run(build_claude_session_probe_argv(slug), capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return cp.returncode == 0


def bar_spec(slug: str, program: str) -> statusbar.BarSpec:
    """The rendered bar for ``slug``'s ``program`` window, read fresh from the registry at LAUNCH (so
    a model/profile/auth change shows on the next window — no recreate). Fails OPEN to a slug-only
    bar for an unknown/malformed project (corrupt TOML raises ``TOMLDecodeError``, not
    ``ValidationError`` — both count) — a registry hiccup must not block opening a window."""
    session = statusbar.CLAUDE_SESSION if _is_agent(program) else statusbar.SHELL_SESSION
    try:
        project = projects.load(slug)
    except (FileNotFoundError, ValidationError, tomllib.TOMLDecodeError, OSError):
        return statusbar.render(slug, session=session)
    profile = project.profile or ""
    if not profile:
        try:
            default = profiles.default_profile()
        except (ValidationError, tomllib.TOMLDecodeError, OSError):
            default = None
        profile = default.name if default is not None else ""
    return statusbar.render(slug, profile=profile, auth=project.auth, overlay=project.overlay,
                            model=project.claude_model or project.model, egress=project.egress,
                            session=session)


BAR_UNAVAILABLE_NOTE = (
    f"status bar off for this window: the container's image lacks `{statusbar.LAUNCHER}` — rebuild "
    "it (`claudemanctl image build --project <slug>`, or `image build base` then any up/recreate, "
    "which rebuilds stale overlays) and recreate the project"
)


def _bar_for(slug: str, program: str) -> tuple[statusbar.BarSpec | None, str]:
    """The bar to launch ``program`` under (None = the plain launch) plus an operator note.

    Plain silently when the setting is off or the program has its own status line (nvim); plain
    WITH ``BAR_UNAVAILABLE_NOTE`` when the bar was wanted but the image lacks the launcher (probed —
    fail-open to the launch that always works, but never silently: the operator would otherwise
    see "no difference" and not know why)."""
    if not (_is_agent(program) or program == "bash") or not _bar_enabled():
        return None, ""
    if not bar_available(slug):
        return None, BAR_UNAVAILABLE_NOTE
    return bar_spec(slug, program), ""


def spawn(slug: str, program: str, *, keep_open: bool = True, workdir: str = "",
          args: tuple[str, ...] = ()) -> SpawnHandle:
    """Launch a detached terminal window. ``program`` is typically 'bash' or 'claude'.

    Returns a ``SpawnHandle``; every caller must pass it to ``watch_spawn`` so a launcher that
    starts and then fails is surfaced (and the stderr capture is closed)."""
    bar, note = _bar_for(slug, program)
    argv = build_argv(slug, program, keep_open=keep_open, workdir=workdir, tint_hex=_tint_hex(slug),
                      args=args, bar=bar)
    stderr_file = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(
            argv,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
        )
    except BaseException:
        stderr_file.close()
        raise
    return SpawnHandle(proc, stderr_file, note)


def spawn_shell(slug: str) -> SpawnHandle:
    return spawn(slug, "bash", workdir=launch_workdir(slug))


def spawn_nvim(slug: str) -> SpawnHandle:
    """Open neovim (baked read-only into the image — images/nvim) in the project's launch workdir.

    No one-per-container guard: unlike claude (invariant 6), parallel nvim instances share no
    mutable state worth guarding (shada/state live per-container on the .cache tmpfs)."""
    return spawn(slug, "nvim", workdir=launch_workdir(slug))


# One agent per container (CLAUDE.md invariant 6 / review SEC-3): a second claude in the same
# container races on `.claude.json`/session writes. The probe walks /proc comm names inside the
# container (no procps dependency) looking for the provider's ``proc_comm`` (the image's native
# claude install runs as a process named `claude`). The comm is validated by ``AgentProvider`` to a
# plain token, so it is safe to interpolate into the `sh -c` string.
def _probe_sh(comm: str) -> str:
    return (
        'for c in /proc/[0-9]*/comm; do '
        f'read -r n < "$c" 2>/dev/null && [ "$n" = {comm} ] && exit 0; '
        'done; exit 1'
    )


def _is_agent(program: str) -> bool:
    """True if ``program`` is an agent binary (vs a shell / nvim) — the windows that take the
    keep-open wrapper + the `claude` tmux session."""
    return program in agents.binaries()


def provider_for(slug: str) -> AgentProvider:
    """The project's agent provider, read fresh from the registry at LAUNCH (like ``launch_workdir``).
    Fails OPEN to the default (claude) provider for an unknown/malformed project — a registry hiccup
    must not block opening a window (corrupt TOML raises ``TOMLDecodeError``, not ``ValidationError``
    — both count)."""
    try:
        return projects.load(slug).provider
    except (FileNotFoundError, ValidationError, tomllib.TOMLDecodeError, OSError):
        return agents.DEFAULT


def build_claude_probe_argv(slug: str, *, provider: AgentProvider = agents.DEFAULT) -> list[str]:
    """Pure: argv probing for a running agent process (``provider.proc_comm``) inside the container
    (rc 0 = running)."""
    validate_slug(slug)
    return ["docker", "exec", config.container_name(slug), "sh", "-c", _probe_sh(provider.proc_comm)]


def claude_already_running(slug: str, *, provider: AgentProvider | None = None) -> bool:
    """True if the project's agent process (``provider.proc_comm``; the registry's provider when
    ``provider`` is None) is already live in the container. Fails OPEN (False) on any probe error —
    a wedged daemon must not lock the operator out of their own project."""
    try:
        cp = subprocess.run(build_claude_probe_argv(slug, provider=provider or provider_for(slug)),
                            capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return cp.returncode == 0


def claude_model_args(slug: str) -> tuple[str, ...]:
    """The ``--model`` argv for the project's claude-model pin (registry ``claude_model``), or ``()``.

    Read fresh from the registry like ``launch_workdir``; fails OPEN (no flag — claude's own
    default) for an unknown/malformed project so a registry hiccup can't block opening claude
    (corrupt TOML raises ``TOMLDecodeError``, not ``ValidationError`` — both count)."""
    try:
        model = projects.load(slug).claude_model
    except (FileNotFoundError, ValidationError, tomllib.TOMLDecodeError, OSError):
        return ()
    return ("--model", model) if model else ()


def spawn_claude(slug: str, *, provider: AgentProvider | None = None) -> SpawnHandle:
    # Invariant-6 amendment (issue #37): a claude running INSIDE the bar's tmux session is re-attached
    # (the launcher's `new-session -A`) rather than refused — closing the window never killed it. A
    # claude running anywhere else is still a second-claude race, and is refused as before.
    provider = provider or provider_for(slug)   # the project's agent (registry; fail-open claude)
    if (claude_already_running(slug, provider=provider)
            and not (_bar_enabled() and claude_session_exists(slug))):
        raise RuntimeError(
            f"{provider.binary} is already running in {slug!r} — one agent per container (a second "
            f"races on its config/session writes). Use the existing window, or open a shell instead."
        )
    args = claude_model_args(slug) if provider is agents.CLAUDE else ()   # `--model` is claude's flag shape
    return spawn(slug, provider.binary, workdir=launch_workdir(slug), args=args)


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
        # Probe like the terminal path: a configured opener whose binary is gone must surface as
        # an error (via build_open_path_argv), not fail silently at Popen.
        return list(s.opener_command) if shutil.which(s.opener_command[0]) else None
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
        cfg = _safe_settings().opener_command
        if cfg:
            raise RuntimeError(
                f"configured opener {cfg[0]!r} not found on PATH — install it, or "
                f"`claudemanctl config opener --auto`"
            )
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
