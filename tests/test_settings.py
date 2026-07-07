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

    # -- image (on-start claude-version) settings ------------------------------
    def test_image_defaults(self) -> None:
        s = settings_registry.load()
        self.assertTrue(s.image_update_check)
        self.assertEqual(s.claude_channel, "latest")
        self.assertEqual(s.claude_version_pin, "")

    def test_image_roundtrip(self) -> None:
        settings_registry.set_image_settings(update_check=False, claude_channel="stable",
                                             claude_version_pin="2.1.150")
        s = settings_registry.load()
        self.assertFalse(s.image_update_check)
        self.assertEqual(s.claude_channel, "stable")
        self.assertEqual(s.claude_version_pin, "2.1.150")

    def test_image_partial_update_leaves_others(self) -> None:
        settings_registry.set_image_settings(claude_channel="stable")
        settings_registry.set_image_settings(claude_version_pin="  2.1.140  ")  # stripped
        s = settings_registry.load()
        self.assertEqual(s.claude_channel, "stable")       # preserved across the second write
        self.assertEqual(s.claude_version_pin, "2.1.140")

    def test_image_no_pin_clears(self) -> None:
        settings_registry.set_image_settings(claude_version_pin="2.1.150")
        settings_registry.set_image_settings(claude_version_pin="")
        self.assertEqual(settings_registry.load().claude_version_pin, "")

    def test_image_coexists_with_ssh_and_git(self) -> None:
        settings_registry.add_ssh_key("~/.ssh/a")
        settings_registry.set_git_identity("X", "x@y.z")
        settings_registry.set_image_settings(claude_channel="stable")
        s = settings_registry.load()
        self.assertEqual(s.ssh_keys, ("~/.ssh/a",))
        self.assertEqual(s.git_user_name, "X")
        self.assertEqual(s.claude_channel, "stable")

    def test_schema_rejects_bad_channel(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(claude_channel="nightly")

    def test_setter_rejects_bad_channel(self) -> None:
        with self.assertRaises(ValidationError):
            settings_registry.set_image_settings(claude_channel="nightly")

    def test_parse_coerces_unknown_channel_to_default(self) -> None:
        # A hand-edited bad channel must NOT brick load() — it coerces to the default.
        s = settings_registry._parse({"image": {"claude_channel": "weekly"}})
        self.assertEqual(s.claude_channel, "latest")

    # -- terminal / opener preferences -----------------------------------------
    def test_terminal_defaults_to_auto(self) -> None:
        s = settings_registry.load()
        self.assertEqual(s.terminal_program, "")
        self.assertEqual(s.terminal_command, ())
        self.assertEqual(s.opener_command, ())

    def test_terminal_program_roundtrip(self) -> None:
        settings_registry.set_terminal(program="kitty")
        self.assertEqual(settings_registry.load().terminal_program, "kitty")
        settings_registry.set_terminal(program="")  # back to auto
        self.assertEqual(settings_registry.load().terminal_program, "")

    def test_terminal_custom_roundtrip(self) -> None:
        settings_registry.set_terminal(program="custom",
                                       command=["myterm", "-e", "{argv}"])
        s = settings_registry.load()
        self.assertEqual(s.terminal_program, "custom")
        self.assertEqual(s.terminal_command, ("myterm", "-e", "{argv}"))

    def test_terminal_custom_without_argv_element_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            settings_registry.set_terminal(program="custom", command=["myterm", "-e"])

    def test_parse_coerces_broken_custom_to_auto(self) -> None:
        # A hand-edited custom template missing '{argv}' must not brick load().
        s = settings_registry._parse(
            {"terminal": {"program": "custom", "command": ["myterm"]}})
        self.assertEqual(s.terminal_program, "")

    def test_parse_rejects_non_list_command(self) -> None:
        with self.assertRaises(ValidationError):
            settings_registry._parse({"terminal": {"command": "not-a-list"}})

    def test_opener_roundtrip_and_clear(self) -> None:
        settings_registry.set_opener(["nautilus", "--new-window"])
        self.assertEqual(settings_registry.load().opener_command, ("nautilus", "--new-window"))
        settings_registry.set_opener([])
        self.assertEqual(settings_registry.load().opener_command, ())

    def test_terminal_tint_defaults_off(self) -> None:
        self.assertFalse(settings_registry.load().terminal_tint)

    def test_terminal_tint_roundtrip(self) -> None:
        settings_registry.set_terminal_tint(True)
        self.assertTrue(settings_registry.load().terminal_tint)
        settings_registry.set_terminal_tint(False)
        self.assertFalse(settings_registry.load().terminal_tint)

    def test_terminal_tint_coexists_with_terminal_program(self) -> None:
        settings_registry.set_terminal(program="kitty")
        settings_registry.set_terminal_tint(True)
        s = settings_registry.load()
        self.assertEqual(s.terminal_program, "kitty")   # the tint write preserves the program
        self.assertTrue(s.terminal_tint)

    def test_terminal_coexists_with_other_sections(self) -> None:
        settings_registry.add_ssh_key("~/.ssh/a")
        settings_registry.set_git_identity("X", "x@y.z")
        settings_registry.set_terminal(program="kitty")
        settings_registry.set_opener(["open"])
        s = settings_registry.load()
        self.assertEqual(s.ssh_keys, ("~/.ssh/a",))      # preserved across the terminal writes
        self.assertEqual(s.git_user_name, "X")
        self.assertEqual(s.terminal_program, "kitty")
        self.assertEqual(s.opener_command, ("open",))

    def test_shell_history_defaults_off(self) -> None:
        self.assertFalse(settings_registry.load().shell_persist_history)

    def test_shell_history_roundtrip_and_coexists(self) -> None:
        settings_registry.add_ssh_key("~/.ssh/a")        # an unrelated section to preserve
        settings_registry.set_shell_history(True)
        s = settings_registry.load()
        self.assertTrue(s.shell_persist_history)
        self.assertEqual(s.ssh_keys, ("~/.ssh/a",))      # preserved across the shell write
        settings_registry.set_shell_history(False)
        self.assertFalse(settings_registry.load().shell_persist_history)


if __name__ == "__main__":
    unittest.main()
