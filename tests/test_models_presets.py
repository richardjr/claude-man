"""The curated coding-model preset table lints clean (Phase 9 — issue #14), and resolve/default work."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.models import presets  # noqa: E402


class PresetsTest(unittest.TestCase):
    def test_library_lints_clean(self) -> None:
        # Catches a bad edit dependency-free: dup key, malformed tag, no/multiple defaults, missing VRAM,
        # or a preset with no honest tool-use note (the make-or-break).
        self.assertEqual(presets.lint_presets(), [])

    def test_exactly_one_default_and_it_is_qwen3_coder(self) -> None:
        d = presets.default_preset()
        self.assertEqual(d.key, "qwen3-coder")
        self.assertEqual(d.tag, "qwen3-coder:30b")

    def test_resolve_known_and_unknown(self) -> None:
        p = presets.resolve_preset("gpt-oss-20b")
        self.assertIsNotNone(p)
        self.assertEqual(p.tag, "gpt-oss:20b")
        # an unknown key is None so the caller falls back to treating it as a raw ollama tag
        self.assertIsNone(presets.resolve_preset("qwen3-coder:30b"))

    def test_every_preset_has_a_vram_tier(self) -> None:
        for p in presets.PRESETS:
            self.assertGreater(p.vram_gb, 0, p.key)


if __name__ == "__main__":
    unittest.main()
