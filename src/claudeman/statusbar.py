"""Per-project status bar — the tmux bar strings drawn on the TOP row of a claude/shell window.

Issue #37: on a decoration-less tiling desktop (Hyprland/Omarchy) the window title and the OSC-11 tint
that name a project's terminal are never visible, so the identity is drawn INSIDE the terminal's cell
grid instead: the launcher (``tui/terminals.py``) runs ``claude``/``bash`` under the baked
``claude-man-bar`` tmux launcher (``images/bash/claude-man-bar`` + ``images/bash/tmux.conf``) with the
three strings rendered here passed as exec-time env (``CLAUDE_MAN_BAR_{STYLE,LEFT,RIGHT}``). PURE —
no registry/docker/textual imports — so the render is unit-tested dependency-free; ``terminals`` does
the registry read and the fail-open.

Colours come from the project's palette bucket (``config.project_name_color`` — the TUI project
column hue — as the bar foreground and the slug chip's background; ``config.project_tint`` — the dark
OSC-11 hue — as the bar background), so a project's bar, TUI row and tinted window always agree.

Every operator-sourced value is ``##``-escaped (``escape``): tmux formats treat ``#`` as the
expansion character, and ``#(cmd)`` runs a SHELL inside the container's tmux server — a registry
string must never be able to smuggle one into the bar. The only ``#(...)`` in the output is the fixed
git-branch probe rendered by this module.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config

STYLE_ENV = "CLAUDE_MAN_BAR_STYLE"
LEFT_ENV = "CLAUDE_MAN_BAR_LEFT"
RIGHT_ENV = "CLAUDE_MAN_BAR_RIGHT"
BAR_ENV_NAMES = (STYLE_ENV, LEFT_ENV, RIGHT_ENV)

#: The baked in-container launcher (images/bash/claude-man-bar) the host execs instead of the bare
#: program when the bar is on. Probed per launch (``terminals.bar_available``) so a stale image without
#: it falls back to the plain launch.
LAUNCHER = "claude-man-bar"

#: tmux session names: "claude" is attach-or-create (a live claude is re-attached, invariant 6); any
#: other name gets a unique per-window session (the launcher appends its pid).
CLAUDE_SESSION = "claude"
SHELL_SESSION = "shell"

_CHIP_FG = "#101010"   # dark text on the bright slug chip
_SEP = "  "


@dataclass(frozen=True)
class BarSpec:
    """The three tmux strings: ``status-style``, ``status-left`` and ``status-right``."""

    style: str
    left: str
    right: str

    def env(self) -> dict[str, str]:
        """As the exec-time env the launcher/conf read (``-e NAME=value`` on ``docker exec``)."""
        return {STYLE_ENV: self.style, LEFT_ENV: self.left, RIGHT_ENV: self.right}


def escape(value: str) -> str:
    """Neutralise tmux format syntax in an operator-sourced value: ``#`` -> ``##`` (so ``#(``, ``#{``
    and ``#[`` are literal) and strip control characters / quotes that would break the conf's
    double-quoted ``set -g`` expansion."""
    out = []
    for ch in str(value):
        if ch == "#":
            out.append("##")
        elif ch in '"\\' or ord(ch) < 0x20 or ch == "\x7f":
            continue
        else:
            out.append(ch)
    return "".join(out)


def _segment(label: str, value: str) -> str:
    return f"#[dim]{escape(label)}#[nodim] #[bold]{escape(value)}#[nobold]"


def render(slug: str, *, profile: str = "", auth: str = "", overlay: str = "", model: str = "",
           egress: str = "", session: str = "") -> BarSpec:
    """Render the bar for ``slug``. Every keyword is optional (``""`` -> the segment is omitted, except
    ``model`` which reads ``default`` so the subscription-direct state is never silent) so a partial
    registry read still yields a usable bar with at least the slug chip.

    ``session`` names the window kind (``claude``/``shell``) shown at the right edge."""
    fg = config.project_name_color(slug)
    bg = config.project_tint(slug)
    chip = f"#[bg={fg},fg={_CHIP_FG},bold] {escape(slug)} #[bg={bg},fg={fg},nobold]"
    segments = []
    if profile:
        segments.append(_segment("profile", profile))
    if auth:
        segments.append(_segment("auth", auth))
    if overlay:
        segments.append(_segment("image", overlay))
    segments.append(_segment("model", model or "default"))
    if egress:
        segments.append(_segment("egress", "locked" if egress == "strict" else egress))
    left = chip + _SEP + _SEP.join(segments) + _SEP
    # Right: the git branch of the pane's cwd (a FIXED probe — the only #() in the bar; refreshed every
    # status-interval), the window kind, and a clock. `#{pane_current_path}` expands before the shell
    # runs; single-quoted so a path with spaces survives.
    branch = ("#(cd '#{pane_current_path}' 2>/dev/null && git branch --show-current 2>/dev/null"
              " | sed 's/^./⎇ &/')")
    kind = f"#[dim]{escape(session)}#[nodim]{_SEP}" if session else ""
    right = f"{branch}{_SEP}{kind}%H:%M "
    return BarSpec(style=f"bg={bg},fg={fg}", left=left, right=right)
