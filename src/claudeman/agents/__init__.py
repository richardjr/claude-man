"""Agent providers (Phase 7a — docs/AGENTS.md): ``resolve(id)`` is the ONE way to reach a
provider's policy data. Registry: ``claude`` (the reference, baked into the base image) and
``codex`` (7c — installed via the tool registry); ``Project.agent`` selects per project.
"""

from __future__ import annotations

from .. import config
from .base import PERMISSIONS, AgentEvent, AgentProvider, AuthSpec, ImageSpec, RunRequest, RunSpec, UpdateSpec
from .claude import PROVIDER as CLAUDE
from .codex import PROVIDER as CODEX

DEFAULT_ID = CLAUDE.id

PROVIDERS: dict[str, AgentProvider] = {CLAUDE.id: CLAUDE, CODEX.id: CODEX}

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


def credential_env_names() -> frozenset[str]:
    """EVERY provider's credential env names (its token env + its mis-bill scrub set) — invariant 9:
    the layer injects exactly ONE credential (the chosen profile's, for the chosen provider) and
    scrubs every provider's credential names from operator-supplied env, so a codex key can never
    ride into a claude container (or vice versa) via ``project.env`` / ``env_file`` / an env-mount."""
    names: set[str] = set()
    for p in PROVIDERS.values():
        names.add(p.auth.token_env)
        names.update(p.auth.scrub_env)
    return frozenset(names)


def is_forbidden_env_name(name: str) -> bool:
    """``config.is_forbidden_env_name`` (GH_TOKEN + the claude names) extended over every registered
    provider's credential names — same case/underscore-padding normalisation."""
    if config.is_forbidden_env_name(name):
        return True
    norm = name.strip("_").upper()
    return any(norm == f.strip("_").upper() for f in credential_env_names())


__all__ = ["AgentProvider", "AuthSpec", "ImageSpec", "UpdateSpec", "RunSpec", "RunRequest",
           "AgentEvent", "PERMISSIONS", "CLAUDE", "CODEX", "DEFAULT",
           "DEFAULT_ID", "PROVIDERS", "resolve", "ids", "binaries", "config_dirs", "config_dir_envs",
           "credential_env_names", "is_forbidden_env_name"]
