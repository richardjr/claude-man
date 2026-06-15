"""Materialize a project's pack selection into its asset source (host-side, Phase 6).

Writes are confined to the per-project ASSET SOURCE (``~/.config/claude-man/assets/<slug>/``) —
the existing ``assets.sync_in`` rail then carries them into the container binds with all its
guards (claude-side allowlist, denylist, symlink containment, backup-before-overwrite). The one
exception is the REMOVAL pass, which also deletes a deselected pack's files from the binds
directly: ``sync_in`` merges and never propagates deletions, so a deselected skill would
otherwise linger active in the container.

The state-tier **manifest** (``config.packs_manifest_path``) records every pack-managed path +
content hash. It is the ours/theirs boundary:

- an existing file NOT in the manifest is operator-owned -> never overwritten (skip + note);
- a manifested file whose content drifted (agent edit ridden back by ``sync_out``) is
  curated-wins: backed up, re-stamped from the library, noted;
- deselection removes exactly the manifested paths, then prunes empty dirs.

CLAUDE.md fragments are LINKED, not inlined: files land under ``workspace/.claude-man/<pack>/``
and a fenced, claude-man-owned block of ``@`` imports is patched into the workspace CLAUDE.md
(operator content outside the block is never touched — the settings.json field-patch
philosophy). See docs/PACKS.md.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import claudemd, config
from ..registry.schema import Project
from . import library

ProgressFn = Callable[[str], None]

BLOCK_START = "<!-- claude-man:packs (managed — edits inside this block are overwritten) -->"
BLOCK_END = "<!-- /claude-man:packs -->"
# Marker MATCHING is by prefix so an in-container edit to the marker comment text can't orphan
# the block (the rewrite restores the canonical markers).
_START_PREFIX = "<!-- claude-man:packs"
_END_PREFIX = "<!-- /claude-man:packs"

FRAGMENTS_DIR = ".claude-man"        # /workspace/.claude-man/<pack>/<fragment>.md
_WORKSPACE_ROOT = "workspace"        # manifest key prefixes — match the asset-source subtrees
_CLAUDE_ROOT = "claude"
_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class RefreshReport:
    ok: bool
    detail: str                          # human one-liner folded into lifecycle.Result ("" = no-op)
    refreshed: tuple[str, ...] = ()      # manifest keys written this pass
    removed: tuple[str, ...] = ()        # manifest keys removed (deselected)
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Pure pieces (unit-tested without a filesystem)
# ---------------------------------------------------------------------------
def patch_block(text: str, lines: list[str]) -> str:
    """Patch the packs ``@``-import block into a CLAUDE.md body — the shared managed-block patcher
    (``claudemd.patch_block``) bound to the packs markers. Empty ``lines`` removes the block;
    operator content outside the markers is preserved; the block is rewritten in place (so it
    coexists with the scratch-dir block without either migrating). See ``claudemd``."""
    return claudemd.patch_block(text, lines, begin=BLOCK_START, end=BLOCK_END,
                                begin_prefix=_START_PREFIX, end_prefix=_END_PREFIX)


def block_lines(selection: tuple[str, ...], lib: dict[str, library.Pack]) -> list[str]:
    """The ``@`` import lines for the fenced block, in selection order (fragments sorted within
    a pack; unknown/fragment-less packs contribute nothing)."""
    lines: list[str] = []
    for name in selection:
        pack = lib.get(name)
        if pack is None:
            continue
        lines.extend(f"@{FRAGMENTS_DIR}/{pack.name}/{frag}" for frag in pack.fragments)
    return lines


def desired_files(selection: tuple[str, ...], lib: dict[str, library.Pack]) -> tuple[dict[str, Path], tuple[str, ...]]:
    """Map every file the selection wants materialized: manifest key -> library source path.

    Keys are ``workspace/.claude-man/<pack>/<frag>`` and ``claude/skills/<skill>/<rel…>``
    (skill trees walked recursively; cruft/symlinks are not expected in the curated library, but
    symlinks are refused defensively). Returns ``(files, notes)`` — unknown pack names become
    notes, never errors (a stale selection must not block a start)."""
    files: dict[str, Path] = {}
    notes: list[str] = []
    for name in selection:
        pack = lib.get(name)
        if pack is None:
            notes.append(f"unknown pack {name!r} (not in the library — skipped)")
            continue
        for frag in pack.fragments:
            files[f"{_WORKSPACE_ROOT}/{FRAGMENTS_DIR}/{pack.name}/{frag}"] = \
                pack.path / library.CLAUDE_MD_DIR / frag
        for skill in pack.skills:
            skill_dir = pack.path / library.SKILLS_DIR / skill
            for src in sorted(p for p in skill_dir.rglob("*") if not p.is_dir()):
                if src.is_symlink():
                    notes.append(f"{name}: skipped symlink {src.name!r} in skill {skill!r}")
                    continue
                rel = src.relative_to(skill_dir).as_posix()
                files[f"{_CLAUDE_ROOT}/skills/{skill}/{rel}"] = src
    return files, tuple(notes)


# ---------------------------------------------------------------------------
# Manifest (state tier)
# ---------------------------------------------------------------------------
def load_manifest(slug: str) -> dict[str, str]:
    """key -> sha256 of the pack-managed files, ``{}`` when absent/unreadable (fail-soft: an
    unreadable manifest degrades to 'nothing is ours', which can only skip-with-note, never
    overwrite operator files)."""
    path = config.packs_manifest_path(slug)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        files = data.get("files", {})
        return {str(k): str(v) for k, v in files.items()} if isinstance(files, dict) else {}
    except (OSError, ValueError):
        return {}


def save_manifest(slug: str, files: dict[str, str]) -> None:
    path = config.packs_manifest_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"version": _MANIFEST_VERSION, "files": files},
                              indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Refresh (the lifecycle hook + the CLI verb body)
# ---------------------------------------------------------------------------
def refresh(project: Project, *, root: Path | None = None,
            on_progress: ProgressFn | None = None) -> RefreshReport:
    """Bring the project's asset source in line with its pack selection (idempotent).

    Never raises for content-level problems (bad selection entries, drift, collisions become
    notes); a malformed LIBRARY raises ``library.LibraryError`` — callers on the start path wrap
    this fail-soft (a broken library must not block a container start)."""
    slug = project.slug
    lib = library.discover(root)
    desired, notes_t = desired_files(project.packs, lib)
    notes: list[str] = list(notes_t)
    manifest = load_manifest(slug)
    refreshed: list[str] = []
    removed: list[str] = []
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ok = True

    if project.packs and not project.sync.enabled:
        notes.append("sync disabled — packs are materialized but won't reach the container")
    if project.packs and FRAGMENTS_DIR not in project.sync.workspace:
        notes.append(f"'{FRAGMENTS_DIR}' missing from [project.sync] workspace — fragments won't sync")

    new_manifest = dict(manifest)
    for key, src in desired.items():
        dst = config.project_assets_dir(slug) / key
        try:
            want_hash = library.file_hash(src)
        except OSError as exc:
            notes.append(f"{key}: unreadable library source ({exc})")
            continue
        have_hash = library.file_hash(dst) if dst.is_file() else None
        if have_hash == want_hash:
            new_manifest[key] = want_hash  # adopt/repair the manifest entry; content already right
            continue
        if have_hash is not None and key not in manifest:
            notes.append(f"{key}: exists and is not pack-managed — operator file wins (skipped)")
            continue
        if have_hash is not None:  # ours, but stale or drifted — back up, then re-stamp
            if not _backup(slug, dst, ts=ts, key=key):
                ok = False
                notes.append(f"{key}: backup FAILED — overwrite refused")
                continue
            if have_hash != manifest.get(key):
                notes.append(f"{key}: drifted from the curated copy — overwritten (backed up)")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except OSError as exc:
            ok = False
            notes.append(f"{key}: write failed ({exc})")
            continue
        new_manifest[key] = want_hash
        refreshed.append(key)
        if on_progress:
            on_progress(f"packs: refreshed {key}")

    # Removal pass: manifested files the selection no longer wants — from the asset source AND
    # the binds (sync_in merges; deletions would not propagate). Backed up first.
    for key in sorted(set(manifest) - set(desired)):
        asset_root, bind_root = config.project_assets_dir(slug), _bind_base(slug, key)
        for path, stop in ((asset_root / key, asset_root),
                           (bind_root / _bind_rel(key), bind_root)):
            if not path.is_file():
                continue
            if not _backup(slug, path, ts=ts, key=key):
                ok = False
                notes.append(f"{key}: backup FAILED — removal refused")
                break
            try:
                path.unlink()
                _prune_empty_dirs(path.parent, stop=stop)
            except OSError as exc:
                ok = False
                notes.append(f"{key}: remove failed ({exc})")
                break
        else:
            new_manifest.pop(key, None)
            removed.append(key)
            if on_progress:
                on_progress(f"packs: removed {key}")

    note = _patch_claude_md(project, block_lines(project.packs, lib))
    if note:
        notes.append(note)
    if new_manifest != manifest:
        save_manifest(slug, new_manifest)
    return RefreshReport(ok=ok, detail=_format_detail(refreshed, removed, notes),
                         refreshed=tuple(refreshed), removed=tuple(removed), notes=tuple(notes))


def _bind_base(slug: str, key: str) -> Path:
    return config.workspace_dir(slug) if key.startswith(_WORKSPACE_ROOT + "/") \
        else config.claude_config_dir(slug)


def _bind_rel(key: str) -> str:
    return key.split("/", 1)[1]  # strip the workspace/|claude/ manifest-root prefix


def _prune_empty_dirs(start: Path, *, stop: Path) -> None:
    """rmdir upward from ``start`` while empty, never removing ``stop`` itself."""
    cur = start
    while cur != stop and cur.is_dir() and not any(cur.iterdir()):
        cur.rmdir()
        cur = cur.parent


def _backup(slug: str, target: Path, *, ts: str, key: str) -> bool:
    """Copy ``target`` into ``backups/<ts>/packs/<key>`` before overwrite/removal (same backups
    tree the asset sync uses). False = backup failed -> the caller must refuse the write."""
    backup = config.backups_dir(slug) / ts / "packs" / key
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup, follow_symlinks=False)
        return True
    except (OSError, shutil.Error):
        return False


def _patch_claude_md(project: Project, lines: list[str]) -> str | None:
    """Patch the fenced ``@``-import block into the ASSET-SOURCE workspace CLAUDE.md.

    If the asset copy is missing but the bind has one (operator authored it in-container or
    pre-sync), the bind copy is ADOPTED first — patching a fresh stub instead would overwrite
    the operator's file on the next asset-wins ``sync_in``. Both missing -> a stub is created
    (with the block) so the imports always have a host. Returns a note or None."""
    slug = project.slug
    asset = config.project_assets_workspace_dir(slug) / "CLAUDE.md"
    bind = config.workspace_dir(slug) / "CLAUDE.md"
    try:
        if not asset.exists() and bind.is_file():
            asset.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bind, asset)
        if asset.exists():
            text = asset.read_text(encoding="utf-8")
        elif lines:
            text = f"# CLAUDE.md — project: {slug}\n"
        else:
            return None  # no file anywhere and nothing to link — leave it absent
        patched = patch_block(text, lines)
        if patched != text or not asset.exists():
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text(patched, encoding="utf-8")
    except OSError as exc:
        return f"CLAUDE.md block patch failed: {exc}"
    return None


def _format_detail(refreshed: list[str], removed: list[str], notes: list[str]) -> str:
    if not refreshed and not removed and not notes:
        return ""
    parts = []
    if refreshed:
        parts.append(f"{len(refreshed)} refreshed")
    if removed:
        parts.append(f"{len(removed)} removed")
    detail = "packs: " + (", ".join(parts) if parts else "up to date")
    if notes:
        detail += "; " + "; ".join(notes)
    return detail
