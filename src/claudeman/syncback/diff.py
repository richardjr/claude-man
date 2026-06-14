"""Human-readable, secret-masked diffs for the review gate (Phase 5).

``difflib.unified_diff`` for file/memory/CLAUDE.md artifacts; canonical key-path diff
for settings.json and the MCP block. Every line passes through ``denylist.mask_line``
before it reaches the diff buffer.
"""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path

from .. import config
from ..checkout.repos import is_within
from . import artifacts, baseline, denylist


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


def json_key_diff(before: dict, after: dict, *, path: str = "settings.json") -> list[str]:
    """A secret-masked unified diff of two JSON objects, denied keys dropped FIRST (SYNC-5).

    Top-level ``is_denied_json_key`` keys (identity / ``last*`` / ``cached*`` / telemetry) are removed
    from BOTH sides before rendering, so they can never reach the gate even masked. The remainder is
    canonicalised (sorted keys, 2-space indent) and run through the SAME masked unified-diff machinery
    as ``file_diff`` — so a token-shaped value anywhere in the tree is still redacted line-by-line by
    ``denylist.mask_line`` (incl. the BUG-4 value-shape scan)."""
    b = _canonical(_drop_denied(before))
    a = _canonical(_drop_denied(after))
    return file_diff(b, a, path=path)


def _drop_denied(obj: dict) -> dict:
    """Top-level denied-key drop (mirrors ``profiles.seed._patch_settings`` — top-level only)."""
    if not isinstance(obj, dict):
        return {}
    return {k: v for k, v in obj.items() if not denylist.is_denied_json_key(k)}


def _canonical(obj: dict) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# change_diff — the per-Change masked diff the review gate renders
# ---------------------------------------------------------------------------
def change_diff(slug: str, change: object) -> list[str]:
    """A secret-masked diff for one detected change: ``a/`` = the current HOST value, ``b/`` = the
    current CONTAINER value — i.e. "what acceptance would write". ``change`` is duck-typed (it has
    ``.artifact``/``.kind``/``.rel``) so this module never imports ``detect`` (no cycle). The unit is
    re-read fresh; the actual merge re-reads + re-guards independently, so the tiny read/apply window
    is harmless (this is display only)."""
    kind = getattr(change, "kind", "")
    rel = getattr(change, "rel", "")
    art = _artifact(getattr(change, "artifact", ""))
    if art is None:
        return []
    # Defence-in-depth: detect only ever produces clean, denylist-filtered rels, but re-assert here so
    # a future caller that hand-builds a Change can't make change_diff read a denied/escaping path.
    if rel and any(denylist.is_denied_path(seg) for seg in rel.split("/")):
        return []
    if kind in ("tree", "tree-symlink", "file"):
        host_root = Path(art.host_target)
        host = host_root / rel if rel else host_root
        if rel and not is_within(host, host_root):
            return []
        cont = config.claude_config_dir(slug) / art.container_rel / rel
        return file_diff(_read_text(host), _read_text(cont), path=f"{art.name}/{rel}" if rel else art.name)
    if kind == "json-keys":
        host = _pick(_read_json(Path(art.host_target)), rel)
        cont = _pick(_read_json(config.claude_config_dir(slug) / "settings.json"), rel)
        return json_key_diff(host, cont, path=f"settings.json:{rel}")
    if kind == "mcp":
        host = _pick(baseline.read_mcp_servers(Path(art.host_target)), rel)
        cont = _pick(baseline.read_mcp_servers(config.claude_config_dir(slug) / ".claude.json"), rel)
        return json_key_diff(host, cont, path=f"mcp:{rel}")
    return []


def _artifact(name: str) -> artifacts.Artifact | None:
    return next((a for a in artifacts.default_artifacts() if a.name == name), None)


def _pick(obj: dict, key: str) -> dict:
    """The single changed key/server, so the diff stays focused on the unit under review."""
    return {key: obj[key]} if isinstance(obj, dict) and key in obj else {}


def _read_text(path: Path) -> str:
    """File text for the diff; a symlink renders as its target string (never dereferenced)."""
    try:
        if path.is_symlink():
            return f"symlink -> {os.readlink(path)}"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return ""


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
