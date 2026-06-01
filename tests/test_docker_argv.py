"""The hardened `docker create` argv renderer is the security floor — pin it."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.docker import runner  # noqa: E402
from claudeman.docker.runner import OAUTH_TOKEN_ENV  # noqa: E402
from claudeman.registry.schema import Project, Repo  # noqa: E402


def _project() -> Project:
    return Project(
        slug="landarna",
        profile="work",
        overlay="node",
        env={"NODE_ENV": "development"},
        repos=(Repo(url="git@github.com:3ADAPT/landarna-backend.git", branch="main"),),
    )


class HardenedArgvTest(unittest.TestCase):
    def setUp(self) -> None:
        self.argv = runner.build_create_argv(
            _project(),
            profile_name="work",
            version="2.1.159",
            created_iso="2026-06-01T00:00:00Z",
            claude_config_path="/state/landarna/claude-config",
            workspace_path="/state/landarna/workspace",
        )

    def test_hardening_flags_present(self) -> None:
        a = self.argv
        self.assertIn("--read-only", a)
        self.assertIn("--security-opt", a)
        self.assertIn("no-new-privileges", a)
        # --cap-drop ALL as an adjacent pair
        self.assertEqual(a[a.index("--cap-drop") + 1], "ALL")
        self.assertEqual(a[a.index("--user") + 1], "1000:1000")
        self.assertEqual(a[a.index("--pids-limit") + 1], "1024")

    def test_tmpfs_mounts(self) -> None:
        tmpfs = [a for a in self.argv if a.startswith("/tmp:") or a.startswith("/home/agent/.cache:")]
        self.assertTrue(any(t.startswith("/tmp:") and "exec" in t for t in tmpfs))
        self.assertTrue(any(t.startswith("/home/agent/.cache:") for t in tmpfs))

    def test_token_is_passthrough_never_a_value(self) -> None:
        # The token name is present as an env pass-through (no "=value").
        self.assertIn(OAUTH_TOKEN_ENV, self.argv)
        self.assertFalse(
            any(a.startswith(f"{OAUTH_TOKEN_ENV}=") for a in self.argv),
            "token must be pass-through, never inlined into argv",
        )

    def test_anthropic_keys_never_rendered(self) -> None:
        for a in self.argv:
            self.assertNotIn("ANTHROPIC_API_KEY", a)
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", a)

    def test_scrubbed_project_env_dropped(self) -> None:
        # Even if a forbidden key sneaks past schema, the renderer drops it.
        proj = Project(slug="x", env={})
        object.__setattr__(proj, "env", {"ANTHROPIC_API_KEY": "sk-leak", "FOO": "bar"})
        argv = runner.build_create_argv(proj, profile_name="home", created_iso="t")
        self.assertNotIn("ANTHROPIC_API_KEY=sk-leak", argv)
        self.assertIn("FOO=bar", argv)

    def test_persistent_binds(self) -> None:
        self.assertIn("/state/landarna/claude-config:/home/agent/.claude", self.argv)
        self.assertIn("/state/landarna/workspace:/workspace", self.argv)
        self.assertEqual(self.argv[self.argv.index("-w") + 1], "/workspace")

    def test_image_and_idle_command(self) -> None:
        self.assertEqual(self.argv[-3:], ["claude-man:node", "sleep", "infinity"])

    def test_labels_present(self) -> None:
        joined = " ".join(self.argv)
        self.assertIn("claude-man.slug=landarna", joined)
        self.assertIn("claude-man.profile=work", joined)
        self.assertIn("claude-man.repos=1", joined)


if __name__ == "__main__":
    unittest.main()
