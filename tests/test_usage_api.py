"""Subscription-usage parsing + bar rendering (pure; no sockets)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import usage_api  # noqa: E402

SAMPLE = {
    "five_hour": {"utilization": 32.5, "resets_at": "2026-06-05T18:00:00Z"},
    "seven_day": {"utilization": 71, "resets_at": "2026-06-09T00:00:00Z"},
    "seven_day_opus": {"utilization": None, "resets_at": None},
    "seven_day_sonnet": {"utilization": 12, "resets_at": "2026-06-09T00:00:00Z"},
    "extra_usage": {"is_enabled": False},
}


class ParseTest(unittest.TestCase):
    def test_parses_windows(self) -> None:
        u = usage_api.parse_utilization(SAMPLE)
        self.assertEqual(u.five_hour.pct, 32.5)
        self.assertEqual(u.five_hour.resets_at, "2026-06-05T18:00:00Z")
        self.assertEqual(u.seven_day.pct, 71.0)
        self.assertEqual(u.seven_day_sonnet.pct, 12.0)

    def test_null_window_is_none(self) -> None:
        u = usage_api.parse_utilization(SAMPLE)
        self.assertIsNone(u.seven_day_opus.pct)        # utilization: null
        self.assertEqual(u.seven_day_opus.resets_at, "")

    def test_missing_keys_are_none(self) -> None:
        u = usage_api.parse_utilization({})
        self.assertIsNone(u.five_hour.pct)
        self.assertIsNone(u.seven_day.pct)

    def test_bool_is_not_a_percent(self) -> None:
        # JSON true/false must not be coerced to 1.0/0.0 (bool is an int subclass in Python).
        u = usage_api.parse_utilization({"five_hour": {"utilization": True}})
        self.assertIsNone(u.five_hour.pct)

    def test_non_finite_is_none(self) -> None:
        # NaN/inf must become None — else level() would read NaN as the 'ok' band.
        u = usage_api.parse_utilization({"five_hour": {"utilization": float("nan")},
                                         "seven_day": {"utilization": float("inf")}})
        self.assertIsNone(u.five_hour.pct)
        self.assertIsNone(u.seven_day.pct)


class RenderBarTest(unittest.TestCase):
    def test_none_is_dash(self) -> None:
        self.assertEqual(usage_api.render_bar(None), "—")

    def test_empty_and_full(self) -> None:
        self.assertEqual(usage_api.render_bar(0, width=8), "░░░░░░░░ 0%")
        self.assertEqual(usage_api.render_bar(100, width=8), "████████ 100%")

    def test_half(self) -> None:
        bar = usage_api.render_bar(50, width=8)
        self.assertEqual(bar.count("█"), 4)
        self.assertEqual(bar.count("░"), 4)
        self.assertTrue(bar.endswith(" 50%"))

    def test_clamps_out_of_range(self) -> None:
        self.assertTrue(usage_api.render_bar(150, width=8).startswith("█" * 8))
        self.assertEqual(usage_api.render_bar(-5, width=8).count("█"), 0)


class LevelTest(unittest.TestCase):
    def test_bands(self) -> None:
        self.assertEqual(usage_api.level(None), "none")
        self.assertEqual(usage_api.level(0), "ok")
        self.assertEqual(usage_api.level(69.9), "ok")
        self.assertEqual(usage_api.level(70), "warn")
        self.assertEqual(usage_api.level(89), "warn")
        self.assertEqual(usage_api.level(90), "crit")
        self.assertEqual(usage_api.level(100), "crit")


class FetchGuardTest(unittest.TestCase):
    def test_no_token_is_noted_not_fetched(self) -> None:
        res = usage_api.fetch_utilization("")  # must not touch the network
        self.assertIsNone(res.util)
        self.assertEqual(res.note, "no token")


if __name__ == "__main__":
    unittest.main()
