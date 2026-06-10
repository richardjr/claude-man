"""Boot-splash frame generation (pure — no textual/rich; the screen only feeds frame() a clock)."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import __version__  # noqa: E402
from claudeman.tui import splash  # noqa: E402

_TAG_OPEN = re.compile(r"\[(?!/)[^\]]+\]")
_TAG_CLOSE = re.compile(r"\[/\]")


class LogoShapeTest(unittest.TestCase):
    def test_rows_are_uniform_width(self) -> None:
        widths = {len(line) for line in splash.LOGO}
        self.assertEqual(len(widths), 1, f"ragged logo rows: {widths}")

    def test_logo_contains_no_markup_metachars(self) -> None:
        # frame() embeds rows in markup unescaped — they must never contain '['.
        for line in splash.LOGO:
            self.assertNotIn("[", line)

    def test_gradient_spans_terracotta_to_ember(self) -> None:
        self.assertEqual(splash.row_color(0), "#d97757")
        self.assertEqual(splash.row_color(len(splash.LOGO) - 1), "#8a4e38")


class FrameTimelineTest(unittest.TestCase):
    def test_reveal_starts_with_top_row_only(self) -> None:
        f = splash.frame(0.0)
        self.assertIn(splash.LOGO[0], f)
        self.assertNotIn(splash.LOGO[-1], f)

    def test_reveal_is_monotonic_and_completes(self) -> None:
        seen = 0
        for step in range(0, 11):
            t = splash.REVEAL_S * step / 10
            f = splash.frame(t)
            visible = sum(1 for line in splash.LOGO if line in f)
            self.assertGreaterEqual(visible, seen, f"rows vanished at t={t}")
            seen = visible
        self.assertEqual(seen, len(splash.LOGO))

    def test_frame_height_is_constant(self) -> None:
        heights = {splash.frame(t).count("\n") for t in (0.0, 0.1, 0.3, 0.5, splash.DURATION_S)}
        self.assertEqual(len(heights), 1, f"layout jumps: {heights}")

    def test_sweep_phase_contains_highlight(self) -> None:
        t = splash.REVEAL_S + splash.SWEEP_S / 2
        self.assertIn(splash.SWEEP_COLOR, splash.frame(t))

    def test_hold_frame_has_no_highlight_but_full_logo(self) -> None:
        f = splash.frame(splash.DURATION_S)
        self.assertNotIn(splash.SWEEP_COLOR, f)
        for line in splash.LOGO:
            self.assertIn(line, f)

    def test_tagline_and_version_appear_after_reveal(self) -> None:
        early, late = splash.frame(0.0), splash.frame(splash.DURATION_S)
        self.assertNotIn(splash.TAGLINE, early)
        self.assertIn(splash.TAGLINE, late)
        self.assertIn(f"v{__version__}", late)

    def test_markup_tags_balance_in_every_phase(self) -> None:
        for t in (0.0, 0.1, splash.REVEAL_S, 0.35, 0.5, splash.DURATION_S, 99.0):
            f = splash.frame(t)
            self.assertEqual(len(_TAG_OPEN.findall(f)), len(_TAG_CLOSE.findall(f)),
                             f"unbalanced markup at t={t}")


if __name__ == "__main__":
    unittest.main()
