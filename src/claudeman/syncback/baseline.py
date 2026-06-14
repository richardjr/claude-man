"""Three-way baseline manifest (Phase 5).

At a project's first start we snapshot BOTH the seeded container config bind AND the operator's real
host ``~/.claude`` targets, so the session-close detect step can tell container-side drift (the agent
edited a skill / settings.json) from host-side drift (the operator changed their global config in the
meantime) and force a genuine three-way conflict to manual review.

The manifest is a fingerprint, never the content: file trees → per-file ``sha256:`` (or a stable
``symlink:<target>`` / ``symlink-blocked`` marker), settings.json → per-top-level-key canonical hash,
MCP → per-server canonical hash. Only sha256 hashes and (non-secret) symlink target strings are
stored, so the baseline itself can never leak a secret. Written to ``config.baseline_path(slug)`` — a
sibling of the mount, never inside it.

Invariant 5 holds on every READ here: the tree walk applies the basename denylist + the
``fsmerge.check_symlink`` escape guard per nested entry, settings.json drops ``is_denied_json_key``
keys, and the ONLY path that opens the (denied) ``.claude.json`` is ``_read_mcp_servers`` — a narrow
extractor that returns *only* ``mcpServers`` and discards the rest. ``.claude.json`` is NEVER routed
through the tree walk.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path

from .. import config
from . import artifacts, denylist, fsmerge

MANIFEST_VERSION = 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def write_baseline(slug: str) -> None:
    """Snapshot the container bind + the real host targets to ``config.baseline_path(slug)``."""
    _write_manifest(slug, build_manifest(slug))


def write_manifest(slug: str, container: dict, host: dict) -> None:
    """Persist a manifest from ALREADY-computed snapshots.

    Used by ``detect``'s no-baseline path to store the IMPLICIT reference (what claude-man itself
    wrote) rather than the current agent-edited state — so a follow-up apply re-detects the SAME
    changes instead of finding them silently absorbed into a current-state baseline."""
    _write_manifest(slug, {
        "version": MANIFEST_VERSION, "slug": slug, "created": _now_iso(),
        "container": container, "host": host,
    })


def _write_manifest(slug: str, manifest: dict) -> None:
    path = config.baseline_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_baseline(slug: str) -> dict:
    """The stored manifest, or ``{}`` when absent/unreadable (graceful no-baseline degradation)."""
    path = config.baseline_path(slug)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def build_manifest(slug: str) -> dict:
    """Build (without writing) the three-way manifest for ``slug``."""
    return {
        "version": MANIFEST_VERSION,
        "slug": slug,
        "created": _now_iso(),
        "container": snapshot_container(slug),
        "host": snapshot_host(),
    }


def snapshot_container(slug: str) -> dict:
    """Fingerprint each allowlisted artifact in the project's container config bind."""
    root = config.claude_config_dir(slug)
    return {art.name: _entry_for(art, _container_path(art, root)) for art in artifacts.default_artifacts()}


def snapshot_host() -> dict:
    """Fingerprint each allowlisted artifact in the operator's real host ``~/.claude``."""
    return {art.name: _entry_for(art, Path(art.host_target)) for art in artifacts.default_artifacts()}


def snapshot_tree(root: Path, kind: str = "tree") -> dict:
    """Public: fingerprint an arbitrary tree the same way a tree artifact is fingerprinted.

    Used by ``detect`` to build the no-baseline implicit reference from the asset/pack SOURCE dir
    (so genuinely agent-authored files show as adds while claude-man's own synced files don't)."""
    return _tree_entry(root, kind)


def read_mcp_servers(path: Path) -> dict:
    """The DELIBERATE denylist exception: open ``.claude.json``, return ONLY ``mcpServers``.

    ``.claude.json`` is denied for the tree walk (it carries identity + per-project state). This narrow
    reader loads it, lifts out the ``mcpServers`` object, and discards everything else — so MCP can be
    detected/diffed without ever routing the whole file through a copy. The sole caller of this on the
    host/container ``.claude.json``; nothing else opens that file for sync-back."""
    data = _load_json(path)
    if isinstance(data, dict):
        servers = data.get("mcpServers")
        if isinstance(servers, dict):
            return servers
    return {}


# ---------------------------------------------------------------------------
# Per-kind entry builders
# ---------------------------------------------------------------------------
def _entry_for(art: artifacts.Artifact, path: Path) -> dict:
    if art.kind in ("tree", "tree-symlink"):
        return _tree_entry(path, art.kind)
    if art.kind == "json-keys":
        return _json_keys_entry(path)
    if art.kind == "mcp":
        return _mcp_entry(path)
    if art.kind == "file":
        return _file_entry(path)
    return {"kind": art.kind}


def _container_path(art: artifacts.Artifact, container_root: Path) -> Path:
    if art.kind == "mcp":
        return container_root / ".claude.json"
    return container_root / art.container_rel


def _tree_entry(root: Path, kind: str) -> dict:
    """``{kind, files:{relpath: 'sha256:…' | 'symlink:…' | 'symlink-blocked'}}`` — denylist + symlink
    escape guard applied per nested entry (SYNC-3), so a denied basename or an escaping/denied-target
    symlink is never fingerprinted by content (it's skipped or recorded as a stable blocked marker)."""
    files: dict[str, str] = {}
    if root.is_dir() and not root.is_symlink():
        _walk_tree(root, root, files)
    return {"kind": kind, "files": files}


def _walk_tree(base: Path, current: Path, out: dict[str, str]) -> None:
    for entry in sorted(current.iterdir()):
        name = entry.name
        if name in fsmerge.TREE_EXCLUDE_NAMES or denylist.is_denied_path(name):
            continue
        rel = entry.relative_to(base).as_posix()
        if entry.is_symlink():
            note = fsmerge.check_symlink(entry, rel, src_root=base, gate=True)
            out[rel] = "symlink-blocked" if note else "symlink:" + os.readlink(entry)
            continue  # never recurse/deref a symlink (escape + cycle safety)
        if entry.is_dir():
            _walk_tree(base, entry, out)
        elif entry.is_file():
            out[rel] = "sha256:" + _sha256(entry)


def _json_keys_entry(path: Path) -> dict:
    """``{kind:'json-keys', keys:{topkey: canonical-hash}}`` — ``is_denied_json_key`` AND the
    structurally-immune ``hooks``/``statusLine`` keys excluded, so they never even become a detectable
    change (the merge refuses them anyway; keeping them out of the fingerprint avoids a confusing
    selectable-but-no-op review row and shrinks the surface)."""
    keys: dict[str, str] = {}
    data = _load_json(path)
    if isinstance(data, dict):
        for key, value in data.items():
            if denylist.is_denied_json_key(key) or key in denylist.STRUCTURAL_IMMUNE_KEYS:
                continue
            keys[key] = _canonical_hash(value)
    return {"kind": "json-keys", "keys": keys}


def _mcp_entry(path: Path) -> dict:
    """``{kind:'mcp', servers:{name: canonical-hash}}`` from the narrow ``mcpServers``-only read."""
    servers = {name: _canonical_hash(cfg) for name, cfg in read_mcp_servers(path).items()}
    return {"kind": "mcp", "servers": servers}


def _file_entry(path: Path) -> dict:
    if path.is_file() and not path.is_symlink():
        return {"kind": "file", "hash": "sha256:" + _sha256(path)}
    return {"kind": "file", "hash": ""}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_hash(value: object) -> str:
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
