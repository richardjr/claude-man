"""Per-project asset sync — host-side copy with backup-then-overwrite, denylist gate, bootstrap.

Pure stdlib: sets CLAUDE_MAN_CONFIG_HOME (asset source) + CLAUDE_MAN_STATE_HOME (binds + backups) to
tempdirs; nothing shells out (the container binds are plain host dirs)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import assets, config  # noqa: E402
from claudeman.registry.schema import Project, Sync, ValidationError  # noqa: E402


class AssetSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.cfg.name
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name
        self.slug = "demo"
        self.project = Project(slug=self.slug)

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        self.cfg.cleanup()
        self.state.cleanup()

    # -- path helpers (a=asset source, b=bind) --------------------------------
    def _aw(self, *p: str) -> Path:
        return config.project_assets_workspace_dir(self.slug).joinpath(*p)

    def _ac(self, *p: str) -> Path:
        return config.project_assets_claude_dir(self.slug).joinpath(*p)

    def _bw(self, *p: str) -> Path:
        return config.workspace_dir(self.slug).joinpath(*p)

    def _bc(self, *p: str) -> Path:
        return config.claude_config_dir(self.slug).joinpath(*p)

    @staticmethod
    def _write(p: Path, text: str) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def _backup_glob(self, pattern: str) -> list[Path]:
        root = config.backups_dir(self.slug)
        return list(root.glob(pattern)) if root.exists() else []

    # -- sync-in --------------------------------------------------------------
    def test_sync_in_copies_workspace_and_claude(self) -> None:
        self._write(self._aw("CLAUDE.md"), "hi")
        self._write(self._ac("skills", "foo", "SKILL.md"), "skill-body")
        rep = assets.sync_in(self.project)
        self.assertTrue(rep.ok)
        self.assertEqual(self._bw("CLAUDE.md").read_text(), "hi")
        self.assertEqual(self._bc("skills", "foo", "SKILL.md").read_text(), "skill-body")

    def test_sync_in_asset_wins_and_backs_up(self) -> None:
        self._write(self._aw("CLAUDE.md"), "NEW")
        self._write(self._bw("CLAUDE.md"), "OLD")
        rep = assets.sync_in(self.project)
        self.assertEqual(self._bw("CLAUDE.md").read_text(), "NEW")  # asset wins
        backups = self._backup_glob("*/in/workspace/CLAUDE.md")
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "OLD")  # the overwritten bind file is preserved
        self.assertIn("workspace/CLAUDE.md", rep.backed_up)

    def test_sync_in_bootstraps_stub_when_none(self) -> None:
        rep = assets.sync_in(self.project)  # nothing on disk anywhere
        self.assertTrue(self._aw("CLAUDE.md").exists())          # stub written to the asset source
        self.assertTrue(self._bw("CLAUDE.md").exists())          # and synced into the bind
        self.assertIn("# CLAUDE.md", self._bw("CLAUDE.md").read_text())
        self.assertTrue(any("bootstrap" in n for n in rep.notes))

    def test_bootstrap_skips_when_present(self) -> None:
        self._write(self._aw("CLAUDE.md"), "already here")
        self.assertIsNone(assets.bootstrap(self.project))

    def test_unchanged_file_skips_copy_and_backup(self) -> None:
        self._write(self._aw("CLAUDE.md"), "same")
        self._write(self._bw("CLAUDE.md"), "same")
        rep = assets.sync_in(self.project)
        self.assertNotIn("workspace/CLAUDE.md", rep.copied)      # identical -> skipped
        self.assertEqual(self._backup_glob("*/in/workspace/CLAUDE.md"), [])

    # -- sync-out -------------------------------------------------------------
    def test_sync_out_bind_wins_and_backs_up(self) -> None:
        self._write(self._bw("CLAUDE.md"), "NEW")
        self._write(self._aw("CLAUDE.md"), "OLD")
        rep = assets.sync_out(self.project)
        self.assertEqual(self._aw("CLAUDE.md").read_text(), "NEW")  # bind wins
        backups = self._backup_glob("*/out/workspace/CLAUDE.md")
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "OLD")
        self.assertIn("workspace/CLAUDE.md", rep.backed_up)

    def test_sync_out_noop_when_binds_absent(self) -> None:
        rep = assets.sync_out(self.project)  # no binds on disk
        self.assertTrue(rep.ok)
        self.assertEqual(rep.copied, ())
        self.assertEqual(rep.detail, "")

    # -- safety: denylist gate, settings.json, symlinks, containment, cruft ---
    def test_denylist_gate_drops_unsafe_claude_entry(self) -> None:
        proj = Project(slug=self.slug, sync=Sync(claude=("skills", "sessions")))
        self._write(self._ac("skills", "ok.md"), "x")
        self._write(self._ac("sessions", "secret.jsonl"), "leak")
        rep = assets.sync_in(proj)
        self.assertEqual(self._bc("skills", "ok.md").read_text(), "x")
        self.assertFalse(self._bc("sessions").exists())          # denylisted — never copied
        self.assertTrue(any("sessions" in n for n in rep.notes))

    def test_claude_allowlist_is_default_deny(self) -> None:
        # A hand-edited claude allowlist can only ever sync skills/agents/commands — never `projects`
        # (session transcripts), even though `projects` itself is not in the denylist.
        proj = Project(slug=self.slug, sync=Sync(claude=("skills", "projects", "custom")))
        self._write(self._ac("skills", "ok.md"), "keep")
        self._write(self._ac("projects", "hash", "session.jsonl"), "TRANSCRIPT")
        self._write(self._ac("custom", "x"), "y")
        rep = assets.sync_in(proj)
        self.assertEqual(self._bc("skills", "ok.md").read_text(), "keep")
        self.assertFalse(self._bc("projects").exists())            # transcripts never sync
        self.assertFalse(self._bc("custom").exists())
        self.assertTrue(any("projects" in n for n in rep.notes))
        self.assertTrue(any("custom" in n for n in rep.notes))

    def test_settings_json_refused(self) -> None:
        proj = Project(slug=self.slug, sync=Sync(claude=("settings.json",)))
        self._write(self._ac("settings.json"), "{}")
        rep = assets.sync_in(proj)
        self.assertFalse(self._bc("settings.json").exists())
        self.assertTrue(any("settings.json" in n for n in rep.notes))

    def test_in_tree_symlink_is_dereferenced(self) -> None:
        # A symlink whose target stays inside the asset tree is dereferenced to a real file.
        self._write(self._ac("skills", "foo", "real.md"), "real-content")
        os.symlink(self._ac("skills", "foo", "real.md"), self._ac("skills", "foo", "link.md"))
        assets.sync_in(self.project)
        dst = self._bc("skills", "foo", "link.md")
        self.assertFalse(dst.is_symlink())                       # dereferenced to a real file
        self.assertEqual(dst.read_text(), "real-content")

    def test_escaping_symlink_refused(self) -> None:
        # A symlink pointing OUTSIDE the source tree must NOT be dereferenced (exfil guard).
        target = Path(self.state.name) / "outside.md"
        target.write_text("secret-outside")
        self._ac("skills", "foo").mkdir(parents=True)
        os.symlink(target, self._ac("skills", "foo", "link.md"))
        rep = assets.sync_in(self.project)
        self.assertFalse(self._bc("skills", "foo", "link.md").exists())
        self.assertTrue(any("escapes" in n for n in rep.notes))

    def test_nested_denylisted_name_skipped(self) -> None:
        # A denylisted name (sessions/, .credentials.json) nested INSIDE an allowed tree is skipped.
        self._write(self._ac("skills", "ok.md"), "keep")
        self._write(self._ac("skills", "sessions", "x.jsonl"), "leak")
        self._write(self._ac("skills", ".credentials.json"), "creds")
        rep = assets.sync_in(self.project)
        self.assertEqual(self._bc("skills", "ok.md").read_text(), "keep")
        self.assertFalse(self._bc("skills", "sessions").exists())        # nested denylisted dir dropped
        self.assertFalse(self._bc("skills", ".credentials.json").exists())  # nested cred name dropped
        self.assertTrue(any("denylisted" in n for n in rep.notes))

    def test_nested_settings_json_skipped(self) -> None:
        self._write(self._ac("skills", "foo", "settings.json"), "{}")
        self._write(self._ac("skills", "foo", "SKILL.md"), "ok")
        assets.sync_in(self.project)
        self.assertTrue(self._bc("skills", "foo", "SKILL.md").exists())
        self.assertFalse(self._bc("skills", "foo", "settings.json").exists())

    def test_sync_out_symlink_to_credential_refused(self) -> None:
        # Sync-out: a symlink in the bind pointing at the real ~/.claude/.credentials.json (in-tree but
        # denylisted) must NOT exfiltrate it into the synced asset (config) tier.
        self._write(self._bc(".credentials.json"), "TOPSECRET")
        self._bc("skills").mkdir(parents=True)
        os.symlink(self._bc(".credentials.json"), self._bc("skills", "leak.md"))
        rep = assets.sync_out(self.project)
        self.assertFalse(self._ac("skills", "leak.md").exists())
        self.assertTrue(any("denylisted" in n for n in rep.notes))
        # the real credential never reached the config tier
        self.assertEqual(list(config.project_assets_claude_dir(self.slug).rglob("*TOPSECRET*")), [])

    def test_tree_excludes_cruft(self) -> None:
        self._write(self._ac("skills", "foo", "SKILL.md"), "keep")
        self._write(self._ac("skills", "foo", ".git", "config"), "vcs")
        self._write(self._ac("skills", "foo", "node_modules", "x.js"), "dep")
        assets.sync_in(self.project)
        self.assertTrue(self._bc("skills", "foo", "SKILL.md").exists())
        self.assertFalse(self._bc("skills", "foo", ".git").exists())
        self.assertFalse(self._bc("skills", "foo", "node_modules").exists())

    def test_invalid_sync_entry_rejected_at_construction(self) -> None:
        with self.assertRaises(ValidationError):
            Sync(workspace=("../escape",))
        with self.assertRaises(ValidationError):
            Sync(claude=("/abs",))

    def test_runtime_containment_refuses_escape(self) -> None:
        # Even a forced (construction-bypassing) escaping entry must not write outside the bind root.
        proj = Project(slug=self.slug)
        bad = Sync()
        object.__setattr__(bad, "workspace", ("../escape",))
        object.__setattr__(proj, "sync", bad)
        self._write(self._aw("..", "escape"), "pwned")           # the asset-side escape source
        rep = assets.sync_in(proj)
        self.assertFalse((config.project_state_dir(self.slug) / "escape").exists())
        self.assertTrue(any("escapes" in n for n in rep.notes))


if __name__ == "__main__":
    unittest.main()
