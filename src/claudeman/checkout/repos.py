"""Clone / fetch a project's repos host-side into its workspace dir.

Done on the HOST (CLAUDE.md invariant: the gh PAT never enters the container). Because
workspace/ is a bind mount and the container runs --user 1000:1000 (matching the host
uid), in-container edits land on the host with correct ownership. ``fetch_all`` never
auto-resets — merges are operator-driven.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from .. import config
from ..registry.schema import Project


@dataclass(frozen=True)
class RepoResult:
    dir: str
    ok: bool
    detail: str


def clone_all(project: Project) -> list[RepoResult]:
    """Clone each repo that isn't already present. Idempotent."""
    ws = config.workspace_dir(project.slug)
    ws.mkdir(parents=True, exist_ok=True)
    results: list[RepoResult] = []
    for repo in project.repos:
        dest = ws / repo.resolved_dir()
        if (dest / ".git").exists():
            results.append(RepoResult(repo.resolved_dir(), True, "already present"))
            continue
        cp = subprocess.run(
            ["git", "clone", "--branch", repo.branch, repo.url, str(dest)],
            capture_output=True, text=True, check=False,
        )
        results.append(
            RepoResult(repo.resolved_dir(), cp.returncode == 0, (cp.stderr or cp.stdout).strip())
        )
    return results


def fetch_all(project: Project) -> list[RepoResult]:
    """`git fetch` each repo and report ahead/behind. Never resets the working tree."""
    ws = config.workspace_dir(project.slug)
    results: list[RepoResult] = []
    for repo in project.repos:
        dest = ws / repo.resolved_dir()
        if not (dest / ".git").exists():
            results.append(RepoResult(repo.resolved_dir(), False, "not cloned"))
            continue
        subprocess.run(["git", "-C", str(dest), "fetch", "--quiet"], check=False)
        rev = subprocess.run(
            ["git", "-C", str(dest), "rev-list", "--left-right", "--count",
             f"HEAD...origin/{repo.branch}"],
            capture_output=True, text=True, check=False,
        )
        detail = rev.stdout.strip() or rev.stderr.strip()
        if rev.returncode == 0 and detail:
            try:
                ahead, behind = detail.split()
                detail = f"ahead {ahead}, behind {behind}"
            except ValueError:
                pass
        results.append(RepoResult(repo.resolved_dir(), rev.returncode == 0, detail))
    return results
