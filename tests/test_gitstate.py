"""Pure git-state parsing: porcelain v2 -> RepoState, error classification, summary glyphs.

Dependency-free (no subprocess, no docker, no textual): the parser is fed raw porcelain text.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.checkout import gitstate  # noqa: E402
from claudeman.checkout import repos as repos_mod  # noqa: E402
from claudeman.checkout.gitstate import RepoState  # noqa: E402
from claudeman.registry.schema import Repo  # noqa: E402

R = Repo(url="git@github.com:org/landarna-backend.git", branch="main")  # resolved_dir = landarna-backend

# Clean, on configured branch, in sync with upstream.
CLEAN = "# branch.oid abc1234def\0# branch.head main\0# branch.upstream origin/main\0# branch.ab +0 -0\0"

# Ahead 2 / behind 1, dirty: staged+unstaged file, staged-only, a rename (+ trailing orig field), untracked.
AHEAD_DIRTY = (
    "# branch.oid abc1234def\0# branch.head main\0"
    "# branch.upstream origin/main\0# branch.ab +2 -1\0"
    "1 MM N... 100644 100644 100644 1111111 2222222 a.txt\0"
    "1 M. N... 100644 100644 100644 3333333 3333333 b.txt\0"
    "2 R. N... 100644 100644 100644 4444444 4444444 R100 new.txt\0old.txt\0"
    "? untracked.txt\0"
)

# Detached HEAD, no upstream: the branch.upstream + branch.ab lines are ABSENT.
DETACHED_NO_UPSTREAM = "# branch.oid 9999999\0# branch.head (detached)\0"

# On a branch that does NOT match the configured "main", clean.
WRONG_BRANCH = "# branch.oid deadbee\0# branch.head feature/x\0# branch.upstream origin/feature/x\0# branch.ab +0 -0\0"

# Mid-merge conflict (unmerged entry).
CONFLICT = (
    "# branch.oid abc1234def\0# branch.head main\0# branch.upstream origin/main\0# branch.ab +0 -0\0"
    "u UU N... 100644 100644 100644 100644 1 2 3 conflicted.txt\0"
)

# Local branch with no remote-tracking branch yet (fresh): upstream/ab absent, but on a branch.
NO_UPSTREAM = "# branch.oid abc1234def\0# branch.head main\0"


class ParsePorcelainTest(unittest.TestCase):
    def test_clean(self) -> None:
        s = gitstate.parse_porcelain(CLEAN, repo=R)
        self.assertFalse(s.dirty)
        self.assertEqual((s.ahead, s.behind), (0, 0))
        self.assertEqual(s.upstream, "origin/main")
        self.assertEqual(s.branch, "main")
        self.assertTrue(s.branch_matches_config)
        self.assertEqual(s.last_sha, "abc1234")
        self.assertEqual(s.kind, gitstate.OK)

    def test_ahead_dirty_counts_and_rename_skip(self) -> None:
        s = gitstate.parse_porcelain(AHEAD_DIRTY, repo=R)
        self.assertEqual((s.ahead, s.behind), (2, 1))
        # MM -> staged+unstaged; M. -> staged; R. -> staged; ? -> untracked. orig-path field skipped.
        self.assertEqual((s.staged, s.unstaged, s.untracked), (3, 1, 1))
        self.assertTrue(s.dirty)
        self.assertEqual(s.unmerged, 0)

    def test_detached_no_upstream(self) -> None:
        s = gitstate.parse_porcelain(DETACHED_NO_UPSTREAM, repo=R)
        self.assertTrue(s.detached)
        self.assertEqual(s.branch, "")
        self.assertIsNone(s.upstream)
        self.assertEqual((s.ahead, s.behind), (0, 0))
        self.assertFalse(s.branch_matches_config)

    def test_wrong_branch(self) -> None:
        s = gitstate.parse_porcelain(WRONG_BRANCH, repo=R)
        self.assertEqual(s.branch, "feature/x")
        self.assertFalse(s.branch_matches_config)
        self.assertFalse(s.dirty)

    def test_conflict_counts_unmerged_not_staged(self) -> None:
        s = gitstate.parse_porcelain(CONFLICT, repo=R)
        self.assertEqual(s.unmerged, 1)
        self.assertEqual((s.staged, s.unstaged), (0, 0))  # 'u' is not double-counted as 1/2 entries
        self.assertTrue(s.dirty)
        self.assertEqual(gitstate.state_label(s), "conflict (1)")

    def test_no_upstream_on_branch(self) -> None:
        s = gitstate.parse_porcelain(NO_UPSTREAM, repo=R)
        self.assertIsNone(s.upstream)
        self.assertFalse(s.detached)
        self.assertEqual(s.branch, "main")
        self.assertEqual(gitstate.state_label(s), "no upstream")

    def test_state_style_clean_green_dirty_red(self) -> None:
        clean = RepoState(dir="a", kind=gitstate.OK, present=True, branch="main",
                          upstream="origin/main")
        dirty = RepoState(dir="b", kind=gitstate.OK, present=True, branch="main",
                          upstream="origin/main", unstaged=1)
        self.assertEqual(gitstate.state_style(clean), "green")
        self.assertEqual(gitstate.state_style(dirty), "red")
        self.assertEqual(gitstate.state_style(gitstate.parse_porcelain(CONFLICT, repo=R)), "red")
        self.assertEqual(gitstate.state_style(RepoState(dir="c", kind=gitstate.UNCLONED)), "yellow")
        self.assertEqual(gitstate.state_style(RepoState(dir="d", kind=gitstate.ERROR,
                                                        error="boom")), "red")
        self.assertEqual(gitstate.state_style(RepoState(dir="e", kind=gitstate.OWNERSHIP,
                                                        present=True)), "red")
        self.assertEqual(gitstate.state_style(gitstate.parse_porcelain(NO_UPSTREAM, repo=R)), "yellow")


class ClassifyErrorTest(unittest.TestCase):
    def test_dubious_ownership(self) -> None:
        err = "fatal: detected dubious ownership in repository at '/workspace/x'"
        self.assertEqual(gitstate.classify_error(err), gitstate.OWNERSHIP)

    def test_safe_directory_hint(self) -> None:
        self.assertEqual(gitstate.classify_error("add safe.directory to allow"), gitstate.OWNERSHIP)

    def test_generic_error(self) -> None:
        self.assertEqual(gitstate.classify_error("fatal: not a git repository"), gitstate.ERROR)


class SummarizeTest(unittest.TestCase):
    @staticmethod
    def _clean(dir_: str) -> RepoState:
        return RepoState(dir=dir_, kind=gitstate.OK, present=True, branch="main",
                         upstream="origin/main", config_branch="main", branch_matches_config=True)

    def test_empty(self) -> None:
        self.assertEqual(gitstate.summarize([]).line, "0 repos")

    def test_all_clean(self) -> None:
        line = gitstate.summarize([self._clean("a"), self._clean("b"), self._clean("c")]).line
        self.assertEqual(line, "3 ✓")

    def test_mixed_clean_dirty_ahead(self) -> None:
        dirty = RepoState(dir="client", kind=gitstate.OK, present=True, branch="main",
                          upstream="origin/main", config_branch="main", branch_matches_config=True,
                          dirty=True, unstaged=3, ahead=1)
        line = gitstate.summarize([self._clean("backend"), dirty]).line
        self.assertEqual(line, "1 ✓  client:~↑1")

    def test_uncloned(self) -> None:
        unc = RepoState(dir="packages", kind=gitstate.UNCLONED, present=False, config_branch="main")
        line = gitstate.summarize([self._clean("backend"), unc]).line
        self.assertEqual(line, "1 ✓  1 uncloned")

    def test_error_dominates(self) -> None:
        err = RepoState(dir="broken", kind=gitstate.ERROR, present=False, error="boom")
        line = gitstate.summarize([self._clean("a"), err]).line
        self.assertEqual(line, "✗ 1 error  1 ok")

    def test_branch_drift_glyph(self) -> None:
        drift = RepoState(dir="client", kind=gitstate.OK, present=True, branch="feat/x",
                          upstream="origin/feat/x", config_branch="main", branch_matches_config=False)
        line = gitstate.summarize([drift]).line
        self.assertEqual(line, "client:⚠")


class CellLabelTest(unittest.TestCase):
    """The pure cell renderers shared by the CLI table and the TUI detail panel."""

    def test_branch_label_variants(self) -> None:
        clean = RepoState(dir="a", present=True, branch="main", config_branch="main",
                          branch_matches_config=True)
        drift = RepoState(dir="a", present=True, branch="feat/x", config_branch="main",
                          branch_matches_config=False)
        det = RepoState(dir="a", present=True, detached=True, last_sha="abc1234")
        unc = RepoState(dir="a", kind=gitstate.UNCLONED, present=False, config_branch="main")
        self.assertEqual(gitstate.branch_label(clean), "main")
        self.assertEqual(gitstate.branch_label(drift), "feat/x (cfg:main)")
        self.assertEqual(gitstate.branch_label(det), "detached@abc1234")
        self.assertEqual(gitstate.branch_label(unc), "-")

    def test_ab_and_commit_labels(self) -> None:
        synced = RepoState(dir="a", present=True, upstream="origin/main", ahead=2, behind=1,
                           last_sha="abc1234", last_subject="bump")
        no_up = RepoState(dir="a", present=True, upstream=None)
        unc = RepoState(dir="a", present=False)
        self.assertEqual(gitstate.ab_label(synced), "2/1")
        self.assertEqual(gitstate.ab_label(no_up), "—")
        self.assertEqual(gitstate.ab_label(unc), "—")
        self.assertEqual(gitstate.commit_label(synced), "abc1234 bump")
        self.assertEqual(gitstate.commit_label(unc), "—")

    def test_ab_style_flags_non_zero(self) -> None:
        # A non-0/0 repo (unpushed/unpulled commits) pops yellow; 0/0 and "—" recede to dim.
        base = dict(dir="a", present=True, upstream="origin/main")
        self.assertEqual(gitstate.ab_style(RepoState(**base, ahead=2)), "yellow")
        self.assertEqual(gitstate.ab_style(RepoState(**base, behind=1)), "yellow")
        self.assertEqual(gitstate.ab_style(RepoState(**base)), "dim")
        self.assertEqual(gitstate.ab_style(RepoState(dir="a", present=True, upstream=None)), "dim")
        self.assertEqual(gitstate.ab_style(RepoState(dir="a", present=False, ahead=3)), "dim")


class PullDecisionTest(unittest.TestCase):
    """The pure ff-only eligibility policy: only a clean, on-upstream, behind-only repo is pullable;
    every other state is a safety SKIP with a reason (never a force)."""

    @staticmethod
    def _state(**kw) -> RepoState:
        base = dict(dir="r", kind=gitstate.OK, present=True, branch="main",
                    upstream="origin/main", config_branch="main", branch_matches_config=True)
        base.update(kw)
        return RepoState(**base)

    def test_clean_behind_is_eligible(self) -> None:
        ok, reason = gitstate.pull_decision(self._state(behind=3))
        self.assertTrue(ok)
        self.assertIn("fast-forward", reason)
        self.assertIn("3", reason)

    def test_up_to_date_skips(self) -> None:
        ok, reason = gitstate.pull_decision(self._state(behind=0, ahead=0))
        self.assertFalse(ok)
        self.assertEqual(reason, "up to date")

    def test_dirty_skips(self) -> None:
        ok, reason = gitstate.pull_decision(self._state(behind=2, dirty=True, unstaged=1))
        self.assertFalse(ok)
        self.assertIn("uncommitted", reason)

    def test_diverged_skips(self) -> None:
        ok, reason = gitstate.pull_decision(self._state(ahead=2, behind=1))
        self.assertFalse(ok)
        self.assertIn("diverged", reason)

    def test_ahead_only_skips(self) -> None:
        ok, reason = gitstate.pull_decision(self._state(ahead=2, behind=0))
        self.assertFalse(ok)
        self.assertIn("local commit", reason)

    def test_detached_skips(self) -> None:
        ok, reason = gitstate.pull_decision(self._state(detached=True, branch="", behind=1))
        self.assertFalse(ok)
        self.assertIn("detached", reason)

    def test_no_upstream_skips(self) -> None:
        ok, reason = gitstate.pull_decision(self._state(upstream=None, behind=0))
        self.assertFalse(ok)
        self.assertIn("upstream", reason)

    def test_uncloned_skips(self) -> None:
        ok, reason = gitstate.pull_decision(RepoState(dir="r", kind=gitstate.UNCLONED, present=False))
        self.assertFalse(ok)
        self.assertIn("not cloned", reason)

    def test_ownership_skips(self) -> None:
        ok, reason = gitstate.pull_decision(RepoState(dir="r", kind=gitstate.OWNERSHIP, present=False))
        self.assertFalse(ok)
        self.assertIn("ownership", reason)

    def test_conflict_skips_before_dirty(self) -> None:
        ok, reason = gitstate.pull_decision(self._state(behind=1, dirty=True, unmerged=1))
        self.assertFalse(ok)
        self.assertIn("conflict", reason)

    def test_wrong_branch_eligible_with_note(self) -> None:
        ok, reason = gitstate.pull_decision(self._state(
            behind=1, branch="feat/x", branch_matches_config=False, upstream="origin/feat/x"))
        self.assertTrue(ok)
        self.assertIn("feat/x", reason)
        self.assertIn("cfg:main", reason)


class DeleteRiskTest(unittest.TestCase):
    """The pure delete-time sync policy: a repo is RISKY when ``rm -rf``ing its checkout would lose
    work not on a remote. Errs toward risky (a false positive only nags); behind-only / non-config
    branch / clean are all safe."""

    @staticmethod
    def _state(**kw) -> RepoState:
        base = dict(dir="r", kind=gitstate.OK, present=True, branch="main",
                    upstream="origin/main", config_branch="main", branch_matches_config=True)
        base.update(kw)
        return RepoState(**base)

    def test_clean_synced_is_safe(self) -> None:
        risky, reason = gitstate.delete_risk(self._state())
        self.assertFalse(risky)
        self.assertIn("clean", reason)

    def test_clean_behind_is_safe(self) -> None:
        # behind-only means a peer pushed; our work is all on the remote — no loss risk.
        risky, _ = gitstate.delete_risk(self._state(behind=5))
        self.assertFalse(risky)

    def test_wrong_branch_alone_is_safe(self) -> None:
        # On a non-config branch but otherwise clean+tracking: not a data-loss risk.
        risky, _ = gitstate.delete_risk(self._state(
            branch="feat/x", branch_matches_config=False, upstream="origin/feat/x"))
        self.assertFalse(risky)

    def test_uncommitted_is_risky(self) -> None:
        risky, reason = gitstate.delete_risk(self._state(staged=1, unstaged=2))
        self.assertTrue(risky)
        self.assertIn("uncommitted", reason)

    def test_untracked_counts_as_uncommitted(self) -> None:
        risky, reason = gitstate.delete_risk(self._state(untracked=3))
        self.assertTrue(risky)
        self.assertIn("uncommitted", reason)

    def test_unpushed_ahead_is_risky(self) -> None:
        risky, reason = gitstate.delete_risk(self._state(ahead=2))
        self.assertTrue(risky)
        self.assertIn("unpushed", reason)

    def test_stash_is_risky(self) -> None:
        risky, reason = gitstate.delete_risk(self._state(stash=1))
        self.assertTrue(risky)
        self.assertIn("stash", reason)

    def test_conflict_is_risky(self) -> None:
        risky, reason = gitstate.delete_risk(self._state(unmerged=1))
        self.assertTrue(risky)
        self.assertIn("conflict", reason)

    def test_detached_is_risky(self) -> None:
        risky, reason = gitstate.delete_risk(self._state(detached=True, branch="", upstream=None))
        self.assertTrue(risky)
        self.assertIn("detached", reason)

    def test_no_upstream_is_risky(self) -> None:
        risky, reason = gitstate.delete_risk(self._state(upstream=None))
        self.assertTrue(risky)
        self.assertIn("upstream", reason)

    def test_uncloned_is_safe(self) -> None:
        risky, reason = gitstate.delete_risk(RepoState(dir="r", kind=gitstate.UNCLONED, present=False))
        self.assertFalse(risky)
        self.assertIn("not cloned", reason)

    def test_ownership_cannot_verify_is_risky(self) -> None:
        risky, reason = gitstate.delete_risk(RepoState(dir="r", kind=gitstate.OWNERSHIP, present=False))
        self.assertTrue(risky)
        self.assertIn("verify", reason)

    def test_error_cannot_verify_is_risky(self) -> None:
        risky, reason = gitstate.delete_risk(
            RepoState(dir="r", kind=gitstate.ERROR, present=False, error="boom"))
        self.assertTrue(risky)
        self.assertIn("verify", reason)

    def test_reason_lists_every_concurrent_hazard(self) -> None:
        risky, reason = gitstate.delete_risk(self._state(unstaged=1, ahead=2, stash=1, unmerged=1))
        self.assertTrue(risky)
        for needle in ("conflict", "uncommitted", "unpushed", "stash"):
            self.assertIn(needle, reason)


class RepoUrlShapeTest(unittest.TestCase):
    def test_accepts_scp_and_scheme_forms(self) -> None:
        for ok in ("git@github.com:org/repo.git", "https://github.com/org/repo",
                   "https://github.com/org/repo.git", "ssh://git@host/o/r.git",
                   "file:///tmp/x"):
            self.assertTrue(repos_mod.looks_like_repo_url(ok), ok)

    def test_rejects_garbage(self) -> None:
        for bad in ("", "   ", "hello world", "not a url", "/just/a/path"):
            self.assertFalse(repos_mod.looks_like_repo_url(bad), repr(bad))


if __name__ == "__main__":
    unittest.main()
