"""Boot-splash frames — the block-wordmark logo with a reveal + highlight-sweep animation.

PURE (no textual/rich imports): ``frame(t)`` maps an elapsed time to a Rich *markup string*, so
every animation phase is unit-testable dependency-free, exactly like the argv renderers. The
Textual side (``screens/splash.py``) just ticks a timer, feeds ``frame()`` into a ``Static``, and
handles the final scroll-off + skip keys.

Timeline (seconds; any key skips the whole thing):

  0.00 ─ REVEAL_S   logo rows appear top→bottom
  REVEAL_S ─ +SWEEP_S   a bright highlight band sweeps diagonally left→right across the gradient
  … ─ DURATION_S    brief hold; then the SCREEN scrolls the splash up off-view (SCROLL_S, in
                    screens/splash.py — movement is animated there, not re-rendered here)
"""

from __future__ import annotations

from .. import __version__

# fmt: off
LOGO: tuple[str, ...] = (
    r" ██████╗██╗      █████╗ ██╗   ██╗██████╗ ███████╗",
    r"██╔════╝██║     ██╔══██╗██║   ██║██╔══██╗██╔════╝",
    r"██║     ██║     ███████║██║   ██║██║  ██║█████╗  ",
    r"██║     ██║     ██╔══██║██║   ██║██║  ██║██╔══╝  ",
    r"╚██████╗███████╗██║  ██║╚██████╔╝██████╔╝███████╗",
    r" ╚═════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝",
    r"          ███╗   ███╗ █████╗ ███╗   ██╗          ",
    r"          ████╗ ████║██╔══██╗████╗  ██║          ",
    r"          ██╔████╔██║███████║██╔██╗ ██║          ",
    r"          ██║╚██╔╝██║██╔══██║██║╚██╗██║          ",
    r"          ██║ ╚═╝ ██║██║  ██║██║ ╚████║          ",
    r"          ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝          ",
)
# fmt: on
TAGLINE = "hardened containers for claude code"
VERSION_LINE = f"v{__version__}"

# Gradient endpoints (terracotta → dim ember) + the sweep highlight.
_GRAD_TOP = (0xD9, 0x77, 0x57)
_GRAD_BOTTOM = (0x8A, 0x4E, 0x38)
SWEEP_COLOR = "#ffd9a8"
TAGLINE_COLOR = "#9a9a9a"
VERSION_COLOR = "#6a6a6a"

REVEAL_S = 0.20
SWEEP_S = 0.45
HOLD_S = 1.05  # long enough to actually read the tagline/version before the scroll-off
DURATION_S = REVEAL_S + SWEEP_S + HOLD_S   # frames stop here …
SCROLL_S = 0.30                            # … then the screen scrolls the splash off
FPS = 30

_BAND_W = 9        # sweep band width, in columns
_BAND_SLANT = 1.5  # columns the band trails per row (the diagonal)


def _lerp(a: int, b: int, f: float) -> int:
    return round(a + (b - a) * f)


def row_color(row: int, rows: int = len(LOGO)) -> str:
    """The gradient hex for a logo row (terracotta at the top fading to dim ember)."""
    f = row / max(rows - 1, 1)
    r, g, b = (_lerp(a, b, f) for a, b in zip(_GRAD_TOP, _GRAD_BOTTOM))
    return f"#{r:02x}{g:02x}{b:02x}"


def _row_markup(line: str, color: str, band_lo: float, band_hi: float) -> str:
    """One logo row with the ``[band_lo, band_hi)`` column span highlighted (sweep phase)."""
    lo, hi = max(0, round(band_lo)), min(len(line), round(band_hi))
    if hi <= 0 or lo >= len(line) or hi <= lo:
        return f"[{color}]{line}[/]"
    return (f"[{color}]{line[:lo]}[/]"
            f"[bold {SWEEP_COLOR}]{line[lo:hi]}[/]"
            f"[{color}]{line[hi:]}[/]")


def frame(t: float) -> str:
    """The full splash as a Rich markup string for elapsed time ``t`` (clamped to the timeline).

    Rows not yet revealed render as blank lines (the splash never changes height, so the layout
    can't jump). The logo contains no ``[``, so the text needs no markup escaping.
    """
    rows = len(LOGO)
    width = len(LOGO[0])
    if t < REVEAL_S:                      # reveal: rows visible top→bottom
        visible = max(1, round((t / REVEAL_S) * rows))
        band_pos = None
    else:                                 # sweep (then hold, with the band already off-screen)
        visible = rows
        p = min((t - REVEAL_S) / SWEEP_S, 1.0)
        travel = width + _BAND_W + rows * _BAND_SLANT
        band_pos = p * travel - _BAND_W
    out: list[str] = []
    for i, line in enumerate(LOGO):
        if i >= visible:
            out.append("")
        elif band_pos is None:
            out.append(f"[{row_color(i)}]{line}[/]")
        else:
            lo = band_pos - i * _BAND_SLANT
            out.append(_row_markup(line, row_color(i), lo, lo + _BAND_W))
    out.append("")
    done = t >= REVEAL_S
    out.append(f"[{TAGLINE_COLOR}]{TAGLINE.center(width)}[/]" if done else "")
    out.append(f"[{VERSION_COLOR}]{VERSION_LINE.center(width)}[/]" if done else "")
    return "\n".join(out)
