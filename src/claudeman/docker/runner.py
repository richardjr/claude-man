"""Render and execute the hardened ``docker create`` for a project container.

``build_create_argv`` is a PURE function (no daemon, no filesystem) so the exact
hardened flag set can be unit-tested. The thin ``create``/``start``/``stop``/``rm``
wrappers shell out to ``docker`` via ``subprocess`` with explicit argv (never
``shell=True``).

Security notes (see CLAUDE.md invariants 1 & 2):
  * The OAuth token is injected via docker's env PASS-THROUGH form (``-e NAME``
    with no ``=value``) so the secret value is read from the ``docker`` process's
    environment and never appears in the host process argv (``ps aux``).
  * ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` are never rendered.
  * No ``.credentials.json`` is mounted; auth is the env token only.
"""

from __future__ import annotations

import os
import subprocess

from .. import config
from ..registry.schema import Project
from . import labels

OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

# The fixed hardened flags (CLAUDE.md invariant 2 — the floor, not a suggestion).
_HARDENING = [
    "--read-only",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--user", f"{config.CONTAINER_UID}:{config.CONTAINER_GID}",
    "--pids-limit", "1024",
    "--tmpfs", "/tmp:rw,exec,nosuid,size=512m",
    "--tmpfs", f"{config.CONTAINER_CACHE}:rw,exec,nosuid,size=256m",
]

# Baked-but-also-explicit env (matches images/base/Dockerfile).
_BAKED_ENV = {
    "HOME": config.CONTAINER_HOME,
    "CLAUDE_CONFIG_DIR": config.CONTAINER_CLAUDE_CONFIG,
    "XDG_CACHE_HOME": config.CONTAINER_CACHE,
    "USE_BUILTIN_RIPGREP": "0",
    "DISABLE_AUTOUPDATER": "1",
}


def build_create_argv(
    project: Project,
    *,
    profile_name: str,
    version: str = config.DEFAULT_CLAUDE_VERSION,
    created_iso: str,
    claude_config_path: str | None = None,
    workspace_path: str | None = None,
    inject_token: bool = True,
) -> list[str]:
    """Render the full ``docker create`` argv for a project's hardened container.

    Never includes the token *value* or any scrubbed env key. ``claude_config_path``
    / ``workspace_path`` default to the project's host state dirs.
    """
    cfg_path = claude_config_path or str(config.claude_config_dir(project.slug))
    ws_path = workspace_path or str(config.workspace_dir(project.slug))

    argv: list[str] = ["docker", "create", "--name", project.container]
    argv += labels.to_args(
        labels.build(project, profile=profile_name, version=version, created_iso=created_iso)
    )
    argv += _HARDENING

    # Baked env (explicit for clarity even though the image bakes it).
    for key, value in _BAKED_ENV.items():
        argv += ["-e", f"{key}={value}"]

    # OAuth token: pass-through form, value stays out of argv.
    if inject_token:
        argv += ["-e", OAUTH_TOKEN_ENV]

    # Project env vars (declared config), with scrubbed keys removed defensively.
    for key, value in project.env.items():
        if key in config.SCRUBBED_ENV_KEYS or key == OAUTH_TOKEN_ENV:
            continue
        argv += ["-e", f"{key}={value}"]
    if project.env_file:
        argv += ["--env-file", os.path.expanduser(project.env_file)]

    # Writable persistent binds + read-only rootfs everywhere else.
    argv += ["-v", f"{cfg_path}:{config.CONTAINER_CLAUDE_CONFIG}"]
    argv += ["-v", f"{ws_path}:{config.CONTAINER_WORKSPACE}"]
    argv += ["-w", config.CONTAINER_WORKSPACE]

    argv += [project.image, "sleep", "infinity"]
    return argv


# ---------------------------------------------------------------------------
# Thin execution wrappers (need a docker daemon; not unit-tested)
# ---------------------------------------------------------------------------
def _run(argv: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, env=env, capture_output=True, text=True, check=False)


def exists(slug: str) -> bool:
    cp = _run(["docker", "container", "inspect", config.container_name(slug)])
    return cp.returncode == 0


def create(
    project: Project,
    *,
    profile_name: str,
    token: str,
    version: str = config.DEFAULT_CLAUDE_VERSION,
    created_iso: str,
) -> subprocess.CompletedProcess:
    """Create the container, passing the token through the subprocess env (not argv)."""
    argv = build_create_argv(
        project, profile_name=profile_name, version=version, created_iso=created_iso
    )
    env = dict(os.environ)
    for key in config.SCRUBBED_ENV_KEYS:
        env.pop(key, None)
    env[OAUTH_TOKEN_ENV] = token
    return _run(argv, env=env)


def start(slug: str) -> subprocess.CompletedProcess:
    return _run(["docker", "start", config.container_name(slug)])


def stop(slug: str) -> subprocess.CompletedProcess:
    return _run(["docker", "stop", config.container_name(slug)])


def remove(slug: str) -> subprocess.CompletedProcess:
    return _run(["docker", "rm", "-f", config.container_name(slug)])
