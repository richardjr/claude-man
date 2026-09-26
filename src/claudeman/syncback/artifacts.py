"""Declarative registry of the artifacts sync-back considers, and their host targets.

Pure data + small helpers. The detect/diff/merge layers (Phase 5) consume this. Host
targets distinguish the user scope (``~/.claude/...``), the project scope
(``~/Work/.claude/...`` and a repo-root ``.mcp.json``), and the setups mirror.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import denylist


@dataclass(frozen=True)
class Artifact:
    name: str
    kind: str                 # tree | tree-symlink | json-keys | mcp | file
    container_rel: str        # path relative to the container CLAUDE_CONFIG_DIR
    host_target: str          # absolute host path or a special marker
    scope: str                # user | project | repo

    @property
    def default_decision(self) -> str:
        """Authored text defaults to accept; config/MCP defaults to reject."""
        if self.kind in ("json-keys", "mcp"):
            return "reject"
        return "accept"


def user_home() -> Path:
    return Path.home()


def policy_for(slug: str):
    """The ``agents.SyncbackPolicy`` for ``slug``'s provider (registry-read), or None when the
    provider has no sync-back (the lifecycle gates every entry point on that; here it is a
    defence-in-depth: no policy → no artifacts → nothing is ever read)."""
    from ..registry import projects as projects_registry
    try:
        return projects_registry.load(slug).provider.syncback
    except (FileNotFoundError, ValueError, OSError):
        return None


def default_artifacts(policy=None) -> list[Artifact]:
    """The user-scope artifact set for ``policy`` (default: claude's ``denylist.SYNC_ARTIFACTS``,
    targeting ``~/.claude``). Host targets are the policy's ``host_dir`` (``~``-expanded).

    Memory + the context file are project-scoped and resolved per-project at detect time.
    """
    home = user_home()
    if policy is None:
        pairs = tuple(denylist.SYNC_ARTIFACTS.items())
        host_dir, mcp_host = home / ".claude", home / ".claude.json"
    else:
        pairs = policy.artifacts
        host_dir = home / policy.host_dir[2:]
        mcp_host = home / policy.mcp_host_file[2:] if policy.mcp_host_file else None
    out: list[Artifact] = []
    for rel, kind in pairs:
        if rel == "__mcp__" or kind == "mcp":
            if mcp_host is not None:
                out.append(Artifact("mcp", "mcp", "", str(mcp_host), "user"))
            continue
        out.append(Artifact(rel, kind, rel, str(host_dir / rel), "user"))
    return out
