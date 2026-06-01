"""Human-readable, secret-masked diffs for the review gate (Phase 5).

``difflib.unified_diff`` for file/memory/CLAUDE.md artifacts; canonical key-path diff
for settings.json and the MCP block. Every line passes through ``denylist.mask_line``
before it reaches the diff buffer.
"""

from __future__ import annotations

import difflib

from . import denylist


def file_diff(before: str, after: str, *, path: str) -> list[str]:
    """A unified diff with every line secret-masked. Safe to render in the TUI."""
    raw = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return [denylist.mask_line(line) for line in raw]


def json_key_diff(before: dict, after: dict) -> list[str]:
    raise NotImplementedError("phase 5: canonical JSON key diff — see ROADMAP.md")
