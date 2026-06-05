"""Lifecycle helpers that don't need a docker daemon (workspace-ownership pre-flight)."""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config, lifecycle  # noqa: E402
from claudeman.checkout.gitstate import RepoState  # noqa: E402
from claudeman.checkout.repos import RepoResult  # noqa: E402
from claudeman.registry.schema import Project, Repo  # noqa: E402


class WorkspaceOwnedTest(unittest.TestCase):
    """`_ensure_workspace_owned` must make workspace/ operator-owned before docker binds it, or Docker
    auto-creates the bind source as root and host-side clones fail with EACCES."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        self.tmp.cleanup()

    def test_creates_missing_workspace(self) -> None:
        ws = config.workspace_dir("demo")
        self.assertFalse(ws.exists())
        self.assertIsNone(lifecycle._ensure_workspace_owned("demo"))
        self.assertTrue(ws.is_dir())

    def test_noop_when_already_owned(self) -> None:
        ws = config.workspace_dir("demo")
        ws.mkdir(parents=True)
        (ws / "keep").write_text("x")
        self.assertIsNone(lifecycle._ensure_workspace_owned("demo"))
        self.assertTrue((ws / "keep").exists())  # untouched

    def test_foreign_empty_is_reclaimed(self) -> None:
        # Simulate Docker's root-owned-but-empty workspace by making our own dir look foreign.
        ws = config.workspace_dir("demo")
        ws.mkdir(parents=True)
        with mock.patch.object(os, "getuid", lambda: 999999):
            err = lifecycle._ensure_workspace_owned("demo")
        self.assertIsNone(err)
        self.assertTrue(ws.is_dir())  # rmdir + mkdir -> still present (now "owned")

    def test_foreign_nonempty_is_surfaced_not_destroyed(self) -> None:
        ws = config.workspace_dir("demo")
        ws.mkdir(parents=True)
        (ws / "uncommitted-work").write_text("precious")
        with mock.patch.object(os, "getuid", lambda: 999999):
            err = lifecycle._ensure_workspace_owned("demo")
        self.assertIsNotNone(err)
        self.assertIn("sudo chown", err)
        self.assertTrue((ws / "uncommitted-work").exists())  # never destroyed

    def test_foreign_unreadable_returns_error_not_exception(self) -> None:
        # A foreign-owned dir the operator can't iterdir (mode 0700) must surface the hint, not raise
        # (the iterdir/rmdir region must be inside the OSError guard).
        ws = config.workspace_dir("demo")
        ws.mkdir(parents=True)
        os.chmod(ws, 0o000)
        try:
            with mock.patch.object(os, "getuid", lambda: 999999):
                err = lifecycle._ensure_workspace_owned("demo")
            self.assertIsNotNone(err)
            self.assertIn("sudo chown", err)
        finally:
            os.chmod(ws, 0o755)  # restore so TemporaryDirectory cleanup can recurse


class PullApplyTest(unittest.TestCase):
    """`pull_apply` ff-merges only the still-eligible repos under the lock, folds per-repo results, and
    refuses outright on a host/container uid mismatch. The git/registry seams are mocked so the test
    stays dependency-free (no subprocess, no filesystem registry)."""

    PROJECT = Project(slug="demo", repos=(
        Repo(url="git@github.com:o/a.git"),  # resolved_dir = a
        Repo(url="git@github.com:o/b.git"),  # resolved_dir = b
    ))
    # `a` is clean + behind (eligible); `b` is dirty (must be skipped, never merged).
    STATES = [
        RepoState(dir="a", kind="ok", present=True, branch="main", upstream="origin/main",
                  config_branch="main", branch_matches_config=True, behind=2),
        RepoState(dir="b", kind="ok", present=True, branch="main", upstream="origin/main",
                  config_branch="main", branch_matches_config=True, dirty=True, unstaged=1, behind=1),
    ]

    def _patches(self, *, uid_match: bool, merged: list[str]):
        def fake_ff(slug, repo):
            merged.append(repo.resolved_dir())
            return RepoResult(repo.resolved_dir(), True, "fast-forwarded")

        return [
            mock.patch.object(lifecycle.gitstate, "host_uid_matches_container", lambda: uid_match),
            mock.patch.object(lifecycle.projects_registry, "exists", lambda s: True),
            mock.patch.object(lifecycle, "_slug_lock", lambda s: contextlib.nullcontext()),
            mock.patch.object(lifecycle.projects_registry, "load", lambda s: self.PROJECT),
            mock.patch.object(lifecycle.gitstate, "project_states", lambda p: list(self.STATES)),
            mock.patch.object(lifecycle.repos, "ff_merge_one", fake_ff),
        ]

    def test_pulls_eligible_skips_dirty(self) -> None:
        merged: list[str] = []
        with contextlib.ExitStack() as stack:
            for p in self._patches(uid_match=True, merged=merged):
                stack.enter_context(p)
            res = lifecycle.pull_apply("demo", ["a", "b"])
        self.assertTrue(res.ok)
        self.assertEqual(merged, ["a"])          # only the eligible repo was fast-forwarded
        self.assertIn("pulled 1", res.detail)
        self.assertIn("skipped 1", res.detail)
        self.assertIn("uncommitted", res.detail)  # b's skip reason, re-decided under the lock

    def test_uid_mismatch_refuses(self) -> None:
        merged: list[str] = []
        with contextlib.ExitStack() as stack:
            for p in self._patches(uid_match=False, merged=merged):
                stack.enter_context(p)
            res = lifecycle.pull_apply("demo", ["a", "b"])
        self.assertFalse(res.ok)
        self.assertIn("dubious ownership", res.detail)
        self.assertEqual(merged, [])              # nothing merged when the uid guard refuses


if __name__ == "__main__":
    unittest.main()
