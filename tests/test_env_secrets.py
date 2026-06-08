"""Per-project env-var VALUES: 0600 state-tier store, never config.toml, name validation."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config, env_secrets  # noqa: E402
from claudeman.registry import projects  # noqa: E402
from claudeman.registry.schema import EnvMount, Project, ValidationError  # noqa: E402


class EnvSecretsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.cfg.name
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        self.cfg.cleanup()
        self.state.cleanup()

    # -- store --------------------------------------------------------------
    def test_set_get_remove(self) -> None:
        self.assertEqual(env_secrets.load("demo"), {})
        env_secrets.set("demo", "FOO", "bar")
        env_secrets.set("demo", "BAZ", "qux")
        self.assertEqual(env_secrets.get("demo", "FOO"), "bar")
        self.assertEqual(env_secrets.load("demo"), {"FOO": "bar", "BAZ": "qux"})
        self.assertTrue(env_secrets.remove("demo", "FOO"))
        self.assertIsNone(env_secrets.get("demo", "FOO"))
        self.assertFalse(env_secrets.remove("demo", "FOO"))  # idempotent

    def test_stored_0600(self) -> None:
        env_secrets.set("demo", "FOO", "bar")
        mode = stat.S_IMODE(os.stat(config.project_env_path("demo")).st_mode)
        self.assertEqual(mode, 0o600)

    def test_lives_in_state_tier_not_config(self) -> None:
        p = str(config.project_env_path("demo").resolve())
        self.assertTrue(p.startswith(str(config.state_home().resolve())))
        self.assertFalse(p.startswith(str(config.config_home().resolve())))

    # -- schema -------------------------------------------------------------
    def test_env_mount_name_validation(self) -> None:
        self.assertEqual(EnvMount(kind="env", name="MY_VAR").target, "MY_VAR")
        for bad in ("1BAD", "has space", "has-dash", ""):
            with self.assertRaises(ValidationError):
                EnvMount(kind="env", name=bad)

    def test_forbidden_env_names_rejected(self) -> None:
        for bad in ("GH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            with self.assertRaises(ValidationError):
                EnvMount(kind="env", name=bad)

    def test_forbidden_name_variants_rejected(self) -> None:
        # Case + underscore-padding variants of a reserved name must also be rejected (no bypass).
        for bad in ("gh_token", "Gh_Token", "_GH_TOKEN", "GH_TOKEN_", "__gh_token__",
                    "anthropic_api_key", "_CLAUDE_CODE_OAUTH_TOKEN_"):
            with self.assertRaises(ValidationError, msg=bad):
                EnvMount(kind="env", name=bad)

    def test_legit_names_with_reserved_substring_allowed(self) -> None:
        # Genuinely-different names that merely contain a reserved word must NOT be over-rejected.
        for ok in ("GH_TOKEN_BACKUP", "MY_GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_REGION"):
            self.assertEqual(EnvMount(kind="env", name=ok).target, ok)

    def test_env_mount_roundtrips_through_registry(self) -> None:
        p = Project(slug="demo", env_mount=(
            EnvMount(kind="env", name="FOO"),
            EnvMount(kind="file", src="/abs/x", dst="/home/agent/x"),
        ))
        projects.save(p)
        loaded = projects.load("demo").env_mount
        self.assertEqual([(m.kind, m.target) for m in loaded],
                         [("env", "FOO"), ("file", "/home/agent/x")])


if __name__ == "__main__":
    unittest.main()
