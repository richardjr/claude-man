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

import os

from . import config

#: The env var name gh reads first (ahead of GITHUB_TOKEN). Injected pass-through, never as argv value.
ENV_NAME = config.GH_TOKEN_ENV


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
    """Write ``token`` to the 0600 state file (mkdir parent 0700 first). Raises on an empty token.

    The file is *created* 0600 via ``os.open`` (no brief world-readable window between write and
    chmod), and a trailing ``chmod`` re-tightens a pre-existing file that ``O_CREAT`` won't re-mode."""
    token = token.strip()
    if not token:
        raise ValueError("gh token must be a non-empty string")
    path = config.gh_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (token + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)  # tighten a pre-existing file (O_CREAT keeps the old mode)


def clear() -> bool:
    """Remove the token file. Returns True if one was present (idempotent — False if already absent)."""
    path = config.gh_token_path()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
