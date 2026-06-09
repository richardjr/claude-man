"""Lifecycle helpers that don't need a docker daemon (workspace-ownership pre-flight)."""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import subprocess  # noqa: E402

from claudeman import assets, config, lifecycle  # noqa: E402
from claudeman.checkout.gitstate import RepoState  # noqa: E402
from claudeman.checkout.repos import RepoResult  # noqa: E402
from claudeman.docker.images import BuildResult  # noqa: E402
from claudeman.registry.schema import Project, Repo, Settings, Sync  # noqa: E402
from claudeman.updates import ReleaseCheck  # noqa: E402


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


class DeleteProjectTest(unittest.TestCase):
    """Full teardown over real tmp config+state dirs (exercising the real flock + delete_definition),
    with only ``runner.remove`` mocked so no docker daemon is touched."""

    def setUp(self) -> None:
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.cfg.name
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name
        config.projects_config_dir().mkdir(parents=True, exist_ok=True)
        config.project_toml_path("demo").write_text('[project]\nslug = "demo"\noverlay = "base"\n')
        ws = config.workspace_dir("demo")
        ws.mkdir(parents=True)
        (ws / "work.txt").write_text("precious")          # unsynced agent work
        cc = config.claude_config_dir("demo")
        cc.mkdir(parents=True)
        (cc / ".claude.json").write_text("{}")

    def tearDown(self) -> None:
        for k in ("CLAUDE_MAN_CONFIG_HOME", "CLAUDE_MAN_STATE_HOME"):
            os.environ.pop(k, None)
        self.cfg.cleanup()
        self.state.cleanup()

    def test_full_delete_removes_container_state_and_registry(self) -> None:
        with mock.patch.object(lifecycle.runner, "remove") as rm:
            res = lifecycle.delete_project("demo")
        self.assertTrue(res.ok)
        rm.assert_called_once_with("demo")
        self.assertFalse(config.project_toml_path("demo").exists())   # registry gone
        self.assertFalse(config.project_state_dir("demo").exists())   # workspace + claude-config gone

    def test_keep_workspace_preserves_checkout_but_drops_the_rest(self) -> None:
        with mock.patch.object(lifecycle.runner, "remove") as rm:
            res = lifecycle.delete_project("demo", keep_workspace=True)
        self.assertTrue(res.ok)
        rm.assert_called_once_with("demo")
        self.assertFalse(config.project_toml_path("demo").exists())             # registry gone
        self.assertTrue((config.workspace_dir("demo") / "work.txt").exists())   # checkout kept
        self.assertFalse(config.claude_config_dir("demo").exists())             # claude state gone

    def test_missing_project_is_an_error_and_skips_docker(self) -> None:
        with mock.patch.object(lifecycle.runner, "remove") as rm:
            res = lifecycle.delete_project("nope")
        self.assertFalse(res.ok)
        rm.assert_not_called()

    def test_idempotent_when_state_dir_already_absent(self) -> None:
        shutil.rmtree(config.project_state_dir("demo"))   # simulate a never-created project
        with mock.patch.object(lifecycle.runner, "remove"):
            res = lifecycle.delete_project("demo")
        self.assertTrue(res.ok)
        self.assertFalse(config.project_toml_path("demo").exists())


class AddEnvVarTest(unittest.TestCase):
    """add_env_var stores the value + registers the mount transactionally (no docker; temp registry)."""

    def setUp(self) -> None:
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.cfg.name
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name
        lifecycle.projects_registry.save(Project(slug="demo"))

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        self.cfg.cleanup()
        self.state.cleanup()

    def test_success_stores_value_and_mount(self) -> None:
        res = lifecycle.add_env_var("demo", "FOO", "bar")
        self.assertTrue(res.ok)
        self.assertEqual([m.target for m in lifecycle.projects_registry.load("demo").env_mount], ["FOO"])
        self.assertEqual(lifecycle.env_secrets.get("demo", "FOO"), "bar")

    def test_value_store_failure_rolls_back_mount(self) -> None:
        def boom(slug, name, value):
            raise OSError("disk full")

        with mock.patch.object(lifecycle.env_secrets, "set", boom):
            res = lifecycle.add_env_var("demo", "FOO", "bar")
        self.assertFalse(res.ok)
        self.assertIn("failed to store value", res.detail)
        # the value-less mount must NOT linger in the registry (rolled back)
        self.assertEqual(lifecycle.projects_registry.load("demo").env_mount, ())

    def test_remove_cleans_stored_value(self) -> None:
        lifecycle.add_env_var("demo", "FOO", "bar")
        res = lifecycle.remove_mount("demo", "FOO")
        self.assertTrue(res.ok)
        self.assertIsNone(lifecycle.env_secrets.get("demo", "FOO"))
        self.assertEqual(lifecycle.projects_registry.load("demo").env_mount, ())


class SyncHooksTest(unittest.TestCase):
    """up() syncs assets IN before start; stop() syncs OUT only after a successful stop. Both fold the
    note into the Result and never let a sync fault break start/stop. Seams (runner, ensure_created,
    assets, registry) are mocked so the test stays dependency-free."""

    @staticmethod
    def _cp(rc: int = 0) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], rc, "", "")

    def test_up_syncs_in_before_start(self) -> None:
        order: list[str] = []
        rep = assets.SyncReport(True, "asset sync-in: 1 copied")
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(lifecycle, "ensure_created",
                lambda p, on_progress=None: lifecycle.Result(True, "created claude-man-demo")))
            st.enter_context(mock.patch.object(lifecycle.assets, "sync_in",
                lambda p, on_progress=None: (order.append("sync"), rep)[1]))
            st.enter_context(mock.patch.object(lifecycle.runner, "start",
                lambda slug: (order.append("start"), self._cp(0))[1]))
            res = lifecycle.up(Project(slug="demo"))
        self.assertTrue(res.ok)
        self.assertEqual(order, ["sync", "start"])  # assets land before the container starts
        self.assertIn("asset sync-in", res.detail)

    def test_up_disabled_sync_skips(self) -> None:
        called: list[str] = []
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(lifecycle, "ensure_created",
                lambda p, on_progress=None: lifecycle.Result(True, "created claude-man-demo")))
            st.enter_context(mock.patch.object(lifecycle.assets, "sync_in",
                lambda p, on_progress=None: called.append("x")))
            st.enter_context(mock.patch.object(lifecycle.runner, "start", lambda slug: self._cp(0)))
            res = lifecycle.up(Project(slug="demo", sync=Sync(enabled=False)))
        self.assertTrue(res.ok)
        self.assertEqual(called, [])  # disabled -> sync_in never invoked

    def test_up_sync_in_failure_does_not_block_start(self) -> None:
        started: list[str] = []

        def boom(p, on_progress=None):
            raise OSError("disk full")

        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(lifecycle, "ensure_created",
                lambda p, on_progress=None: lifecycle.Result(True, "created claude-man-demo")))
            st.enter_context(mock.patch.object(lifecycle.assets, "sync_in", boom))
            st.enter_context(mock.patch.object(lifecycle.runner, "start",
                lambda slug: (started.append(slug), self._cp(0))[1]))
            res = lifecycle.up(Project(slug="demo"))
        self.assertTrue(res.ok)               # start still happened despite the sync fault
        self.assertEqual(started, ["demo"])
        self.assertIn("sync-in error", res.detail)

    def test_stop_syncs_out_after_stop_ok(self) -> None:
        order: list[str] = []
        rep = assets.SyncReport(True, "asset sync-out: 1 copied")
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(lifecycle.runner, "stop",
                lambda slug: (order.append("stop"), self._cp(0))[1]))
            st.enter_context(mock.patch.object(lifecycle.projects_registry, "exists", lambda s: True))
            st.enter_context(mock.patch.object(lifecycle.projects_registry, "load",
                lambda s: Project(slug="demo")))
            st.enter_context(mock.patch.object(lifecycle.assets, "sync_out",
                lambda p, on_progress=None: (order.append("sync"), rep)[1]))
            res = lifecycle.stop("demo")
        self.assertTrue(res.ok)
        self.assertEqual(order, ["stop", "sync"])  # never read the binds before the container stops
        self.assertIn("asset sync-out", res.detail)

    def test_stop_failed_skips_sync_out(self) -> None:
        called: list[str] = []
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(lifecycle.runner, "stop", lambda slug: self._cp(1)))
            st.enter_context(mock.patch.object(lifecycle.assets, "sync_out",
                lambda p, on_progress=None: called.append("x")))
            res = lifecycle.stop("demo")
        self.assertFalse(res.ok)
        self.assertEqual(called, [])

    def test_stop_orphan_no_registry_skips_sync(self) -> None:
        called: list[str] = []
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(lifecycle.runner, "stop", lambda slug: self._cp(0)))
            st.enter_context(mock.patch.object(lifecycle.projects_registry, "exists", lambda s: False))
            st.enter_context(mock.patch.object(lifecycle.assets, "sync_out",
                lambda p, on_progress=None: called.append("x")))
            res = lifecycle.stop("orphan")
        self.assertTrue(res.ok)
        self.assertEqual(called, [])  # no registry entry -> no sync config -> skip


class CheckUpdateTest(unittest.TestCase):
    """The on-start claude-version decision matrix (read-only; no docker/network — all mocked)."""

    def _check(self, *, settings, current, channel_version=None, project=None):
        proj = project or Project(slug="p", overlay="base")
        rc = ReleaseCheck(channel_version, "" if channel_version else "offline")
        with mock.patch.object(lifecycle.settings_registry, "load", return_value=settings), \
             mock.patch.object(lifecycle.images, "image_claude_version", return_value=current), \
             mock.patch.object(lifecycle.updates, "resolve_channel", return_value=rc):
            return lifecycle.check_update(proj)

    def test_disabled_does_nothing(self) -> None:
        chk = self._check(settings=Settings(image_update_check=False), current="2.1.160",
                          channel_version="2.1.169")
        self.assertFalse(chk.prompt)
        self.assertEqual(chk.build_to, "")
        self.assertIn("disabled", chk.note)

    def test_channel_newer_prompts(self) -> None:
        chk = self._check(settings=Settings(), current="2.1.160", channel_version="2.1.169")
        self.assertTrue(chk.prompt)
        self.assertEqual(chk.target, "2.1.169")
        self.assertEqual(chk.current, "2.1.160")
        self.assertEqual(chk.build_to, "")  # prompt-gated, not auto

    def test_channel_up_to_date_no_prompt(self) -> None:
        chk = self._check(settings=Settings(), current="2.1.169", channel_version="2.1.169")
        self.assertFalse(chk.prompt)
        self.assertIn("up to date", chk.note)

    def test_channel_older_than_image_no_prompt(self) -> None:
        # e.g. tracking stable (2.1.153) while the image is newer (2.1.160) — never downgrade.
        chk = self._check(settings=Settings(claude_channel="stable"), current="2.1.160",
                          channel_version="2.1.153")
        self.assertFalse(chk.prompt)

    def test_fresh_project_auto_builds_channel_no_prompt(self) -> None:
        # No image yet (current None -> "") -> build_to set so the first build tracks the channel.
        chk = self._check(settings=Settings(), current=None, channel_version="2.1.169")
        self.assertFalse(chk.prompt)
        self.assertEqual(chk.build_to, "2.1.169")

    def test_offline_fails_open(self) -> None:
        chk = self._check(settings=Settings(), current="2.1.160", channel_version=None)
        self.assertFalse(chk.prompt)
        self.assertEqual(chk.build_to, "")
        self.assertEqual(chk.target, "")

    def test_global_pin_drift_prompts(self) -> None:
        chk = self._check(settings=Settings(claude_version_pin="2.1.150"), current="2.1.160",
                          channel_version="2.1.169")
        self.assertTrue(chk.prompt)
        self.assertEqual(chk.target, "2.1.150")  # explicit pin wins, even as a downgrade

    def test_global_pin_match_no_prompt(self) -> None:
        chk = self._check(settings=Settings(claude_version_pin="2.1.150"), current="2.1.150")
        self.assertFalse(chk.prompt)

    def test_pin_fresh_project_auto_builds_to_pin(self) -> None:
        chk = self._check(settings=Settings(claude_version_pin="2.1.150"), current=None)
        self.assertFalse(chk.prompt)
        self.assertEqual(chk.build_to, "2.1.150")

    def test_per_project_pin_beats_channel(self) -> None:
        proj = Project(slug="p", overlay="base", claude_version="2.1.155")
        chk = self._check(settings=Settings(), current="2.1.160", channel_version="2.1.169",
                          project=proj)
        self.assertTrue(chk.prompt)
        self.assertEqual(chk.target, "2.1.155")  # project pin, not the channel's 2.1.169

    def test_malformed_config_fails_open_not_raises(self) -> None:
        # check_update documents 'NEVER raises'. A hand-broken config.toml (real tomllib.load raising
        # TOMLDecodeError) must fold into a no-action result so the CLI `project up` doesn't crash.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = tmp.name
        self.addCleanup(lambda: os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None))
        (Path(tmp.name) / "config.toml").write_text("[image\nbroken = ")  # invalid TOML
        chk = lifecycle.check_update(Project(slug="p", overlay="base"))  # must NOT raise
        self.assertFalse(chk.prompt)
        self.assertEqual(chk.build_to, "")
        self.assertIn("config", chk.note)


class MaybeRebuildForUpdateTest(unittest.TestCase):
    """The rebuild+recreate helper: skip a running container, recreate a stopped one, fail open."""

    @staticmethod
    def _proj() -> Project:
        return Project(slug="p", overlay="base")

    def test_running_container_is_not_rebuilt(self) -> None:
        with mock.patch.object(lifecycle.runner, "is_running", return_value=True), \
             mock.patch.object(lifecycle.images, "rebuild_chain") as rb, \
             mock.patch.object(lifecycle.runner, "remove") as rm:
            lifecycle._maybe_rebuild_for_update(self._proj(), "2.1.169", on_progress=None)
        rb.assert_not_called()
        rm.assert_not_called()

    def test_rebuild_ok_recreates_existing_container(self) -> None:
        with mock.patch.object(lifecycle.runner, "is_running", return_value=False), \
             mock.patch.object(lifecycle.images, "rebuild_chain",
                               return_value=BuildResult(True, ["base"], "rebuilt")), \
             mock.patch.object(lifecycle.runner, "exists", return_value=True), \
             mock.patch.object(lifecycle.runner, "remove") as rm:
            lifecycle._maybe_rebuild_for_update(self._proj(), "2.1.169", on_progress=None)
        rm.assert_called_once()  # removed so ensure_created recreates on the fresh image

    def test_rebuild_ok_no_container_no_remove(self) -> None:
        with mock.patch.object(lifecycle.runner, "is_running", return_value=False), \
             mock.patch.object(lifecycle.images, "rebuild_chain",
                               return_value=BuildResult(True, ["base"], "rebuilt")), \
             mock.patch.object(lifecycle.runner, "exists", return_value=False), \
             mock.patch.object(lifecycle.runner, "remove") as rm:
            lifecycle._maybe_rebuild_for_update(self._proj(), "2.1.169", on_progress=None)
        rm.assert_not_called()

    def test_rebuild_failure_fails_open(self) -> None:
        # A failed rebuild leaves the existing container/image untouched — start proceeds on it.
        with mock.patch.object(lifecycle.runner, "is_running", return_value=False), \
             mock.patch.object(lifecycle.images, "rebuild_chain",
                               return_value=BuildResult(False, [], "boom")), \
             mock.patch.object(lifecycle.runner, "exists", return_value=True), \
             mock.patch.object(lifecycle.runner, "remove") as rm:
            lifecycle._maybe_rebuild_for_update(self._proj(), "2.1.169", on_progress=None)
        rm.assert_not_called()


class UpRebuildToTest(unittest.TestCase):
    """`up` invokes the rebuild helper iff rebuild_to is set; ensure_created short-circuits the rest."""

    def test_rebuild_to_set_invokes_helper(self) -> None:
        proj = Project(slug="p", overlay="base")
        with mock.patch.object(lifecycle, "_maybe_rebuild_for_update") as mr, \
             mock.patch.object(lifecycle, "ensure_created",
                               return_value=lifecycle.Result(False, "stop here")):
            res = lifecycle.up(proj, rebuild_to="2.1.169")
        mr.assert_called_once()
        self.assertFalse(res.ok)

    def test_rebuild_to_empty_skips_helper(self) -> None:
        proj = Project(slug="p", overlay="base")
        with mock.patch.object(lifecycle, "_maybe_rebuild_for_update") as mr, \
             mock.patch.object(lifecycle, "ensure_created",
                               return_value=lifecycle.Result(False, "stop here")):
            lifecycle.up(proj, rebuild_to="")
        mr.assert_not_called()


if __name__ == "__main__":
    unittest.main()
