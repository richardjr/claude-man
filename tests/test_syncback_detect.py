"""Three-way change detection — agent authorship vs claude-man's own writes vs host drift.

Pure stdlib: isolates the bind (STATE_HOME), the asset source (CONFIG_HOME) and the host (``$HOME``)
to tempdirs. Each test seeds a baseline, mutates the relevant tier, and asserts the classification."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402
from claudeman.syncback import baseline, detect, diff  # noqa: E402


class DetectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.cfg.name
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name
        os.environ["HOME"] = self.home.name
        self.slug = "demo"
        self.bind = config.claude_config_dir(self.slug)
        self.src = config.project_assets_claude_dir(self.slug)
        self.hostc = Path(self.home.name) / ".claude"
        for d in (self.bind, self.src, self.hostc):
            d.mkdir(parents=True, exist_ok=True)

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

    def _changes_by_label(self) -> dict[str, detect.Change]:
        return {c.label: c for c in detect.detect_changes(self.slug)}

    # -- with-baseline scenarios ------------------------------------------
    def test_agent_added_skill_flagged_accept(self) -> None:
        baseline.write_baseline(self.slug)  # empty everything
        self._write(self.bind / "skills" / "new" / "SKILL.md", "agent-made")
        changes = self._changes_by_label()
        self.assertIn("skills/new/SKILL.md", changes)
        c = changes["skills/new/SKILL.md"]
        self.assertEqual(c.change_type, "added")
        self.assertEqual(c.default_decision, "accept")
        self.assertFalse(c.conflict)

    def test_claude_man_synced_file_subtracted(self) -> None:
        # byte-equal in source + bind ⇒ claude-man wrote it, not the agent (no baseline needed)
        self._write(self.src / "skills" / "s.md", "same")
        self._write(self.bind / "skills" / "s.md", "same")
        self.assertNotIn("skills/s.md", self._changes_by_label())

    def test_settings_key_modified_default_reject(self) -> None:
        self._write(self.bind / "settings.json", json.dumps({"model": "a"}))
        baseline.write_baseline(self.slug)
        self._write(self.bind / "settings.json", json.dumps({"model": "b"}))
        c = self._changes_by_label().get("settings.json/model")
        self.assertIsNotNone(c)
        self.assertEqual(c.change_type, "modified")
        self.assertEqual(c.default_decision, "reject")  # json-keys default

    def test_host_drift_forces_conflict_reject(self) -> None:
        self._write(self.bind / "agents" / "a.md", "v1")
        self._write(self.hostc / "agents" / "a.md", "v1")
        baseline.write_baseline(self.slug)
        self._write(self.bind / "agents" / "a.md", "agent-edit")   # agent changed it
        self._write(self.hostc / "agents" / "a.md", "operator-edit")  # host ALSO changed it
        c = self._changes_by_label().get("agents/a.md")
        self.assertIsNotNone(c)
        self.assertTrue(c.conflict)
        self.assertEqual(c.default_decision, "reject")

    def test_both_changed_to_same_is_noop(self) -> None:
        self._write(self.bind / "agents" / "a.md", "v1")
        self._write(self.hostc / "agents" / "a.md", "v1")
        baseline.write_baseline(self.slug)
        self._write(self.bind / "agents" / "a.md", "converged")
        self._write(self.hostc / "agents" / "a.md", "converged")
        self.assertNotIn("agents/a.md", self._changes_by_label())

    def test_deletion_against_present_host_default_reject(self) -> None:
        self._write(self.bind / "commands" / "c.md", "x")
        self._write(self.hostc / "commands" / "c.md", "x")
        baseline.write_baseline(self.slug)
        (self.bind / "commands" / "c.md").unlink()  # agent deleted it; host still has it
        c = self._changes_by_label().get("commands/c.md")
        self.assertIsNotNone(c)
        self.assertEqual(c.change_type, "deleted")
        self.assertEqual(c.default_decision, "reject")

    # -- no-baseline degradation ------------------------------------------
    def test_no_baseline_only_genuine_adds(self) -> None:
        self._write(self.src / "skills" / "synced.md", "lib")     # claude-man source
        self._write(self.bind / "skills" / "synced.md", "lib")    # same in bind
        self._write(self.bind / "skills" / "agent.md", "novel")   # agent-authored, no source
        self.assertFalse(config.baseline_path(self.slug).exists())
        labels = self._changes_by_label()
        self.assertIn("skills/agent.md", labels)
        self.assertNotIn("skills/synced.md", labels)  # not flooded with claude-man's own file
        self.assertTrue(config.baseline_path(self.slug).exists())  # fresh baseline written

    # -- change_diff -------------------------------------------------------
    def test_change_diff_masks_and_shows_host_vs_container(self) -> None:
        self._write(self.bind / "settings.json", json.dumps({"model": "b", "apiKey": "sk-ant-SECRETSECRETSECRETSECRET12"}))
        self._write(self.hostc / "settings.json", json.dumps({"model": "a"}))
        change = detect.Change("settings.json", "json-keys", "user", "modified", "model", False, "reject")
        out = "\n".join(diff.change_diff(self.slug, change))
        self.assertIn("model", out)
        self.assertIn('"a"', out)  # host (a/) value
        self.assertIn('"b"', out)  # container (b/) value
        # a secret in the same file is never leaked even when diffing another key
        secret_change = detect.Change("settings.json", "json-keys", "user", "added", "apiKey", False, "reject")
        secret_out = "\n".join(diff.change_diff(self.slug, secret_change))
        self.assertNotIn("sk-ant-SECRETSECRETSECRETSECRET12", secret_out)


if __name__ == "__main__":
    unittest.main()
