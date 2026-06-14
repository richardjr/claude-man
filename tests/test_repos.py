"""checkout/repos.py pure helpers — credential masking + workspace containment (no git, no network)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.checkout import repos  # noqa: E402


class MaskUrlCredsTest(unittest.TestCase):
    """mask_url_creds is run over every git stderr/stdout before it surfaces (an auth failure echoes
    the remote URL, which may embed credentials) — so its redaction is a security boundary."""

    def test_userinfo_redacted(self) -> None:
        self.assertEqual(repos.mask_url_creds("https://user:tok@host/x"), "https://***@host/x")

    def test_token_userinfo_redacted(self) -> None:
        out = repos.mask_url_creds("remote: https://x-access-token:ghp_SECRET@github.com/org/repo.git")
        self.assertNotIn("ghp_SECRET", out)
        self.assertIn("https://***@github.com/org/repo.git", out)

    def test_scp_style_url_untouched(self) -> None:
        # scp-style git@host:path has no scheme:// and carries no secret — must be left intact.
        self.assertEqual(repos.mask_url_creds("git@github.com:org/repo.git"),
                         "git@github.com:org/repo.git")

    def test_plain_text_and_empty_unchanged(self) -> None:
        self.assertEqual(repos.mask_url_creds("fatal: could not read from remote"),
                         "fatal: could not read from remote")
        self.assertEqual(repos.mask_url_creds(""), "")


class ContainmentTest(unittest.TestCase):
    """is_within backs the clone/ff-merge workspace-escape guard (and fsmerge symlink containment)."""

    def test_child_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertTrue(repos.is_within(root / "a", root))
            self.assertTrue(repos.is_within(root / "a" / "b", root))

    def test_root_not_within_itself(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertFalse(repos.is_within(root, root))

    def test_parent_and_sibling_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ws"
            root.mkdir()
            self.assertFalse(repos.is_within(root.parent, root))
            self.assertFalse(repos.is_within(root.parent / "other", root))


if __name__ == "__main__":
    unittest.main()
