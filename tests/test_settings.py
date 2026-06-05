"""Global settings store: default-when-absent, add/remove ssh key, round-trip, validation."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.registry import settings as settings_registry  # noqa: E402
from claudeman.registry.schema import Settings, ValidationError  # noqa: E402


class SettingsStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        self.tmp.cleanup()

    def test_load_default_when_absent(self) -> None:
        s = settings_registry.load()
        self.assertEqual(s.ssh_keys, ())
        self.assertTrue(s.ssh_auto_load)

    def test_add_then_load_roundtrip(self) -> None:
        updated, added = settings_registry.add_ssh_key("~/.ssh/id_ed25519")
        self.assertTrue(added)
        self.assertEqual(updated.ssh_keys, ("~/.ssh/id_ed25519",))
        self.assertEqual(settings_registry.load().ssh_keys, ("~/.ssh/id_ed25519",))  # persisted

    def test_add_is_dedup_and_order_preserving(self) -> None:
        settings_registry.add_ssh_key("~/.ssh/a")
        settings_registry.add_ssh_key("~/.ssh/b")
        _, added = settings_registry.add_ssh_key("~/.ssh/a")
        self.assertFalse(added)
        self.assertEqual(settings_registry.load().ssh_keys, ("~/.ssh/a", "~/.ssh/b"))

    def test_remove(self) -> None:
        settings_registry.add_ssh_key("~/.ssh/a")
        _, removed = settings_registry.remove_ssh_key("~/.ssh/a")
        self.assertTrue(removed)
        self.assertEqual(settings_registry.load().ssh_keys, ())
        _, removed_again = settings_registry.remove_ssh_key("~/.ssh/a")
        self.assertFalse(removed_again)

    def test_parse_rejects_non_list_keys(self) -> None:
        with self.assertRaises(ValidationError):
            settings_registry._parse({"ssh": {"keys": "not-a-list"}})

    def test_empty_key_rejected_by_schema(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(ssh_keys=("",))

    def test_git_identity_default_empty(self) -> None:
        s = settings_registry.load()
        self.assertEqual((s.git_user_name, s.git_user_email), ("", ""))

    def test_git_identity_roundtrip(self) -> None:
        settings_registry.set_git_identity("  Grace Hopper  ", "grace@example.com")
        s = settings_registry.load()  # persisted + stripped
        self.assertEqual(s.git_user_name, "Grace Hopper")
        self.assertEqual(s.git_user_email, "grace@example.com")

    def test_git_identity_coexists_with_ssh_keys(self) -> None:
        settings_registry.add_ssh_key("~/.ssh/a")
        settings_registry.set_git_identity("X", "x@y.z")
        s = settings_registry.load()
        self.assertEqual(s.ssh_keys, ("~/.ssh/a",))      # ssh keys preserved across a git write
        self.assertEqual(s.git_user_name, "X")


if __name__ == "__main__":
    unittest.main()
