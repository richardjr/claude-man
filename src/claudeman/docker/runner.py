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
    "XDG_STATE_HOME": config.CONTAINER_STATE,
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
    file_env: dict[str, str] | None = None,
) -> list[str]:
    """Render the full ``docker create`` argv for a project's hardened container.

    Never includes the token *value* or any scrubbed env key. ``claude_config_path``
    / ``workspace_path`` default to the project's host state dirs. ``file_env`` is the
    already-parsed-and-scrubbed contents of ``project.env_file`` (resolved host-side by
    ``create``); its keys are injected as docker env PASS-THROUGH (``-e KEY`` with no
    value) so secrets never appear in the host argv (``ps aux``).
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
    # env_file vars: injected as pass-through (-e KEY, value supplied via the subprocess
    # env in create()) so secrets stay out of argv. The scrub is enforced here too, so a
    # forbidden key can never be rendered even if a caller hands us an un-scrubbed dict.
    # NOTE: docker is NOT given --env-file directly — that path bypassed the ANTHROPIC_*
    # scrub and could silently outrank the OAuth token (review SEC-2).
    for key in file_env or {}:
        if key in config.SCRUBBED_ENV_KEYS or key == OAUTH_TOKEN_ENV:
            continue
        argv += ["-e", key]

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


def read_env_file(path: str) -> dict[str, str]:
    """Parse a ``KEY=VAL`` env file host-side, dropping scrubbed/auth keys.

    Blank lines and ``#`` comments are ignored; a leading ``export`` is stripped; surrounding
    single/double quotes on the value are removed. ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN``
    / ``CLAUDE_CODE_OAUTH_TOKEN`` are never returned (review SEC-2), so they can neither reach the
    container nor outrank the injected OAuth token.
    """
    out: dict[str, str] = {}
    with open(os.path.expanduser(path), encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key or key in config.SCRUBBED_ENV_KEYS or key == OAUTH_TOKEN_ENV:
                continue
            out[key] = value
    return out


def exists(slug: str) -> bool:
    cp = _run(["docker", "container", "inspect", config.container_name(slug)])
    return cp.returncode == 0


def create(
    project: Project,
    *,
    profile_name: str,
    token: str | None = None,
    version: str = config.DEFAULT_CLAUDE_VERSION,
    created_iso: str,
) -> subprocess.CompletedProcess:
    """Create the container, passing the token + env_file values through the subprocess env.

    Secrets (the OAuth token and any ``env_file`` values) are supplied via the child process
    environment and rendered into argv only as pass-through names, so they never appear in
    ``ps aux``. ``env_file`` is parsed + scrubbed host-side (review SEC-2). ``token`` may be
    ``None`` (e.g. before any profile token is minted) — the container still builds and a shell
    works, but in-container ``claude`` won't authenticate.
    """
    file_env = read_env_file(project.env_file) if project.env_file else {}
    argv = build_create_argv(
        project, profile_name=profile_name, version=version, created_iso=created_iso,
        file_env=file_env, inject_token=bool(token),
    )
    env = dict(os.environ)
    for key in config.SCRUBBED_ENV_KEYS:
        env.pop(key, None)
    if token:
        env[OAUTH_TOKEN_ENV] = token
    env.update(file_env)
    return _run(argv, env=env)


def start(slug: str) -> subprocess.CompletedProcess:
    return _run(["docker", "start", config.container_name(slug)])


def stop(slug: str) -> subprocess.CompletedProcess:
    return _run(["docker", "stop", config.container_name(slug)])


def remove(slug: str) -> subprocess.CompletedProcess:
    return _run(["docker", "rm", "-f", config.container_name(slug)])
