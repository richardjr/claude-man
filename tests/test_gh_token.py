"""The optional GH_TOKEN secret: 0600 state-tier storage, opt-in, never in the secret-free config."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config, gh_token  # noqa: E402


class GhTokenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        self.tmp.cleanup()

    def test_unset_by_default(self) -> None:
        self.assertIsNone(gh_token.load())
        self.assertFalse(gh_token.is_set())

    def test_save_load_roundtrip_stripped(self) -> None:
        gh_token.save("  ghp_abc123  ")
        self.assertEqual(gh_token.load(), "ghp_abc123")  # surrounding whitespace stripped
        self.assertTrue(gh_token.is_set())

    def test_stored_0600(self) -> None:
        gh_token.save("ghp_x")
        mode = stat.S_IMODE(os.stat(config.gh_token_path()).st_mode)
        self.assertEqual(mode, 0o600)

    def test_clear_is_idempotent(self) -> None:
        gh_token.save("ghp_x")
        self.assertTrue(gh_token.clear())   # was present
        self.assertFalse(gh_token.is_set())
        self.assertFalse(gh_token.clear())  # already gone — no error

    def test_empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            gh_token.save("   ")

    def test_lives_in_state_tier_not_config(self) -> None:
        # Load-bearing: the secret must sit under the STATE tier, never the git-versionable config tier.
        p = str(config.gh_token_path().resolve())
        self.assertTrue(p.startswith(str(config.state_home().resolve())))
        self.assertFalse(p.startswith(str(config.config_home().resolve())))


if __name__ == "__main__":
    unittest.main()
