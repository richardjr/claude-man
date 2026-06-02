"""The sync-back denylist is a security boundary — pin its decisions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.syncback import denylist  # noqa: E402


class DeniedPathTest(unittest.TestCase):
    def test_secrets_and_state_denied(self) -> None:
        for p in (
            ".credentials.json",
            ".claude.json",
            "history.jsonl",
            "sessions/abc.jsonl",
            "projects/foo/transcript.jsonl",
            "shell-snapshots/snap.sh",
            "statsig/evlist",
            "cache/anything",
            "file-history/x",
            "backups/2026/x",
            "some-cache.json",
            "settings.local.json",
            "todos/abc.json",
            "debug/latest",
        ):
            self.assertTrue(denylist.is_denied_path(p), f"{p} should be denied")

    def test_synced_artifacts_allowed(self) -> None:
        for p in (
            "agents/code-reviewer.md",
            "skills/omarchy",
            "commands/deploy.md",
            "settings.json",
        ):
            self.assertFalse(denylist.is_denied_path(p), f"{p} should be allowed")


class JsonKeyTest(unittest.TestCase):
    def test_identity_and_machine_keys_denied(self) -> None:
        for k in ("oauthAccount", "userID", "accountUuid", "lastFoo", "cachedBar"):
            self.assertTrue(denylist.is_denied_json_key(k), f"{k} should be denied")

    def test_normal_keys_allowed(self) -> None:
        for k in ("hooks", "statusLine", "permissions", "effortLevel"):
            self.assertFalse(denylist.is_denied_json_key(k))


class SecretMaskTest(unittest.TestCase):
    def test_bearer_redacted(self) -> None:
        out = denylist.mask_line("+   Authorization: Bearer sk-abc123DEF456")
        self.assertNotIn("sk-abc123DEF456", out)
        self.assertIn("redacted", out)

    def test_secret_keyvalue_redacted(self) -> None:
        out = denylist.mask_line('+   "apiKey": "super-secret-value",')
        self.assertNotIn("super-secret-value", out)
        self.assertIn("redacted", out)

    def test_plain_line_untouched(self) -> None:
        line = '+   "model": "claude-opus-4-8",'
        self.assertEqual(denylist.mask_line(line), line)

    def test_is_secret_key(self) -> None:
        self.assertTrue(denylist.is_secret_key("authToken"))
        self.assertTrue(denylist.is_secret_key("PASSWORD"))
        self.assertFalse(denylist.is_secret_key("displayName"))


if __name__ == "__main__":
    unittest.main()
