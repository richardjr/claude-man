"""Baseline manifest — fingerprints the container bind + the real host config; the narrow
``mcpServers``-only read is the one deliberate ``.claude.json`` exception (invariant 5).

Pure stdlib: isolates CLAUDE_MAN_CONFIG_HOME/STATE_HOME (the bind) and ``$HOME`` (the host side) to
tempdirs so nothing touches the operator's real ``~/.claude``."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402
from claudeman.syncback import baseline  # noqa: E402


class BaselineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.cfg.name
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name
        os.environ["HOME"] = self.home.name
        self.slug = "demo"
        self.cdir = config.claude_config_dir(self.slug)  # the container config bind
        self.cdir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        for k in ("CLAUDE_MAN_CONFIG_HOME", "CLAUDE_MAN_STATE_HOME"):
            os.environ.pop(k, None)
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        self.cfg.cleanup()
        self.state.cleanup()
        self.home.cleanup()

    @staticmethod
    def _write(p: Path, text: str) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def test_tree_and_settings_fingerprint(self) -> None:
        self._write(self.cdir / "skills" / "foo" / "SKILL.md", "body")
        self._write(self.cdir / "settings.json", json.dumps({"model": "x", "userID": "u"}))
        snap = baseline.snapshot_container(self.slug)
        self.assertEqual(snap["skills"]["kind"], "tree-symlink")
        self.assertTrue(snap["skills"]["files"]["foo/SKILL.md"].startswith("sha256:"))
        self.assertIn("model", snap["settings.json"]["keys"])
        self.assertNotIn("userID", snap["settings.json"]["keys"])  # denied json key excluded

    def test_denied_basename_excluded_from_tree(self) -> None:
        self._write(self.cdir / "skills" / ".credentials.json", "secret")
        self._write(self.cdir / "skills" / "ok.md", "ok")
        files = baseline.snapshot_container(self.slug)["skills"]["files"]
        self.assertIn("ok.md", files)
        self.assertNotIn(".credentials.json", files)

    def test_mcp_read_is_servers_only(self) -> None:
        self._write(self.cdir / ".claude.json", json.dumps({
            "oauthAccount": {"emailAddress": "a@b.c"},
            "userID": "secret-uuid",
            "mcpServers": {"git": {"command": "x"}},
        }))
        servers = baseline.read_mcp_servers(self.cdir / ".claude.json")
        self.assertEqual(list(servers), ["git"])  # ONLY mcpServers lifted; identity discarded
        self.assertIn("git", baseline.snapshot_container(self.slug)["mcp"]["servers"])

    def test_symlink_escape_blocked(self) -> None:
        skills = self.cdir / "skills"
        skills.mkdir(parents=True)
        (skills / "evil").symlink_to("/etc/passwd")
        files = baseline.snapshot_container(self.slug)["skills"]["files"]
        self.assertEqual(files.get("evil"), "symlink-blocked")

    def test_write_read_roundtrip(self) -> None:
        self._write(self.cdir / "agents" / "a.md", "x")
        baseline.write_baseline(self.slug)
        m = baseline.read_baseline(self.slug)
        self.assertEqual(m["version"], baseline.MANIFEST_VERSION)
        self.assertIn("agents", m["container"])
        self.assertIn("agents", m["host"])  # host side present (empty under temp HOME)

    def test_read_missing_baseline_is_empty(self) -> None:
        self.assertEqual(baseline.read_baseline("nope"), {})


if __name__ == "__main__":
    unittest.main()
