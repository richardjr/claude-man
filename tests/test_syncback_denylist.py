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

    def test_cache_json_basename_anchored(self) -> None:
        """BUG-3: a ``*-cache.json`` is denied at the top level AND nested at any depth (the
        ``copy_dir_filtered`` walk tests each basename, so a cache file smuggled inside an allowed
        tree must still match)."""
        for p in (
            "some-cache.json",
            "mcp-needs-auth-cache.json",
            "skills/my-cache.json",
            "projects/foo/state-cache.json",
        ):
            self.assertTrue(denylist.is_denied_path(p), f"{p} should be denied")
        # a normal json under an allowed tree is NOT swept up by the cache glob
        self.assertFalse(denylist.is_denied_path("agents/notes.json"))


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

    def test_value_shape_redacted_under_benign_key(self) -> None:
        """BUG-4: a token-shaped value is redacted even when the KEY looks innocuous."""
        cases = {
            '+   "note": "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF1234ZZZZ",': "sk-ant-api03",
            '+   "id": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",': "ghp_ABCDEFGH",
            '+   "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payloadpart.sigpart",': "eyJhbGci",
        }
        for line, leaked in cases.items():
            out = denylist.mask_line(line)
            self.assertNotIn(leaked, out, f"{leaked!r} should be redacted in {out!r}")
            self.assertIn("redacted", out)

    def test_value_shape_leaves_normal_values(self) -> None:
        for line in (
            '+   "model": "claude-opus-4-8",',
            '+   "dir": "/home/agent/.claude/skills",',
            '+   "count": 42,',
        ):
            self.assertEqual(denylist.mask_line(line), line)

    def test_broadened_secret_shapes_and_keys(self) -> None:
        # provider-shaped values redact regardless of key (adversarial-review Finding 1)
        for leaked, line in (
            ("sk_live_51aBcDeFgHiJkLmNoPqRsT", '+   "STRIPE_SK": "sk_live_51aBcDeFgHiJkLmNoPqRsT",'),
            ("AKIAIOSFODNN7EXAMPLE", '+   "aws": "AKIAIOSFODNN7EXAMPLE",'),
            ("a1b2c3d4e5f60718293a4b5c6d7e8f90", '+   "twilio": "a1b2c3d4e5f60718293a4b5c6d7e8f90",'),
        ):
            out = denylist.mask_line(line)
            self.assertNotIn(leaked, out, f"{leaked!r} leaked in {out!r}")
        # secret-named key (pwd/cred) redacts its value even when short/odd-shaped
        out = denylist.mask_line('+   "REDIS_PWD": "p@ss",')
        self.assertNotIn("p@ss", out)

    def test_case_insensitive_denied_json_keys(self) -> None:
        for k in ("OauthAccount", "userId", "AccountUuid", "OrganizationUUID"):
            self.assertTrue(denylist.is_denied_json_key(k), f"{k} should be denied (case-insensitive)")


class JsonKeyDiffTest(unittest.TestCase):
    def test_denied_keys_never_appear(self) -> None:
        from claudeman.syncback import diff

        before = {"model": "a", "userID": "u-1", "lastSeen": "x"}
        after = {"model": "b", "userID": "u-2", "lastSeen": "y", "oauthAccount": {"e": "z"}}
        out = "\n".join(diff.json_key_diff(before, after))
        for denied in ("userID", "u-1", "u-2", "lastSeen", "oauthAccount"):
            self.assertNotIn(denied, out)
        self.assertIn("model", out)  # the allowed change is shown

    def test_secret_value_masked_in_json_diff(self) -> None:
        from claudeman.syncback import diff

        out = "\n".join(diff.json_key_diff({}, {"webhook": "sk-ant-SECRETSECRETSECRETSECRET1234"}))
        self.assertNotIn("sk-ant-SECRETSECRETSECRETSECRET1234", out)


if __name__ == "__main__":
    unittest.main()
