"""Pure git-identity env rendering (GIT_CONFIG_COUNT/KEY_n/VALUE_n) — no subprocess."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import gitconfig  # noqa: E402


class EnvForTest(unittest.TestCase):
    def test_empty_identity_is_no_env(self) -> None:
        self.assertEqual(gitconfig.env_for("", ""), {})
        self.assertEqual(gitconfig.env_for("  ", "  "), {})  # whitespace is blank

    def test_name_and_email(self) -> None:
        env = gitconfig.env_for("Ada Lovelace", "ada@example.com")
        self.assertEqual(env, {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "Ada Lovelace",
            "GIT_CONFIG_KEY_1": "user.email", "GIT_CONFIG_VALUE_1": "ada@example.com",
        })

    def test_email_only(self) -> None:
        env = gitconfig.env_for("", "ada@example.com")
        self.assertEqual(env, {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "user.email", "GIT_CONFIG_VALUE_0": "ada@example.com",
        })

    def test_name_only(self) -> None:
        env = gitconfig.env_for("Ada", "")
        self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "user.name")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], "Ada")


if __name__ == "__main__":
    unittest.main()
