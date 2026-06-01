"""Registry: load/validate project + profile TOML, and resolve the default profile."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.registry import profiles, projects  # noqa: E402
from claudeman.registry.schema import Project, ValidationError  # noqa: E402

PROJECT_TOML = """\
[project]
slug = "landarna"
profile = "work"
overlay = "node"
extra_apt = ["jq"]

[project.egress]
mode = "strict"
allowlist = ["registry.yarnpkg.com"]

[project.env]
NODE_ENV = "development"

[[project.repos]]
url = "git@github.com:3ADAPT/landarna-backend.git"
branch = "main"
dir = "landarna-backend"
"""

PROFILE_TOML = """\
[profile]
name = "home"
display_name = "Home"
account_email = "me@example.com"
default = true
"""


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.tmp.name
        (Path(self.tmp.name) / "projects").mkdir(parents=True)
        (Path(self.tmp.name) / "profiles").mkdir(parents=True)
        (Path(self.tmp.name) / "projects" / "landarna.toml").write_text(PROJECT_TOML)
        (Path(self.tmp.name) / "profiles" / "home.toml").write_text(PROFILE_TOML)

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        self.tmp.cleanup()

    def test_load_project(self) -> None:
        p = projects.load("landarna")
        self.assertEqual(p.slug, "landarna")
        self.assertEqual(p.profile, "work")
        self.assertEqual(p.overlay, "node")
        self.assertEqual(p.egress, "strict")
        self.assertEqual(p.allowlist, ("registry.yarnpkg.com",))
        self.assertEqual(p.env, {"NODE_ENV": "development"})
        self.assertEqual(len(p.repos), 1)
        self.assertEqual(p.repos[0].resolved_dir(), "landarna-backend")
        self.assertEqual(p.image, "claude-man:node")
        self.assertEqual(p.container, "claude-man-landarna")

    def test_list_slugs(self) -> None:
        self.assertEqual(projects.list_slugs(), ["landarna"])

    def test_load_profile_and_default(self) -> None:
        prof = profiles.load("home")
        self.assertTrue(prof.default)
        self.assertEqual(profiles.default_profile().name, "home")

    def test_resolve_inherits_default(self) -> None:
        p = Project(slug="noproj")  # no explicit profile
        self.assertEqual(profiles.resolve_for_project(p).name, "home")


class ValidationTest(unittest.TestCase):
    def test_bad_slug_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Project(slug="Bad Slug!")

    def test_forbidden_env_key_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Project(slug="x", env={"ANTHROPIC_API_KEY": "sk-leak"})

    def test_bad_overlay_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Project(slug="x", overlay="haskell")


if __name__ == "__main__":
    unittest.main()
