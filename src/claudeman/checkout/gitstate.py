"""Host-side live git state per project repo (read-only; Phase 3).

Split into a PURE parser (``parse_porcelain``, ``classify_error``, ``summarize``) and a thin
subprocess wrapper (``scan_repo``, ``project_states``) — the same renderer/exec split used by
``docker/runner.py``. The parser turns one ``git status --porcelain=v2 --branch -z`` call into a
``RepoState`` with NO subprocess and NO docker, so it is fully unit-testable.

A single status call yields branch, upstream, ahead/behind, and the dirty/untracked file list.
Ahead/behind comes from the ``# branch.ab`` header, which git computes against the branch's ACTUAL
``@{upstream}`` — so it stays correct after an in-container ``git checkout`` (the old ``fetch_all``
hard-coded ``origin/<cfg-branch>``). The scan never fetches: it reports against whatever
remote-tracking ref already exists locally. ``project sync-repos`` fetches first (``repos.fetch_all``)
then scans.

Never mutates: no clone/fetch/reset, no registry writes, no container access. The gh PAT / ssh-agent
stays host-side (CLAUDE.md invariant 1 family).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

from .. import config
from ..registry.schema import Project, Repo
from . import repos as repos_mod

GIT_TIMEOUT_S = 8.0  # generous: the scan never fetches, so this only catches a true hang

# RepoState.kind values
OK = "ok"
UNCLONED = "uncloned"      # registry knows the repo, workspace has no .git yet
OWNERSHIP = "ownership"    # host uid != container uid 1000 -> git "dubious ownership"
ERROR = "error"           # timeout / not-a-git-dir / git fatal


@dataclass(frozen=True)
class RepoState:
    dir: str                       # repo.resolved_dir() — the row's identity
    kind: str = UNCLONED           # OK | UNCLONED | OWNERSHIP | ERROR
    present: bool = False          # a .git was found and `git status` succeeded
    branch: str = ""               # actual checked-out branch ("" when detached/unknown)
    detached: bool = False         # HEAD not on a branch
    upstream: str | None = None    # "origin/main"; None when no tracking branch
    ahead: int = 0
    behind: int = 0
    dirty: bool = False            # any staged/unstaged/untracked/unmerged change
    staged: int = 0                # index-side changes (X column != '.')
    unstaged: int = 0              # worktree-side changes (Y column != '.')
    untracked: int = 0
    unmerged: int = 0              # conflict ('u') entries
    config_branch: str = ""        # the registry-configured branch
    branch_matches_config: bool = True
    last_sha: str = ""             # short sha of HEAD
    last_subject: str = ""         # HEAD commit subject (first line)
    stash: int = 0
    error: str = ""                # masked, single-line detail for the failure path


@dataclass(frozen=True)
class ProjectGitSummary:
    line: str                                              # compact one-liner for a table cell
    states: tuple[RepoState, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Pure layer (unit-tested without a subprocess)
# ---------------------------------------------------------------------------
def classify_error(stderr: str) -> str:
    """Map a failing ``git status`` stderr to a RepoState ``kind``.

    "dubious ownership" / a safe.directory hint (exit 128 when the host uid != the container's
    uid 1000 wrote the tree) is a DISTINCT state with its own remediation, not a generic error.
    """
    low = (stderr or "").lower()
    if "dubious ownership" in low or "safe.directory" in low:
        return OWNERSHIP
    return ERROR


def parse_porcelain(
    status_text: str,
    *,
    repo: Repo,
    last_sha: str = "",
    last_subject: str = "",
    stash: int = 0,
) -> RepoState:
    """Pure: ``git status --porcelain=v2 --branch -z`` stdout -> RepoState. No subprocess, no docker.

    Header lines (NUL-separated): ``# branch.head <name|(detached)>``, ``# branch.oid <sha|(initial)>``,
    ``# branch.upstream <ref>`` (ABSENT when no tracking branch), ``# branch.ab +A -B`` (ABSENT with no
    upstream). Entry lines: ``1 XY …``/``2 XY … <path><NUL><orig>`` (rename consumes the next field),
    ``? path`` (untracked), ``u XY …`` (unmerged), ``! path`` (ignored — uncounted).
    """
    branch = ""
    detached = False
    upstream: str | None = None
    ahead = behind = 0
    staged = unstaged = untracked = unmerged = 0
    oid_sha = ""

    fields = [f for f in status_text.split("\0") if f != ""]
    i = 0
    while i < len(fields):
        f = fields[i]
        if f.startswith("# branch.head "):
            head = f[len("# branch.head "):]
            if head == "(detached)":
                detached = True
            else:
                branch = head
        elif f.startswith("# branch.oid "):
            oid = f[len("# branch.oid "):]
            oid_sha = "" if oid == "(initial)" else oid[:7]
        elif f.startswith("# branch.upstream "):
            upstream = f[len("# branch.upstream "):]
        elif f.startswith("# branch.ab "):
            parts = f[len("# branch.ab "):].split()  # ["+A", "-B"]
            if len(parts) == 2:
                ahead = int(parts[0])           # "+3" -> 3 (int() accepts the leading '+')
                behind = -int(parts[1])         # "-2" -> 2
        elif f.startswith("# "):
            pass  # forward-compatible: ignore unknown headers
        elif f.startswith("1 ") or f.startswith("2 "):
            xy = f[2:4]
            if xy[0] != ".":
                staged += 1
            if xy[1] != ".":
                unstaged += 1
            if f.startswith("2 "):
                i += 1  # rename/copy: skip the trailing original-path field
        elif f.startswith("? "):
            untracked += 1
        elif f.startswith("u "):
            unmerged += 1
        i += 1

    dirty = bool(staged or unstaged or untracked or unmerged)
    matches = (not detached) and branch == repo.branch
    return RepoState(
        dir=repo.resolved_dir(), kind=OK, present=True, branch=branch, detached=detached,
        upstream=upstream, ahead=ahead, behind=behind, dirty=dirty,
        staged=staged, unstaged=unstaged, untracked=untracked, unmerged=unmerged,
        config_branch=repo.branch, branch_matches_config=matches,
        last_sha=last_sha or oid_sha, last_subject=last_subject, stash=stash,
    )


def _glyphs(s: RepoState) -> str:
    """Compact per-repo status glyphs (pure; unit-tested)."""
    if s.kind == OWNERSHIP:
        return "⚠owner"
    if s.kind == ERROR or s.error:
        return "✗"
    if not s.present:
        return "(uncloned)"
    parts: list[str] = []
    if s.detached or not s.branch_matches_config:
        parts.append("⚠")
    if s.unmerged:
        parts.append("‼")
    if s.staged or s.unstaged or s.untracked:
        parts.append("~")
    if s.ahead:
        parts.append(f"↑{s.ahead}")
    if s.behind:
        parts.append(f"↓{s.behind}")
    if s.upstream is None and not s.detached:
        parts.append("?")
    return "".join(parts) or "✓"


def summarize(states: list[RepoState]) -> ProjectGitSummary:
    """Pure: list[RepoState] -> compact table-cell line + the detail list.

    The cell is an AGGREGATE glance across all of a project's repos — clean count plus a per-flag
    repo count — NOT a per-repo list (that's the Repos detail panel's job, and it truncated badly for
    projects with many repos). A repo can carry several flags at once (off-branch AND dirty), so the
    counts can overlap. Examples: ``"3 ✓"`` · ``"1 ✓  1 ~  1 ↑"`` · ``"2 ⚠  1 uncloned"`` ·
    ``"✗ 1 error  2 ok"``.
    """
    n = len(states)
    if n == 0:
        return ProjectGitSummary("0 repos", ())
    if any(s.kind in (ERROR, OWNERSHIP) or s.error for s in states):
        bad = sum(1 for s in states if s.kind in (ERROR, OWNERSHIP) or s.error)
        ok = n - bad
        line = f"✗ {bad} error" + (f"  {ok} ok" if ok else "")
        return ProjectGitSummary(line, tuple(states))
    present = [_glyphs(s) for s in states if s.present]  # one classification per repo
    segs: list[str] = []
    clean = sum(1 for g in present if g == "✓")
    if clean:
        segs.append(f"{clean} ✓")
    # Count repos carrying each flag (substring test over the canonical _glyphs alphabet): off-branch
    # /detached ⚠, conflict ‼, dirty ~, ahead ↑, behind ↓, no-upstream ?. Same order as _glyphs.
    for flag in ("⚠", "‼", "~", "↑", "↓", "?"):
        count = sum(1 for g in present if flag in g)
        if count:
            segs.append(f"{count} {flag}")
    uncloned = sum(1 for s in states if not s.present)
    if uncloned:
        segs.append(f"{uncloned} uncloned")
    return ProjectGitSummary("  ".join(segs) or "✓", tuple(states))


def state_label(s: RepoState) -> str:
    """One-word human State for a per-repo CLI/TUI row (pure)."""
    if s.kind == OWNERSHIP:
        return "dubious-ownership"
    if s.kind == ERROR or s.error:
        return "error"
    if not s.present:
        return "uncloned"
    if s.unmerged:
        return f"conflict ({s.unmerged})"
    if s.staged or s.unstaged or s.untracked:
        return f"dirty ({s.staged + s.unstaged + s.untracked})"
    if s.upstream is None and not s.detached:
        return "no upstream"
    return "clean"


def state_style(s: RepoState) -> str:
    """Rich colour for the State cell so clean vs dirty is obvious at a glance (pure — a plain colour
    name, no rich import). green = clean; red = dirty/conflict/error/dubious-ownership (needs
    attention); yellow = advisory (uncloned / no upstream)."""
    if s.kind in (OWNERSHIP, ERROR) or s.error:
        return "red"
    if not s.present:
        return "yellow"
    if s.unmerged or s.staged or s.unstaged or s.untracked:
        return "red"
    if s.upstream is None and not s.detached:
        return "yellow"
    return "green"


_BRANCH_MAX = 40  # cap a displayed branch name; longer names get an ellipsis (counts toward the cap)


def _truncate_branch(name: str, limit: int = _BRANCH_MAX) -> str:
    """Cap a branch name at ``limit`` chars, marking elision with a trailing ``…``. The ellipsis
    counts toward the limit, so the result is never longer than ``limit`` (keeps the Branch column
    bounded against pathologically long branch names)."""
    return name if len(name) <= limit else name[: limit - 1] + "…"


def branch_label(s: RepoState) -> str:
    """Branch cell for a per-repo row: ``main`` / ``feat/x (cfg:main)`` / ``detached@<sha>`` / ``-``.
    Branch names are truncated to ``_BRANCH_MAX`` chars (see ``_truncate_branch``)."""
    if s.detached:
        return f"detached@{s.last_sha or '?'}"
    if not s.branch:
        return "-"
    if not s.branch_matches_config:
        return f"{_truncate_branch(s.branch)} (cfg:{_truncate_branch(s.config_branch)})"
    return _truncate_branch(s.branch)


def ab_label(s: RepoState) -> str:
    """Ahead/behind cell: ``2/1``, or ``—`` when uncloned / no upstream to compare against."""
    if not s.present or s.upstream is None:
        return "—"
    return f"{s.ahead}/{s.behind}"


def ab_style(s: RepoState) -> str:
    """Rich colour for the ↑/↓ cell (pure — a plain colour name, mirrors ``state_style``):
    a non-0/0 repo (unpushed or unpulled commits) shows yellow; 0/0 and the no-upstream ``—``
    recede to dim so the flagged ones pop."""
    return "yellow" if s.present and s.upstream is not None and (s.ahead or s.behind) else "dim"


def commit_label(s: RepoState) -> str:
    """Last-commit cell: ``<short-sha> <subject>`` or ``—``."""
    return f"{s.last_sha} {s.last_subject}".strip() or "—"


def pull_decision(s: RepoState) -> tuple[bool, str]:
    """Pure: is this repo safe to fast-forward, and a one-line reason (eligible?, why/why-not).

    Eligible ONLY when the repo is present, on a branch that tracks an upstream, has a clean working
    tree, and is strictly *behind* with no local commits — the single case ``git merge --ff-only`` can
    satisfy without inventing a merge commit or disturbing operator/agent work. Every other state is a
    SKIP with a reason, never a force: this is the ``RepoState`` analogue of the repos.py invariant
    "never auto-resets — merges are operator-driven". A repo whose checked-out branch differs from the
    registry's configured branch is still eligible (we fast-forward its OWN ``@{upstream}``, consistent
    with how ahead/behind is computed) but the reason names the mismatch so the operator isn't surprised
    which branch advanced. The TUI confirm modal renders these reasons as the per-repo plan.
    """
    if s.kind == OWNERSHIP:
        return False, "dubious ownership (host uid != container uid 1000)"
    if s.kind == ERROR or s.error:
        return False, "git error"
    if not s.present:
        return False, "not cloned"
    if s.unmerged:
        return False, f"unresolved conflict ({s.unmerged})"
    if s.dirty:
        return False, "uncommitted changes — commit/stash first"
    if s.detached:
        return False, "detached HEAD"
    if s.upstream is None:
        return False, "no upstream to pull from"
    if s.ahead and s.behind:
        return False, f"diverged (↑{s.ahead} ↓{s.behind}) — merge/rebase by hand"
    if s.ahead:
        return False, f"↑{s.ahead} local commit(s), nothing to pull"
    if not s.behind:
        return False, "up to date"
    note = "" if s.branch_matches_config else f" ({s.branch}≠cfg:{s.config_branch})"
    return True, f"↓{s.behind} → fast-forward{note}"


def delete_risk(s: RepoState) -> tuple[bool, str]:
    """Pure: would deleting this repo's on-disk checkout lose unsynced work? -> (risky?, reason).

    The destructive-delete analogue of ``pull_decision``: ``project delete`` ``rm -rf``s the workspace,
    so before it runs we flag every repo holding work that isn't safely on a remote. Deliberately errs
    toward RISKY — a false positive only nags (the operator can still delete anyway / keep the
    workspace), a false negative silently destroys work. Ordered most- to least-severe and the reason
    lists every concurrent hazard so the operator sees the full picture, not just the first one:

    * OWNERSHIP / ERROR  -> risky: the tree couldn't be read, so we can't promise it's synced.
    * uncloned           -> safe: nothing on disk to lose.
    * conflict / uncommitted / unpushed-ahead / stash -> risky: live work not on a remote.
    * detached HEAD      -> risky: commits may sit on no branch.
    * no upstream        -> risky: a local-only branch that was never pushed anywhere.
    * otherwise (clean, on an upstream, nothing ahead) -> safe — even when *behind* (a peer pushed;
      our work is all on the remote) or on a non-config branch (it still tracks its own upstream).
    """
    if s.kind == OWNERSHIP:
        return True, "dubious ownership — can't verify it's synced"
    if s.kind == ERROR or s.error:
        return True, "git error — can't verify it's synced"
    if not s.present:
        return False, "not cloned (nothing on disk)"
    bits: list[str] = []
    if s.unmerged:
        bits.append(f"{s.unmerged} unresolved conflict(s)")
    changes = s.staged + s.unstaged + s.untracked
    if changes:
        bits.append(f"{changes} uncommitted change(s)")
    if s.ahead:
        bits.append(f"↑{s.ahead} unpushed commit(s)")
    if s.stash:
        bits.append(f"{s.stash} stash entr" + ("y" if s.stash == 1 else "ies"))
    if s.detached:
        bits.append("detached HEAD")
    elif s.upstream is None:
        bits.append("no upstream (local-only branch)")
    if bits:
        return True, ", ".join(bits)
    return False, "clean (synced)"


def host_uid_matches_container() -> bool:
    """Whether the host operator's uid equals the container's uid (1000).

    On a mismatch, host-side git on a workspace written by the in-container uid-1000 agent trips
    "dubious ownership". The CLI surfaces a one-time advisory; claude-man never auto-writes
    ``safe.directory`` (that guard exists for exactly this surface).

    On macOS the advisory is suppressed: Docker Desktop's file sharing synthesises bind-mount
    ownership, so in-container uid-1000 writes appear host-side as the operator's own files and
    the mismatch is not real (``hostplatform.uid_checks_meaningful``).
    """
    from .. import hostplatform

    if not hostplatform.uid_checks_meaningful():
        return True
    getuid = getattr(os, "getuid", None)
    return getuid is not None and getuid() == config.CONTAINER_UID


# ---------------------------------------------------------------------------
# Thin subprocess shell (needs git; not unit-tested)
# ---------------------------------------------------------------------------
def _run(argv: list[str]) -> tuple[int, str, str]:
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=GIT_TIMEOUT_S)
        return cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"git timed out after {GIT_TIMEOUT_S:.0f}s"
    except FileNotFoundError:
        return 127, "", "git not found on PATH"
    except OSError as exc:  # pragma: no cover - defensive
        return 1, "", str(exc)


def scan_repo(slug: str, repo: Repo) -> RepoState:
    """Map one repo to its live RepoState, host-side (thin shell over the pure parser)."""
    dest = config.workspace_dir(slug) / repo.resolved_dir()
    rdir = repo.resolved_dir()
    if not (dest / ".git").exists():
        return RepoState(dir=rdir, kind=UNCLONED, config_branch=repo.branch)

    rc, out, err = _run(
        ["git", "-C", str(dest), "status", "--porcelain=v2", "--branch", "-z",
         "--untracked-files=normal"]
    )
    if rc != 0:
        return RepoState(
            dir=rdir, kind=classify_error(err), config_branch=repo.branch,
            error=repos_mod.first_line(repos_mod.mask_url_creds(err)) or "git status failed",
        )

    last_sha = last_subject = ""
    lrc, lout, _ = _run(["git", "-C", str(dest), "log", "-1", "--format=%h%x00%s"])
    if lrc == 0 and "\0" in lout:
        last_sha, last_subject = lout.rstrip("\n").split("\0", 1)
    src, sout, _ = _run(["git", "-C", str(dest), "stash", "list"])
    stash = sout.count("\n") if src == 0 else 0

    return parse_porcelain(out, repo=repo, last_sha=last_sha, last_subject=last_subject, stash=stash)


def project_states(project: Project) -> list[RepoState]:
    """Scan every registry repo for ``project`` (iterate the TOML, not the filesystem — invariant 4)."""
    return [scan_repo(project.slug, repo) for repo in project.repos]


def project_summary(project: Project) -> ProjectGitSummary:
    """Scan + summarize in one call. ``summarize``/``_glyphs``/``project_summary`` are the table-cell
    renderers the TUI Repos column consumes in the next phase; ``project sync-repos`` / ``repo list``
    already use ``project_states`` + ``state_label`` today."""
    return summarize(project_states(project))
