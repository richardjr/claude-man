"""Pure view-model for the TUI profile picker (no textual import — dependency-free)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402
from claudeman.registry import profiles  # noqa: E402
from claudeman.registry.schema import Profile  # noqa: E402
from claudeman.tui import profilesview  # noqa: E402


class TokenStatusTest(unittest.TestCase):
    def test_none_is_no_token(self) -> None:
        self.assertEqual(profilesview.token_status(None), "no token")

    def test_fresh_shows_whole_days(self) -> None:
        self.assertEqual(profilesview.token_status(0.4), "0d")
        self.assertEqual(profilesview.token_status(120.9), "120d")

    def test_aging_tag_at_threshold(self) -> None:
        # Just under the ~1-year cliff stays plain; at/over it is tagged so the operator is warned.
        self.assertEqual(profilesview.token_status(329.9), "329d")
        self.assertEqual(profilesview.token_status(330.0), "330d aging")
        self.assertEqual(profilesview.token_status(400.0), "400d aging")


class RowsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.cfg.name
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name
        profiles.save(Profile(name="home", account_email="me@home.example", default=True))
        profiles.save(Profile(name="work", account_email="me@work.example"))
        profiles.save(Profile(name="zeta"))  # no account email
        tok = config.profile_token_path("home")  # only home has a token minted
        tok.parent.mkdir(parents=True, exist_ok=True)
        tok.write_text("sk-ant-oat-xyz\n")

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        self.cfg.cleanup()
        self.state.cleanup()

    def test_all_profiles_sorted_by_name(self) -> None:
        self.assertEqual([r.key for r in profilesview.rows("work")], ["home", "work", "zeta"])

    def test_only_current_is_marked(self) -> None:
        marked = {r.key: r.marked for r in profilesview.rows("work")}
        self.assertEqual(marked, {"home": False, "work": True, "zeta": False})

    def test_no_current_marks_nothing(self) -> None:
        self.assertFalse(any(r.marked for r in profilesview.rows("")))

    def test_default_flag_and_account_fallback(self) -> None:
        by_key = {r.key: r for r in profilesview.rows("work")}
        self.assertTrue(by_key["home"].default)
        self.assertFalse(by_key["work"].default)
        self.assertEqual(by_key["home"].account, "me@home.example")
        self.assertEqual(by_key["zeta"].account, "-")  # unset email → "-"

    def test_token_status_per_profile(self) -> None:
        by_key = {r.key: r for r in profilesview.rows("work")}
        self.assertEqual(by_key["home"].token, "0d")        # just written
        self.assertEqual(by_key["work"].token, "no token")  # never minted
        self.assertEqual(by_key["zeta"].token, "no token")


if __name__ == "__main__":
    unittest.main()
