"""Per-project identity colour generation (pure stdlib — the TUI project-name tint).

``config.project_name_color`` must be a STABLE, process-independent pick from the curated palette:
the same slug always maps to the same colour so a project reads as one colour everywhere (the TUI
list + the spawned-terminal identity). Keyed on SHA-256, deliberately NOT the salted builtin hash().
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402

_HEX = re.compile(r"^#[0-9a-f]{6}$")


class ProjectNameColorTest(unittest.TestCase):
    def test_all_palette_entries_are_hex(self) -> None:
        for c in config._PROJECT_NAME_COLORS:
            self.assertRegex(c, _HEX, f"non-hex palette entry: {c!r}")

    def test_palette_hue_order_matches_the_terminal_tint_palette(self) -> None:
        # The foreground palette is aligned index-for-index with the 8-slot dark tint palette so a
        # project's TUI colour and its terminal-window tint share a bucket. Guard the length.
        self.assertEqual(len(config._PROJECT_NAME_COLORS), 8)

    def test_colour_is_from_the_palette(self) -> None:
        for slug in ("alpha", "landarna-client", "infra", "x", "a-very-long-slug-name-123"):
            self.assertIn(config.project_name_color(slug), config._PROJECT_NAME_COLORS)

    def test_stable_for_the_same_slug(self) -> None:
        # Deterministic within a run (and across runs — SHA-256, not the PYTHONHASHSEED-salted hash();
        # a cross-process check lives in the manual smoke, not importable here).
        self.assertEqual(config.project_name_color("alpha"), config.project_name_color("alpha"))

    def test_known_slugs_pin_to_expected_buckets(self) -> None:
        # Pins the SHA-256 bucketing so a future refactor that changes the hash/derivation is caught
        # (the exact values also document what stability means: these never move).
        self.assertEqual(config.project_name_color("alpha"), "#c2b44a")
        self.assertEqual(config.project_name_color("alpha2"), "#5fa8e0")
        self.assertEqual(config.project_name_color("bravo"), "#3fb8ad")

    def test_distinguishes_similar_slugs(self) -> None:
        # A trailing char flips the bucket — parallel projects like "alpha"/"alpha2" don't collide.
        self.assertNotEqual(config.project_name_color("alpha"), config.project_name_color("alpha2"))


if __name__ == "__main__":
    unittest.main()
