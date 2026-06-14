"""Clone / fetch a project's repos host-side into its workspace dir.

Done on the HOST (CLAUDE.md invariant: the gh PAT / ssh-agent never enters the container).
Because workspace/ is a bind mount and the container runs --user 1000:1000 (matching the host
uid), in-container edits land on the host with correct ownership. ``fetch_all`` never
auto-resets — merges are operator-driven. Ahead/behind is no longer computed here (it moved to
``checkout/gitstate.py``, which parses it from a single ``git status --porcelain=v2`` header);
``fetch_all`` only performs the network fetch and reports a concise ok/fail per repo (review BUG-6).

Every git ``stderr``/``stdout`` surfaced in a ``RepoResult.detail`` is run through ``mask_url_creds``
first: an auth failure echoes the remote URL, which may embed ``https://user:token@host`` credentials.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import config
from ..registry.schema import Project, Repo

# Userinfo (``user``/``user:password``/``x-access-token:ghp_…``) in a ``scheme://userinfo@host`` URL.
# scp-style ``git@github.com:org/repo`` has no ``://`` and no secret, so it is intentionally untouched.
_CREDS_RE = re.compile(r"([a-zA-Z][\w+.\-]*://)[^/@\s]+@")

# A *shape* check only (no network probe): scp-style ``user@host:path`` OR any ``scheme://…``.
# The real reachability test is the clone — this just stops obvious garbage at the modal boundary.
_REPO_URL_RE = re.compile(r"^(?:[\w.\-]+@[\w.\-]+:[\w./\-~]+|[a-zA-Z][\w+.\-]*://\S+)$")


# git is run non-interactively so a missing credential FAILS FAST instead of blocking on a terminal
# prompt (which would hang the TUI/CLI), and every call is time-bounded so a wedged network can't
# hang us — mirroring docker/runner._run's timeout-to-124 contract.
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
_GIT_NET_TIMEOUT_S = 120    # clone / fetch (network-bound)
_GIT_LOCAL_TIMEOUT_S = 30   # ff-merge (local, no network)


def _run_git(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """Run a git command non-interactively + time-bounded; map a timeout to a 124 result."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False,
                              env=_GIT_ENV, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", f"timed out after {timeout:.0f}s")


def mask_url_creds(text: str) -> str:
    """Redact ``scheme://USERINFO@`` credentials from any git output before it is surfaced."""
    return _CREDS_RE.sub(r"\1***@", text or "")


def looks_like_repo_url(url: str) -> bool:
    """True if ``url`` has a plausible git-remote shape (scp-style or ``scheme://…``). Shape only."""
    return bool(_REPO_URL_RE.match((url or "").strip()))


def is_within(child: Path, root: Path) -> bool:
    """True iff ``child`` resolves to a path strictly inside ``root`` (containment guard)."""
    try:
        rc, rr = child.resolve(), root.resolve()
    except OSError:
        return False
    return rc != rr and rr in rc.parents


@dataclass(frozen=True)
class RepoResult:
    dir: str
    ok: bool
    detail: str


def first_line(text: str) -> str:
    """First non-blank line of ``text``, stripped (shared by ``fetch_all`` + ``gitstate.scan_repo``)."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[0].strip() if lines else ""


def clone_one(slug: str, repo: Repo) -> RepoResult:
    """Clone one repo into ``workspace_dir(slug)/<resolved_dir>``. Idempotent (skips an existing .git).

    Defence-in-depth: even though ``Repo.__post_init__`` already rejects an escaping ``dir``, the
    resolved destination is re-checked against the workspace root before any ``git clone`` runs, so a
    clone can never write outside ``workspace/``.
    """
    ws = config.workspace_dir(slug)
    ws.mkdir(parents=True, exist_ok=True)
    dest = ws / repo.resolved_dir()
    if not is_within(dest, ws):
        return RepoResult(repo.resolved_dir(), False, "refusing to clone: dir escapes workspace/")
    if (dest / ".git").exists():
        return RepoResult(repo.resolved_dir(), True, "already present")
    cp = _run_git(
        ["git", "clone", "--branch", repo.branch, repo.url, str(dest)],
        timeout=_GIT_NET_TIMEOUT_S,
    )
    detail = mask_url_creds(cp.stderr or cp.stdout).strip()
    return RepoResult(repo.resolved_dir(), cp.returncode == 0, detail)


def clone_all(project: Project) -> list[RepoResult]:
    """Clone each repo that isn't already present. Idempotent."""
    return [clone_one(project.slug, repo) for repo in project.repos]


def fetch_all(project: Project) -> list[RepoResult]:
    """``git fetch`` each repo and report a concise ok/fail. Never resets the working tree.

    Ahead/behind reporting now lives in ``gitstate`` (it reads ``# branch.ab`` against the repo's
    actual ``@{upstream}``); this only performs the network fetch so a missing upstream can no longer
    surface a raw multi-line ``git fatal`` (review BUG-6).
    """
    ws = config.workspace_dir(project.slug)
    results: list[RepoResult] = []
    for repo in project.repos:
        dest = ws / repo.resolved_dir()
        if not (dest / ".git").exists():
            results.append(RepoResult(repo.resolved_dir(), False, "not cloned"))
            continue
        cp = _run_git(["git", "-C", str(dest), "fetch", "--quiet"], timeout=_GIT_NET_TIMEOUT_S)
        if cp.returncode == 0:
            results.append(RepoResult(repo.resolved_dir(), True, "fetched"))
        else:
            detail = first_line(mask_url_creds(cp.stderr or cp.stdout)) or "fetch failed"
            results.append(RepoResult(repo.resolved_dir(), False, detail))
    return results


def ff_merge_one(slug: str, repo: Repo) -> RepoResult:
    """LOCAL fast-forward-only merge of one repo against its already-fetched ``@{upstream}``.

    No network, no reset, no merge commit: ``git merge --ff-only @{upstream}`` advances the branch
    pointer ONLY when the upstream is a strict descendant, and git itself exits non-zero otherwise —
    including if the working tree went dirty since the caller's eligibility scan (the TOCTOU backstop
    for a live in-container ``claude`` that wrote a file between the plan and the apply). The network
    ``git fetch`` is the caller's responsibility (``lifecycle.pull_plan`` -> ``fetch_all``), so this
    stays a fast local op. ``@{upstream}`` is passed as literal argv (no shell), so git resolves it
    against the checked-out branch's real tracking ref — the same ref ``gitstate`` reads ahead/behind
    from. Output is ``mask_url_creds``'d defensively (a stale-ref error can echo the remote URL).
    """
    ws = config.workspace_dir(slug)
    dest = ws / repo.resolved_dir()
    rdir = repo.resolved_dir()
    if not is_within(dest, ws):  # defence-in-depth (dir is already containment-validated at add time)
        return RepoResult(rdir, False, "refusing to pull: dir escapes workspace/")
    if not (dest / ".git").exists():
        return RepoResult(rdir, False, "not cloned")
    cp = _run_git(
        ["git", "-C", str(dest), "merge", "--ff-only", "@{upstream}"],
        timeout=_GIT_LOCAL_TIMEOUT_S,
    )
    detail = first_line(mask_url_creds(cp.stderr or cp.stdout))
    if cp.returncode == 0:
        return RepoResult(rdir, True, detail or "fast-forwarded")
    return RepoResult(rdir, False, detail or "fast-forward failed")
