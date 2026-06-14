"""Shared filesystem-merge primitives — the security-reviewed copy/backup/symlink-guard helpers.

Factored out of ``assets.py`` (the per-project asset sync) with ZERO behaviour change so the
review-gated sync-back merge (``syncback/merge.py``) shares the SAME audited implementation of:
timestamped backups, deref-copy that never writes *through* a symlink, the default-deny filtered
recursive copy (per-entry denylist + symlink-escape guards), and the unsafe-symlink check. These are
the boundary that keeps invariants 1 & 5 (credentials/session state never ride along; a symlink can't
escape the source tree or target a denied path). Pure stdlib + the denylist (no textual/docker).
"""

from __future__ import annotations

import datetime
import errno
import os
import shutil
import stat
from pathlib import Path

from .. import config
from ..checkout.repos import is_within
from . import denylist

# A leaf symlink is dereferenced (legit in-tree skill symlinks), but only through a bounded chain.
_MAX_LEAF_SYMLINK_DEPTH = 8

# Cruft never copied into a synced/merged tree (mirrors profiles.seed._TREE_EXCLUDE_NAMES — kept
# duplicated there to avoid importing a private symbol across packages).
TREE_EXCLUDE_NAMES = frozenset(
    {"cache", "data", "node_modules", "blocklist.json", ".DS_Store", ".git", "statsig"}
)


def timestamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def copy_dir_filtered(src_dir: Path, dst_dir: Path, *, root_rel: str, src_root: Path,
                      gate: bool) -> list[str]:
    """Recursively copy ``src_dir`` -> ``dst_dir``, applying per-entry guards (the security boundary):

    - cruft names (``TREE_EXCLUDE_NAMES``) are skipped;
    - on the gated (claude) side, any entry whose basename the denylist forbids — at ANY depth — is
      skipped (so ``skills/sessions/`` or a nested ``.credentials.json`` never rides along), and a bare
      ``settings.json`` is refused (machine-local perms/hooks);
    - a symlink that escapes ``src_root`` (or, gated, whose in-tree target is a denylisted path) is
      skipped; an in-tree symlinked DIR is skipped (avoid cycles); an in-tree symlinked FILE is
      dereferenced. Real files/dirs copy/recurse normally.
    """
    notes: list[str] = []
    dst_dir.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src_dir.iterdir()):
        name = entry.name
        rel = f"{root_rel}/{name}"
        if name in TREE_EXCLUDE_NAMES:
            continue
        if gate and (name == "settings.json" or denylist.is_denied_path(name)):
            notes.append(f"{rel}: skipped (denylisted name)")
            continue
        dst = dst_dir / name
        if entry.is_symlink():
            note = check_symlink(entry, rel, src_root=src_root, gate=gate)
            if note:
                notes.append(note)
                continue
            if entry.is_dir():
                notes.append(f"{rel}: skipped (symlinked directory)")
                continue
            replace_with_file(dst, anchor=src_root, rel_parts=tuple(rel.split("/")))
        elif entry.is_dir():
            notes += copy_dir_filtered(entry, dst, root_rel=rel, src_root=src_root, gate=gate)
        else:
            replace_with_file(dst, anchor=src_root, rel_parts=tuple(rel.split("/")))
    return notes


def check_symlink(link: Path, rel: str, *, src_root: Path, gate: bool) -> str | None:
    """Return a skip-note if ``link`` is an unsafe symlink, else None (safe to dereference).

    Refuses a target that escapes ``src_root`` (catches ``-> /etc``, ``-> ~/.ssh``) and, on the gated
    side, a target that resolves to a denylisted in-tree path (catches a ``skills/leak -> .credentials.json``
    exfil symlink on sync-out)."""
    if not is_within(link, src_root):
        return f"{rel}: skipped (symlink escapes source)"
    if gate:
        try:
            tgt_rel = link.resolve().relative_to(src_root.resolve()).as_posix()
        except (OSError, ValueError):
            return f"{rel}: skipped (unresolvable symlink)"
        if denylist.is_denied_path(tgt_rel):
            return f"{rel}: skipped (symlink targets denylisted {tgt_rel})"
    return None


def replace_with_file(dst: Path, *, anchor: Path, rel_parts: tuple[str, ...]) -> None:
    """Copy the source at ``anchor/<rel_parts>`` to ``dst``, TOCTOU-safe against the untrusted agent.

    The agent keeps running during sync-back and owns the source tree (a bind mount), so it can swap
    ANY path component — an intermediate directory OR the leaf — for a symlink between a stat-time
    check and the copy, redirecting the read to an out-of-tree operator secret. (An earlier fix that
    only guarded the leaf with ``O_NOFOLLOW`` was bypassed via an intermediate-dir swap.) The fix:
    open the source component-by-component from ``anchor`` (a root whose PARENT the agent cannot reach
    — not bind-mounted into the container), each ``open`` carrying ``O_NOFOLLOW``, so a symlink at any
    level fails with ``ELOOP`` rather than being followed. A LEAF symlink to an IN-TREE target is still
    dereferenced (bounded re-walk), so legitimate in-tree skill symlinks keep working. The existing dst
    is removed first so we never write *through* a destination symlink either."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        remove_path(dst)
    fd = _open_regular_no_escape(anchor, list(rel_parts))
    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb", closefd=True) as fsrc, open(dst, "wb") as fdst:
        shutil.copyfileobj(fsrc, fdst)
    try:
        os.chmod(dst, stat.S_IMODE(st.st_mode))  # preserve mode (skills may be executable)
    except OSError:
        pass


def _open_regular_no_escape(anchor: Path, rel_parts: list[str], _depth: int = 0) -> int:
    """Open the regular file at ``anchor/<rel_parts>`` for reading, refusing a symlink at EVERY path
    component (the agent cannot redirect the read by symlink-swapping any directory or the leaf).

    ``anchor`` is opened directly — it is a trusted root whose parent is not agent-accessible (the
    state-tier ``project_state_dir`` is never bind-mounted, so the agent can't replace the bind root).
    Every component below it is opened with ``O_NOFOLLOW`` (a symlink → ``ELOOP``). A LEAF symlink is
    the one allowed symlink: it is dereferenced, but only to a target that (a) is relative and stays
    within ``anchor`` and (b) is itself reached by the same all-``O_NOFOLLOW`` walk (bounded depth) —
    so it cannot escape. ``O_NONBLOCK`` + an ``fstat`` ``S_ISREG`` check refuses a FIFO/device/dir."""
    if not rel_parts:
        raise OSError("empty source path")
    if _depth > _MAX_LEAF_SYMLINK_DEPTH:
        raise OSError("symlink chain too deep")
    dir_fd = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in rel_parts[:-1]:
            if part in ("", ".", ".."):
                raise OSError(f"illegal path component {part!r}")
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = nxt
        leaf = rel_parts[-1]
        if leaf in ("", ".", ".."):
            raise OSError(f"illegal leaf component {leaf!r}")
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        except OSError as exc:
            if exc.errno != errno.ELOOP:  # ELOOP == the leaf is a symlink
                raise
            target = os.readlink(leaf, dir_fd=dir_fd)
            tgt_parts = _resolve_in_tree(anchor, rel_parts[:-1], target)
            if tgt_parts is None:
                raise OSError("leaf symlink target escapes the tree") from exc
            return _open_regular_no_escape(anchor, tgt_parts, _depth + 1)
    finally:
        os.close(dir_fd)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise OSError("source is not a regular file")
    return fd


def _resolve_in_tree(anchor: Path, base_parts: list[str], target: str) -> list[str] | None:
    """Resolve a leaf symlink ``target`` (read at ``base_parts/``) to anchor-relative parts, or None
    if it escapes the anchor. Textual ``..``/prefix handling is sound because the caller re-walks the
    result all-``O_NOFOLLOW`` from ``anchor`` — a symlink anywhere on the way re-``ELOOP``s, so the
    textual interpretation can only ever resolve to a real in-tree file or be refused (never escape)."""
    if os.path.isabs(target):
        norm = os.path.normpath(target)
        root = os.path.normpath(str(anchor))
        prefix = root + os.sep
        if norm == root or not norm.startswith(prefix):
            return None  # absolute target is the anchor dir itself or escapes it
        parts = [s for s in norm[len(prefix):].split(os.sep) if s not in ("", ".")]
        return parts or None
    parts = list(base_parts)
    for seg in target.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None  # escapes above the anchor
            parts.pop()
        else:
            parts.append(seg)
    return parts or None


def copy_regular_file(src: Path, dst: Path) -> None:
    """Plain deref-copy of a TRUSTED regular file (operator-owned content — e.g. mirroring a
    just-written host ``~/.claude`` artifact into the audit repo). NOT for agent-writable sources:
    those must go through ``replace_with_file``'s all-``O_NOFOLLOW`` walk."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        remove_path(dst)
    shutil.copyfile(src, dst)


def remove_path(p: Path) -> None:
    if p.is_symlink():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def backup_target(slug: str, dst: Path, *, ts: str, root_label: str, rel: str) -> str | None:
    """Copy ``dst`` into ``backups_dir(slug)/<ts>/<root_label>/<rel>`` before it is overwritten.

    Returns the rel tag on success, or None if the backup failed (caller then REFUSES the overwrite —
    "nothing truly lost"). Backups live in the STATE tier (never synced) and are removed on delete.
    ``root_label`` is a free string — the asset sync passes ``"in"``/``"out"``; the sync-back merge
    passes ``"merge"`` (so a host ``~/.claude`` overwrite is recoverable from the per-project backups)."""
    backup = config.backups_dir(slug) / ts / root_label / rel
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_dir() and not dst.is_symlink():
            shutil.copytree(dst, backup, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(dst, backup, follow_symlinks=False)
        return rel
    except (OSError, shutil.Error):
        return None
