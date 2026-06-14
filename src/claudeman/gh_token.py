"""The optional GitHub token injected as ``GH_TOKEN`` so in-container ``gh`` works.

A SECRET, so it lives in the state tier at ``config.gh_token_path()`` (``0600``) — NEVER in the
secret-free ``config.toml`` and never synced (the ``.gitignore`` blocks ``/state/``). It is injected
pass-through (``-e GH_TOKEN`` name-only in argv, value via the child env) exactly like the Claude OAuth
token (see ``docker/runner.py``), and ONLY when the operator has configured one — absent a token,
nothing is injected (CLAUDE.md invariant 1).

Global by design (one token for all projects, like the git identity); a per-profile/per-project
override is a possible later refinement. Pure stdlib — importable by the CLI/lifecycle without textual.
"""

from __future__ import annotations

from . import config


def load() -> str | None:
    """The configured GitHub token, or ``None`` if unset/empty/unreadable."""
    path = config.gh_token_path()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    return token or None


def is_set() -> bool:
    return load() is not None


def save(token: str) -> None:
    """Write ``token`` to the 0600 state file (parent 0700, no world-readable window). Raises on empty."""
    token = token.strip()
    if not token:
        raise ValueError("gh token must be a non-empty string")
    config.write_secret_file(config.gh_token_path(), token + "\n")


def clear() -> bool:
    """Remove the token file. Returns True if one was present (idempotent — False if already absent)."""
    path = config.gh_token_path()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
