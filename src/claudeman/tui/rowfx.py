"""Row state-change sweep frames — a highlight band swiping across a projects-table row.

PURE (no textual/rich imports): like ``splash.frame``, ``frame_cells(cells, t, kind)`` maps an
elapsed time to Rich *markup strings* (one per table cell), so every frame is unit-testable
dependency-free. The Textual side (``app._tick_rowfx``) just ticks a timer, feeds the markup into
``DataTable.update_cell``, and repaints the row normally once the band has exited.

The band wears the boot splash's palette: a bright glint head (``SWEEP_COLOR``) over the logo
gradient's terracotta-to-ember as background tints. It travels the row's *virtual* width — the
cell texts laid end-to-end with a fixed ``GUTTER`` between them (an approximation of the
DataTable's cell padding, close enough that the swipe reads as continuous) — and can repeat for
``repeats`` consecutive passes (used by the repo-panel git-change sweeps).
"""

from __future__ import annotations

from .splash import LOGO, SWEEP_COLOR, row_color

DURATION_S = 0.85          # one band pass; slow enough to register, brisk enough not to nag
FPS = 30
BAND_W = 12                # total band width (head + tail), in columns
HEAD_W = 4                 # leading bright-glint columns
GUTTER = 2                 # virtual inter-cell gap the band crosses between cells

# Background tints, sampled from the splash logo gradient so the sweep matches the intro:
# terracotta (#d97757) under the glint head, dim ember (#8a4e38) trailing.
HEAD_BG = row_color(0)
TAIL_BG = row_color(len(LOGO) - 1)


def _escape(s: str) -> str:
    """Escape cell text for embedding in markup (cells carry operator data — slugs, branches,
    commit subjects). Mirrors ``rich.markup.escape``: ``[`` -> ``\\[``, plus a guard backslash when
    the segment ends in a lone one (which would otherwise eat the following style tag)."""
    s = s.replace("[", "\\[")
    if s.endswith("\\") and not s.endswith("\\\\"):
        s += "\\"
    return s


def band_span(t: float, total: int) -> tuple[float, float] | None:
    """The ``[lo, hi)`` global-column span of the band at elapsed ``t`` over a ``total``-column
    row, or ``None`` once the sweep has finished (band fully off the right edge)."""
    if t >= DURATION_S:
        return None
    p = max(t, 0.0) / DURATION_S
    pos = p * (total + BAND_W) - BAND_W  # from fully off-left to fully off-right
    return pos, pos + BAND_W


def _cell_markup(text: str, base: str | None, lo: float, head_lo: float, hi: float) -> str:
    """One cell with the band's local ``[lo, hi)`` span overlaid (``[head_lo, hi)`` = glint head).

    Spans are in this cell's own columns (caller subtracts the cell's global offset) and may fall
    entirely outside ``[0, len(text))`` — then the cell renders plain in its base style.
    """
    n = len(text)
    a = min(max(round(lo), 0), n)        # tail start
    b = min(max(round(head_lo), 0), n)   # head start (= tail end)
    c = min(max(round(hi), 0), n)        # head end
    b = max(b, a)
    parts: list[str] = []
    if text[:a]:
        parts.append(f"[{base}]{_escape(text[:a])}[/]" if base else _escape(text[:a]))
    if text[a:b]:
        parts.append(f"[{base or 'default'} on {TAIL_BG}]{_escape(text[a:b])}[/]")
    if text[b:c]:
        parts.append(f"[bold {SWEEP_COLOR} on {HEAD_BG}]{_escape(text[b:c])}[/]")
    if text[c:]:
        parts.append(f"[{base}]{_escape(text[c:])}[/]" if base else _escape(text[c:]))
    return "".join(parts)


def frame_cells(cells: list[str], t: float, styles: list[str | None] | None = None,
                repeats: int = 1) -> list[str] | None:
    """One sweep frame: a markup string per cell for elapsed ``t``, or ``None`` once the band has
    made its final exit (the caller settles the row back to its normal paint).

    ``styles`` carries each cell's base style (e.g. the Status cell's green/red) so the row keeps
    its resting colours outside the band; ``None`` entries render unstyled. ``repeats`` chains
    that many identical passes back-to-back (each ``DURATION_S`` long).
    """
    if t >= DURATION_S * max(repeats, 1):
        return None
    total = sum(len(c) for c in cells) + GUTTER * max(len(cells) - 1, 0)
    span = band_span(t % DURATION_S, total)
    if span is None:  # unreachable for t >= 0 (the modulo keeps t inside one pass) — belt-and-braces
        return None
    lo, hi = span
    out: list[str] = []
    off = 0
    for i, cell in enumerate(cells):
        base = styles[i] if styles else None
        out.append(_cell_markup(cell, base, lo - off, hi - HEAD_W - off, hi - off))
        off += len(cell) + GUTTER
    return out
