"""Config-dir seeding + token loading — the Phase-1 create prerequisites (no docker needed)."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402
from claudeman.profiles import seed  # noqa: E402
from claudeman.registry import profiles  # noqa: E402
from claudeman.registry.schema import Profile, Project  # noqa: E402


class CaptureSeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = tempfile.TemporaryDirectory()
        self.src = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name
        root = Path(self.src.name)
        (root / "agents").mkdir()
        (root / "agents" / "rev.md").write_text("agent")
        (root / "plugins" / "cache").mkdir(parents=True)
        (root / "plugins" / "cache" / "junk").write_text("x")
        (root / "plugins" / "blocklist.json").write_text("{}")
        (root / "plugins" / "foo.md").write_text("plugin")
        (root / "settings.json").write_text(json.dumps({
            "hooks": {"SessionEnd": [{"hooks": [{"command": "sync-claude.sh --save"}]}]},
            "statusLine": {"type": "command", "command": "bun x ccusage"},
            "permissions": {"allow": ["Bash"]},
            "oauthAccount": {"emailAddress": "x@y.io"},
        }))

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        self.state.cleanup()
        self.src.cleanup()

    def test_capture_strips_hooks_and_excludes_cruft(self) -> None:
        from claudeman.registry.schema import ProfileSeed
        prof = Profile(
            name="home",
            seed=ProfileSeed(source=self.src.name, include=("settings.json", "agents/", "plugins/")),
        )
        captured = seed.capture_profile_seed(prof)
        dest = config.profile_seed_dir("home")
        self.assertIn("settings.json", captured)
        patched = json.loads((dest / "settings.json").read_text())
        self.assertNotIn("hooks", patched)        # host SessionEnd hook stripped
        self.assertNotIn("statusLine", patched)   # bun statusLine stripped
        self.assertNotIn("oauthAccount", patched)  # identity key dropped
        self.assertIn("permissions", patched)     # real settings kept
        self.assertTrue((dest / "agents" / "rev.md").exists())
        self.assertTrue((dest / "plugins" / "foo.md").exists())
        self.assertFalse((dest / "plugins" / "cache").exists())       # machine-local excluded
        self.assertFalse((dest / "plugins" / "blocklist.json").exists())


class SeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        self.tmp.cleanup()

    def test_seed_creates_0700_dir_and_scrubbed_identity(self) -> None:
        profile = Profile(name="home", account_email="me@example.com", default=True)
        cfg = seed.seed_project_config(Project(slug="demo"), profile)
        self.assertTrue(cfg.is_dir())
        self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o700)
        data = json.loads((cfg / ".claude.json").read_text())
        self.assertTrue(data["hasCompletedOnboarding"])
        self.assertEqual(data["installMethod"], "native")
        self.assertEqual(data["oauthAccount"], {"emailAddress": "me@example.com"})
        # identity uuids must NEVER be seeded
        for k in ("accountUuid", "userID", "organizationUuid"):
            self.assertNotIn(k, data["oauthAccount"])

    def test_seed_without_profile_has_empty_identity(self) -> None:
        cfg = seed.seed_project_config(Project(slug="demo2"), None)
        data = json.loads((cfg / ".claude.json").read_text())
        self.assertEqual(data["oauthAccount"], {})
        self.assertTrue(data["hasCompletedOnboarding"])

    def test_seed_does_not_clobber_existing_identity(self) -> None:
        cfg = config.claude_config_dir("demo3")
        cfg.mkdir(parents=True)
        (cfg / ".claude.json").write_text('{"sentinel": true}')
        seed.seed_project_config(Project(slug="demo3"), None)
        self.assertEqual(json.loads((cfg / ".claude.json").read_text()), {"sentinel": True})

    def test_account_mismatch_guard_and_reseed(self) -> None:
        from claudeman import lifecycle
        work = Profile(name="work", account_email="w@co.com")
        home = Profile(name="home", account_email="h@me.com")
        project = Project(slug="acct")
        seed.seed_project_config(project, work)
        self.assertEqual(seed.read_seeded_email("acct"), "w@co.com")
        # same account → no conflict; different account → returns the existing email
        self.assertIsNone(lifecycle.account_mismatch(project, work))
        self.assertEqual(lifecycle.account_mismatch(project, home), "w@co.com")
        # forced re-seed switches the recorded identity to the new account
        seed.seed_project_config(project, home, overwrite_identity=True)
        self.assertEqual(seed.read_seeded_email("acct"), "h@me.com")

    def test_load_token_roundtrip(self) -> None:
        self.assertIsNone(profiles.load_token("home"))  # not minted yet
        tok = config.profile_token_path("home")
        tok.parent.mkdir(parents=True, exist_ok=True)
        tok.write_text("  sk-ant-oat-xyz\n")  # whitespace stripped
        self.assertEqual(profiles.load_token("home"), "sk-ant-oat-xyz")


if __name__ == "__main__":
    unittest.main()
