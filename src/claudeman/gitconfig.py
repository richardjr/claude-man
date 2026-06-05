"""Resolve the git author identity to inject into a container, rendered as ``GIT_CONFIG_*`` env.

The container rootfs is ``--read-only``, so ``git config --global`` can't write
``/home/agent/.gitconfig`` and ``git commit`` fails with *Author identity unknown*. claude-man injects
the identity via git's ENV-config (``GIT_CONFIG_COUNT`` + ``GIT_CONFIG_KEY_n`` / ``GIT_CONFIG_VALUE_n``
— equivalent to ``git -c user.name=…``), which needs NO writable file. ``GIT_CONFIG_GLOBAL`` is pointed
at a writable tmpfs path (see ``runner._BAKED_ENV``) so ``git config --global`` works for anything else.

Identity precedence: the claude-man global settings (``config.toml`` ``[git]``) override; else inherit
the host operator's own ``git config --global user.{name,email}``. name/email are NOT secrets, so they
are rendered as plain ``-e KEY=value``.

``env_for`` is pure (unit-tested); ``resolve_identity`` reads settings + shells out to host git.
"""

from __future__ import annotations

import subprocess

from .registry import settings as settings_registry


def _host_git(key: str) -> str:
    """Read one ``git config --global`` value from the HOST, or "" (missing/no git/timeout)."""
    try:
        cp = subprocess.run(
            ["git", "config", "--global", key],
            capture_output=True, text=True, check=False, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return cp.stdout.strip() if cp.returncode == 0 else ""


def resolve_identity() -> tuple[str, str]:
    """``(name, email)``: claude-man settings if set, else inherited from the host git config, else ""."""
    s = settings_registry.load()
    name = s.git_user_name.strip() or _host_git("user.name")
    email = s.git_user_email.strip() or _host_git("user.email")
    return name, email


def env_for(name: str, email: str) -> dict[str, str]:
    """Pure: render an identity as git env-config (``GIT_CONFIG_COUNT`` + ``KEY_n``/``VALUE_n``).

    Only the set fields are emitted; an empty identity returns ``{}`` (the writable ``GIT_CONFIG_GLOBAL``
    is baked separately, so ``git config --global`` still works even with no inherited identity).
    """
    pairs = [(k, v) for k, v in (("user.name", name.strip()), ("user.email", email.strip())) if v]
    if not pairs:
        return {}
    env = {"GIT_CONFIG_COUNT": str(len(pairs))}
    for i, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


def container_env() -> dict[str, str]:
    """The resolved identity rendered for ``docker create`` (``{}`` when no identity is configured)."""
    return env_for(*resolve_identity())
