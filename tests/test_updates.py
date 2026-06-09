"""Pure version resolution: parse (reject HTML), compare/order, is_newer (empty-safe). No sockets."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import updates  # noqa: E402


class ParseVersionTest(unittest.TestCase):
    def test_plain_version(self) -> None:
        self.assertEqual(updates.parse_version("2.1.169"), "2.1.169")

    def test_strips_whitespace_and_newline(self) -> None:
        self.assertEqual(updates.parse_version("  2.1.169\n"), "2.1.169")

    def test_prerelease_suffix_ok(self) -> None:
        self.assertEqual(updates.parse_version("2.1.170-rc.1"), "2.1.170-rc.1")

    def test_rejects_html_error_page(self) -> None:
        # The endpoint can serve an HTML body (region block / outage); it must NOT read as a version.
        self.assertIsNone(updates.parse_version("<!DOCTYPE html><html>nope</html>"))

    def test_rejects_empty_and_garbage(self) -> None:
        for bad in ("", "   ", None, "latest", "v2.1.169", "2.1", "2.1.x"):
            self.assertIsNone(updates.parse_version(bad), bad)


class CompareTest(unittest.TestCase):
    def test_equal(self) -> None:
        self.assertEqual(updates.compare("2.1.169", "2.1.169"), 0)

    def test_patch_minor_major_ordering(self) -> None:
        self.assertEqual(updates.compare("2.1.169", "2.1.160"), 1)
        self.assertEqual(updates.compare("2.1.160", "2.1.169"), -1)
        self.assertEqual(updates.compare("2.2.0", "2.1.999"), 1)
        self.assertEqual(updates.compare("3.0.0", "2.9.9"), 1)

    def test_release_outranks_prerelease_of_same_core(self) -> None:
        self.assertEqual(updates.compare("2.1.170", "2.1.170-rc.1"), 1)
        self.assertEqual(updates.compare("2.1.170-rc.1", "2.1.170"), -1)


class IsNewerTest(unittest.TestCase):
    def test_strictly_newer(self) -> None:
        self.assertTrue(updates.is_newer("2.1.169", "2.1.160"))

    def test_same_is_not_newer(self) -> None:
        self.assertFalse(updates.is_newer("2.1.169", "2.1.169"))

    def test_older_is_not_newer(self) -> None:
        self.assertFalse(updates.is_newer("2.1.153", "2.1.160"))

    def test_empty_operands_are_never_newer(self) -> None:
        # The 'no image baked yet' / 'channel unresolved' guard — empty never claims to be newer.
        self.assertFalse(updates.is_newer("", "2.1.160"))
        self.assertFalse(updates.is_newer("2.1.169", ""))
        self.assertFalse(updates.is_newer("", ""))


class ResolveChannelGuardTest(unittest.TestCase):
    def test_unknown_channel_returns_note_without_network(self) -> None:
        # A bad channel must short-circuit to a note (no socket attempt, never raises).
        res = updates.resolve_channel("bogus")
        self.assertIsNone(res.version)
        self.assertIn("bad channel", res.note)


if __name__ == "__main__":
    unittest.main()
