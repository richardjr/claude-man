"""Review-gated merge — backup-first, gated tree copy, inverse settings patch, MCP gate-only,
audit commit, global lock. The invariant-5 crux: secrets/identity never written, immune keys never
clobbered, nothing lost (backup before overwrite), denied paths never staged.

Pure stdlib + real ``git`` in a temp audit dir (per the Phase-5 plan). Isolates the bind (STATE_HOME),
the asset source (CONFIG_HOME) and the host (``$HOME``)."""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402
from claudeman.syncback import baseline, detect, merge  # noqa: E402


class MergeTest(unittest.TestCase):
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
        self.hostc = Path(self.home.name) / ".claude"
        for d in (self.bind, self.hostc):
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

    def _accept_all(self) -> dict[str, str]:
        return {c.label: "accept" for c in detect.detect_changes(self.slug)}

    # -- tree apply --------------------------------------------------------
    def test_accept_tree_file_writes_host_and_audits(self) -> None:
        baseline.write_baseline(self.slug)
        self._write(self.bind / "skills" / "new.md", "agent-made")
        rep = merge.apply_accepted(self.slug, {"skills/new.md": "accept"})
        self.assertTrue(rep.ok)
        self.assertIn("skills/new.md", rep.applied)
        self.assertEqual((self.hostc / "skills" / "new.md").read_text(), "agent-made")
        self.assertTrue(rep.audit_commit)  # a commit was made
        self.assertTrue((config.sync_audit_dir() / self.slug / "skills" / "new.md").exists())
        # baseline refreshed ⇒ re-detect is clean
        self.assertEqual(detect.detect_changes(self.slug), [])

    def test_reject_is_not_written(self) -> None:
        baseline.write_baseline(self.slug)
        self._write(self.bind / "skills" / "y.md", "x")
        rep = merge.apply_accepted(self.slug, {"skills/y.md": "reject"})
        self.assertEqual(rep.applied, ())
        self.assertFalse((self.hostc / "skills" / "y.md").exists())

    def test_backup_before_overwrite(self) -> None:
        self._write(self.bind / "skills" / "x.md", "old")
        self._write(self.hostc / "skills" / "x.md", "old")
        baseline.write_baseline(self.slug)
        self._write(self.bind / "skills" / "x.md", "new")  # agent edit
        rep = merge.apply_accepted(self.slug, {"skills/x.md": "accept"})
        self.assertTrue(rep.ok)
        self.assertEqual((self.hostc / "skills" / "x.md").read_text(), "new")
        backups = list(config.backups_dir(self.slug).rglob("skills/x.md"))
        self.assertTrue(backups, "host target should be backed up before overwrite")
        self.assertEqual(backups[0].read_text(), "old")

    def test_accepted_deletion_removes_host_file(self) -> None:
        self._write(self.bind / "commands" / "c.md", "x")
        self._write(self.hostc / "commands" / "c.md", "x")
        baseline.write_baseline(self.slug)
        (self.bind / "commands" / "c.md").unlink()  # agent deleted it
        rep = merge.apply_accepted(self.slug, {"commands/c.md": "accept"})
        self.assertTrue(rep.ok)
        self.assertFalse((self.hostc / "commands" / "c.md").exists())
        self.assertTrue(list(config.backups_dir(self.slug).rglob("commands/c.md")))

    def test_symlink_escape_refused_at_apply(self) -> None:
        baseline.write_baseline(self.slug)
        (self.bind / "skills").mkdir(parents=True, exist_ok=True)
        (self.bind / "skills" / "evil").symlink_to("/etc/passwd")
        rep = merge.apply_accepted(self.slug, {"skills/evil": "accept"})
        self.assertFalse(rep.ok)
        self.assertTrue(any("skills/evil" in r for r in rep.refused))
        self.assertFalse((self.hostc / "skills" / "evil").exists())

    # -- settings inverse field-patch -------------------------------------
    def test_settings_patch_preserves_immune_and_skips_denied(self) -> None:
        self._write(self.hostc / "settings.json", json.dumps({"hooks": {"a": 1}, "model": "old"}))
        self._write(self.bind / "settings.json", json.dumps({"model": "old"}))
        baseline.write_baseline(self.slug)
        self._write(self.bind / "settings.json", json.dumps({
            "model": "new", "statusLine": "EVIL", "permissions": {"allow": ["x"]}, "userID": "uid",
        }))
        decisions = {
            "settings.json/model": "accept",
            "settings.json/statusLine": "accept",   # immune — must be refused even when accepted
            "settings.json/permissions": "accept",
        }
        rep = merge.apply_accepted(self.slug, decisions)
        host = json.loads((self.hostc / "settings.json").read_text())
        self.assertEqual(host["model"], "new")           # overlaid
        self.assertEqual(host["hooks"], {"a": 1})         # untouched host-structural
        self.assertNotIn("statusLine", host)              # immune key never written
        self.assertEqual(host["permissions"], {"allow": ["x"]})
        self.assertNotIn("userID", host)                  # denied key never appears
        self.assertIn("settings.json/model", rep.applied)
        self.assertNotIn("settings.json/statusLine", rep.applied)

    # -- MCP gate-only -----------------------------------------------------
    def test_mcp_is_gate_only(self) -> None:
        baseline.write_baseline(self.slug)
        self._write(self.bind / ".claude.json", json.dumps({"mcpServers": {"git": {"command": "x"}}}))
        rep = merge.apply_accepted(self.slug, {"mcp/git": "accept"})
        self.assertNotIn("mcp/git", rep.applied)
        self.assertTrue(any("MCP apply deferred" in n for n in rep.notes))
        self.assertFalse((Path(self.home.name) / ".claude.json").exists())  # host never written

    # -- audit staging denylist re-assert ---------------------------------
    def test_audit_never_stages_denied_path(self) -> None:
        secret = self.bind / ".credentials.json"
        self._write(secret, "TOPSECRET")
        merge._audit_commit(self.slug, [(".credentials.json", secret, False)], "ts")
        self.assertFalse((config.sync_audit_dir() / self.slug / ".credentials.json").exists())

    # -- no-baseline plan→apply consistency (regression) ------------------
    def test_no_baseline_plan_then_apply_still_writes(self) -> None:
        # No baseline at all: a plan detects the agent add, and a follow-up apply (which re-detects)
        # must STILL write it — the implicit reference persisted by detect excludes the agent's file.
        self.assertFalse(config.baseline_path(self.slug).exists())
        self._write(self.bind / "skills" / "n.md", "x")
        plan_labels = [c.label for c in detect.detect_changes(self.slug)]
        self.assertIn("skills/n.md", plan_labels)  # plan saw it
        rep = merge.apply_accepted(self.slug, {"skills/n.md": "accept"})
        self.assertTrue(rep.ok, rep.detail)
        self.assertEqual((self.hostc / "skills" / "n.md").read_text(), "x")  # apply still wrote it

    # -- TOCTOU symlink-flip exfil (HIGH, from the adversarial review) -----
    def test_replace_with_file_refuses_out_of_tree_leaf_symlink(self) -> None:
        # A leaf symlink whose target escapes the tree (absolute / out-of-tree) must be refused.
        from claudeman.syncback import fsmerge
        root = self.bind / "skills"
        root.mkdir(parents=True, exist_ok=True)
        secret = Path(self.home.name) / "operator-secret.txt"
        secret.write_text("OPERATOR SSH KEY")
        (root / "leak").symlink_to(secret)  # absolute target, escapes `root`
        dst = self.hostc / "skills" / "leak"
        with self.assertRaises(OSError):
            fsmerge.replace_with_file(dst, anchor=root, rel_parts=("leak",))
        self.assertFalse(dst.exists())  # the operator secret never reached the host target

    def test_replace_with_file_refuses_intermediate_symlink_dir(self) -> None:
        # The verifier's bypass: the agent swaps an INTERMEDIATE directory for a symlink to an
        # out-of-tree dir whose leaf is a (regular-file) copy of a secret. The all-O_NOFOLLOW walk
        # must refuse the symlinked intermediate, not follow it.
        from claudeman.syncback import fsmerge
        root = self.bind / "skills"
        root.mkdir(parents=True, exist_ok=True)
        evil = Path(self.home.name) / "evil"
        evil.mkdir()
        (evil / "leaf.md").write_text("OPERATOR SECRET")
        (root / "myskill").symlink_to(evil)  # intermediate dir is a symlink
        dst = self.hostc / "skills" / "myskill" / "leaf.md"
        with self.assertRaises(OSError):
            fsmerge.replace_with_file(dst, anchor=root, rel_parts=("myskill", "leaf.md"))
        self.assertFalse(dst.exists())

    def test_replace_with_file_copies_relative_in_tree_symlink(self) -> None:
        # A legit RELATIVE in-tree leaf symlink is still dereferenced and copied.
        from claudeman.syncback import fsmerge
        root = self.bind / "skills"
        (root / "sub").mkdir(parents=True, exist_ok=True)
        (root / "real.md").write_text("ok")
        os.symlink("../real.md", root / "sub" / "alias")  # relative, in-tree
        dst = self.hostc / "skills" / "sub" / "alias"
        fsmerge.replace_with_file(dst, anchor=root, rel_parts=("sub", "alias"))
        self.assertEqual(dst.read_text(), "ok")

    def test_nested_denied_basename_caught(self) -> None:
        from claudeman.syncback import denylist
        # a denied literal basename smuggled inside an allowed tree must be caught at any depth
        self.assertTrue(denylist.is_denied_path("skills/foo/.credentials.json"))
        self.assertTrue(denylist.is_denied_path("agents/sessions/x.jsonl"))
        self.assertTrue(denylist.is_denied_path("commands/history.jsonl"))
        self.assertFalse(denylist.is_denied_path("skills/foo/legit.md"))

    # -- global lock -------------------------------------------------------
    def test_held_lock_refuses_merge(self) -> None:
        lock_path = config.syncback_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "w")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            rep = merge.apply_accepted(self.slug, {})
            self.assertFalse(rep.ok)
            self.assertTrue(any("another sync-back merge" in n for n in rep.notes))
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


if __name__ == "__main__":
    unittest.main()
