"""Agent providers (Phase 7a — docs/AGENTS.md): ``resolve(id)`` is the ONE way to reach a
provider's policy data. Today the registry holds the ``claude`` provider only; ``Project.agent``
(7b) selects per project, ``codex`` (7c) is the second entry.
"""

from __future__ import annotations

from .base import AgentProvider, AuthSpec, ImageSpec, UpdateSpec
from .claude import PROVIDER as CLAUDE

DEFAULT_ID = CLAUDE.id

PROVIDERS: dict[str, AgentProvider] = {CLAUDE.id: CLAUDE}

DEFAULT: AgentProvider = CLAUDE


def resolve(agent_id: str = DEFAULT_ID) -> AgentProvider:
    """The provider for ``agent_id`` (default: claude). Raises ``KeyError`` for an unknown id — the
    registry validates ``Project.agent`` at load (7b), so this is a programming error, not input."""
    try:
        return PROVIDERS[agent_id]
    except KeyError:
        raise KeyError(f"unknown agent provider {agent_id!r} (known: {', '.join(PROVIDERS)})") from None


def ids() -> tuple[str, ...]:
    return tuple(PROVIDERS)


def binaries() -> frozenset[str]:
    """Every provider's in-container program name — 'is this program an agent?' checks."""
    return frozenset(p.binary for p in PROVIDERS.values())


def config_dirs() -> tuple[str, ...]:
    """Every provider's in-container config dir — the mount-dst denylist covers ALL of them, so a
    file env-mount can never smuggle a credential into any agent's config dir (invariant 1)."""
    return tuple(p.config_dir for p in PROVIDERS.values())


def config_dir_envs() -> frozenset[str]:
    return frozenset(p.config_dir_env for p in PROVIDERS.values())


__all__ = ["AgentProvider", "AuthSpec", "ImageSpec", "UpdateSpec", "CLAUDE", "DEFAULT",
           "DEFAULT_ID", "PROVIDERS", "resolve", "ids", "binaries", "config_dirs", "config_dir_envs"]
