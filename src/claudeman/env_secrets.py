"""Per-project values for `[[project.env_mount]]` entries of ``kind="env"``.

The registry (config.toml-tier) stores only the env var NAMES; the VALUES — which may be secrets —
live here, 0600 in the STATE tier (``config.project_env_path(slug)`` = a per-project ``env.json``):
NEVER in the secret-free config.toml, never synced (``.gitignore`` blocks ``/state/``). They are
injected pass-through (``-e NAME``, value via the child env, never argv) exactly like GH_TOKEN — see
CLAUDE.md invariant 1. Pure stdlib; callers (lifecycle) hold the per-slug flock around set/remove.
"""

from __future__ import annotations

import json

from . import config


def load(slug: str) -> dict[str, str]:
    """All stored {NAME: value} for ``slug`` (empty dict if none / unreadable / malformed)."""
    path = config.project_env_path(slug)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def get(slug: str, name: str) -> str | None:
    return load(slug).get(name)


def _write(slug: str, data: dict[str, str]) -> None:
    """Write the 0600 JSON store (parent 0700, no world-readable window)."""
    config.write_secret_file(
        config.project_env_path(slug), json.dumps(data, indent=2, sort_keys=True),
    )


def set(slug: str, name: str, value: str) -> None:  # noqa: A001 - mirrors dict.set ergonomics
    """Store/overwrite ``name``'s value. Callers serialise via the per-slug flock (lifecycle)."""
    data = load(slug)
    data[name] = value
    _write(slug, data)


def remove(slug: str, name: str) -> bool:
    """Drop ``name`` from the store. Returns True if it was present (idempotent)."""
    data = load(slug)
    if name not in data:
        return False
    del data[name]
    _write(slug, data)
    return True
