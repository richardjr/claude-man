"""Row-sweep frame generation (pure — no textual/rich; the app only feeds frame_cells a clock)."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.tui import rowfx  # noqa: E402
from claudeman.tui.splash import LOGO, SWEEP_COLOR, row_color  # noqa: E402

_TAG = re.compile(r"\[[^\]]*\]")

# A realistic projects row (slug, status, profile, egress, repos, version, detail).
CELLS = ["demo", "UP", "work", "open", "2 ok", "2.1.0", "Up 3 minutes"]
TOTAL = sum(len(c) for c in CELLS) + rowfx.GUTTER * (len(CELLS) - 1)


def t_for_hi(hi: float) -> float:
    """The elapsed time at which the band's right edge sits at global column ``hi``."""
    return (hi / (TOTAL + rowfx.BAND_W)) * rowfx.DURATION_S


class BandSpanTest(unittest.TestCase):
    def test_finishes_exactly_at_duration(self) -> None:
        self.assertIsNotNone(rowfx.band_span(rowfx.DURATION_S - 1e-6, TOTAL))
        self.assertIsNone(rowfx.band_span(rowfx.DURATION_S, TOTAL))
        self.assertIsNone(rowfx.band_span(rowfx.DURATION_S * 2, TOTAL))

    def test_travels_off_left_to_off_right(self) -> None:
        lo0, hi0 = rowfx.band_span(0.0, TOTAL)
        self.assertEqual((lo0, hi0), (-rowfx.BAND_W, 0))  # fully off-left: nothing painted yet
        lo1, _ = rowfx.band_span(rowfx.DURATION_S - 1e-9, TOTAL)
        self.assertGreaterEqual(round(lo1), TOTAL - 1)  # all but off-right at the final frame

    def test_position_is_monotonic(self) -> None:
        los = [rowfx.band_span(t_for_hi(h), TOTAL)[0] for h in range(0, TOTAL, 5)]
        self.assertEqual(los, sorted(los))


class PaletteTest(unittest.TestCase):
    def test_band_wears_the_splash_gradient(self) -> None:
        # The sweep matches the intro: glint head on terracotta, tail on dim ember.
        self.assertEqual(rowfx.HEAD_BG, row_color(0))
        self.assertEqual(rowfx.TAIL_BG, row_color(len(LOGO) - 1))

    def test_frame_uses_glint_head_and_tinted_tail(self) -> None:
        # Band right edge well into cell 0: both head and tail spans land on its text.
        frame = rowfx.frame_cells(["a-longer-slug-cell"], t_for_hi(10))
        self.assertIn(f"bold {SWEEP_COLOR} on {rowfx.HEAD_BG}", frame[0])
        self.assertIn(f"on {rowfx.TAIL_BG}", frame[0])


class FrameCellsTest(unittest.TestCase):
    def test_one_markup_per_cell_and_none_when_done(self) -> None:
        frame = rowfx.frame_cells(CELLS, 0.0)
        self.assertEqual(len(frame), len(CELLS))
        self.assertIsNone(rowfx.frame_cells(CELLS, rowfx.DURATION_S))

    def test_t0_renders_all_cells_plain(self) -> None:
        # Band fully off-left: unstyled cells pass through verbatim, styled ones get only the base.
        frame = rowfx.frame_cells(CELLS, 0.0)
        self.assertEqual(frame[0], "demo")
        styled = rowfx.frame_cells(CELLS, 0.0, styles=[None, "green"] + [None] * 5)
        self.assertEqual(styled[1], "[green]UP[/]")

    def test_band_over_first_cell_only(self) -> None:
        frame = rowfx.frame_cells(CELLS, t_for_hi(2))
        self.assertIn(" on ", frame[0])
        self.assertNotIn(" on ", frame[-1])

    def test_band_straddles_a_cell_boundary(self) -> None:
        # Right edge two columns into cell 1 with BAND_W reaching back across the gutter into cell 0.
        hi = len(CELLS[0]) + rowfx.GUTTER + 2
        self.assertGreater(rowfx.BAND_W, rowfx.GUTTER + 2)  # geometry the assertion below relies on
        frame = rowfx.frame_cells(CELLS, t_for_hi(hi))
        self.assertIn(" on ", frame[0])
        self.assertIn(" on ", frame[1])

    def test_visible_text_is_preserved_every_frame(self) -> None:
        # Stripping markup tags must give back the cell text exactly — the sweep restyles, never
        # rewrites. Bracket-free cells, so tag-stripping is unambiguous.
        for hi in range(0, TOTAL + rowfx.BAND_W, 3):
            frame = rowfx.frame_cells(CELLS, t_for_hi(hi))
            if frame is None:
                continue
            for cell, markup in zip(CELLS, frame):
                self.assertEqual(_TAG.sub("", markup), cell)


class RepeatsTest(unittest.TestCase):
    def test_repeats_extend_the_animation(self) -> None:
        t_mid_second_pass = rowfx.DURATION_S * 1.5
        self.assertIsNone(rowfx.frame_cells(CELLS, t_mid_second_pass))  # single pass: done
        self.assertIsNotNone(rowfx.frame_cells(CELLS, t_mid_second_pass, repeats=2))
        self.assertIsNone(rowfx.frame_cells(CELLS, rowfx.DURATION_S * 3, repeats=3))

    def test_each_pass_replays_identically(self) -> None:
        t = t_for_hi(7)
        first = rowfx.frame_cells(CELLS, t, repeats=3)
        third = rowfx.frame_cells(CELLS, t + 2 * rowfx.DURATION_S, repeats=3)
        self.assertEqual(first, third)


class EscapeTest(unittest.TestCase):
    def test_open_bracket_is_escaped(self) -> None:
        frame = rowfx.frame_cells(["fix [WIP] thing"], 0.0)
        self.assertEqual(frame[0], "fix \\[WIP] thing")

    def test_lone_trailing_backslash_gets_a_guard(self) -> None:
        # A segment ending in a single backslash would otherwise eat the next style tag.
        self.assertEqual(rowfx._escape("x\\"), "x\\\\")
        self.assertEqual(rowfx._escape("x\\\\"), "x\\\\")


if __name__ == "__main__":
    unittest.main()
