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


def default_artifacts() -> list[Artifact]:
    """The user-scope artifact set seeded from ``denylist.SYNC_ARTIFACTS``.

    Memory + CLAUDE.md are project-scoped and resolved per-project at detect time.
    """
    home = user_home()
    out: list[Artifact] = []
    for rel, kind in denylist.SYNC_ARTIFACTS.items():
        if rel == "__mcp__":
            out.append(Artifact("mcp", "mcp", "", str(home / ".claude.json"), "user"))
            continue
        out.append(Artifact(rel, kind, rel, str(home / ".claude" / rel), "user"))
    return out
