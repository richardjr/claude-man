"""Per-project asset sync — host-side copy of CLAUDE.md + skills/agents between a synced
config-tier source and the container's host bind dirs.

The model (see docs/ROADMAP.md and the plan): a project's *asset source* lives in the CONFIG tier at
``~/.config/claude-man/assets/<slug>/`` (which the operator syncs across machines externally). Two
subtrees map to the two writable container binds, which are themselves HOST directories:

    assets/<slug>/workspace/<rel>  <->  config.workspace_dir(slug)/<rel>      (/workspace)
    assets/<slug>/claude/<rel>     <->  config.claude_config_dir(slug)/<rel>  (~/.claude)

Because the binds are host dirs, sync is plain ``shutil`` copy — no ``docker exec``/``cp`` — and works
whether the container is running, stopped, or never created. ``sync_in`` runs on start (asset wins),
``sync_out`` on stop (bind wins); the about-to-be-overwritten target is always backed up first, so a
last-write-wins overwrite never truly loses data.

This is DISTINCT from the Phase-5 review-gated sync-back (``syncback/``), which targets the operator's
*global* ``~/.claude`` with a human review gate. Asset sync is safe to run automatically because its
host target is an isolated per-project dir, never the live global config. The ``syncback.denylist`` is
still asserted as defence-in-depth on the claude side so a hand-edited allowlist can't widen sync to
credentials/session state. Pure stdlib (no textual) so the CLI/lifecycle import it freely.
"""

from __future__ import annotations

import datetime
import filecmp
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .checkout.repos import is_within
from .registry.schema import Project
from .syncback import denylist

ProgressFn = Callable[[str], None]

# Cruft never copied into a synced tree (mirrors profiles.seed._TREE_EXCLUDE_NAMES — kept local to
# avoid importing a private symbol across packages).
_TREE_EXCLUDE_NAMES = frozenset(
    {"cache", "data", "node_modules", "blocklist.json", ".DS_Store", ".git", "statsig"}
)


@dataclass(frozen=True)
class SyncReport:
    ok: bool
    detail: str                          # human one-liner folded into lifecycle.Result (or "" if nothing happened)
    copied: tuple[str, ...] = ()         # "<root>/<rel>" entries written this pass
    backed_up: tuple[str, ...] = ()      # entries copied into backups/ before overwrite
    notes: tuple[str, ...] = field(default_factory=tuple)  # skips / advisories / soft errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def sync_in(project: Project, *, on_progress: ProgressFn | None = None) -> SyncReport:
    """Copy the asset SOURCE -> the host bind dirs (asset wins). Bootstraps a stub CLAUDE.md first."""
    return _sync(project, reverse=False, on_progress=on_progress)


def sync_out(project: Project, *, on_progress: ProgressFn | None = None) -> SyncReport:
    """Copy the host bind dirs -> the asset SOURCE (bind wins). No-op when the binds don't exist."""
    return _sync(project, reverse=True, on_progress=on_progress)


def bootstrap(project: Project) -> str | None:
    """Create a stub workspace CLAUDE.md in the asset source if none exists (CLI ``project assets``)."""
    return _bootstrap_claude_md(project)


# ---------------------------------------------------------------------------
# Allowlist safety (claude side)
# ---------------------------------------------------------------------------
# The claude side is a default-DENY allowlist: only these known-safe content artifacts may sync
# into/out of ~/.claude. A blocklist can't safely enumerate every sensitive top-level path — e.g.
# `projects` is NOT denylisted (only `projects/*/*.jsonl` is) yet holds session transcripts, and
# `settings.json` carries machine-local perms/hooks. So anything not in this set is refused outright;
# nested entries inside an allowed tree get a second basename-denylist + symlink pass in _copy_dir_filtered.
_CLAUDE_SAFE_ENTRIES = frozenset({"skills", "agents", "commands"})


def _safe_claude_entries(entries: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split claude-side entries into (safe, dropped) by the default-deny allowlist above."""
    safe: list[str] = []
    dropped: list[str] = []
    for rel in entries:
        (safe if rel in _CLAUDE_SAFE_ENTRIES else dropped).append(rel)
    return tuple(safe), tuple(dropped)


# ---------------------------------------------------------------------------
# Core sync
# ---------------------------------------------------------------------------
def _sync(project: Project, *, reverse: bool, on_progress: ProgressFn | None) -> SyncReport:
    slug = project.slug
    ts = _timestamp()
    label = "out" if reverse else "in"
    copied: list[str] = []
    backed_up: list[str] = []
    notes: list[str] = []
    ok = True

    if not reverse:
        note = _bootstrap_claude_md(project)
        if note:
            notes.append(note)

    safe_claude, dropped = _safe_claude_entries(project.sync.claude)
    for rel in dropped:
        notes.append(f"claude/{rel}: refused (only skills/agents/commands are syncable artifacts)")

    specs = (
        ("workspace", config.project_assets_workspace_dir(slug),
         config.workspace_dir(slug), tuple(project.sync.workspace), False),
        ("claude", config.project_assets_claude_dir(slug),
         config.claude_config_dir(slug), safe_claude, True),
    )
    for root_name, asset_root, bind_root, entries, gate in specs:
        src_root, dst_root = (bind_root, asset_root) if reverse else (asset_root, bind_root)
        for rel in entries:
            tag = f"{root_name}/{rel}"
            if gate and denylist.is_denied_path(rel):  # defence-in-depth (already filtered above)
                continue
            src, dst = src_root / rel, dst_root / rel
            if not is_within(dst, dst_root):  # destination containment guard
                notes.append(f"{tag}: refused (escapes {dst_root})")
                continue
            if not src.exists():
                continue  # nothing to copy this direction (incl. a broken symlink)
            if src.is_symlink():  # a top-level symlink: refuse if it escapes or targets a denied path
                note = _check_symlink(src, tag, src_root=src_root, gate=gate)
                if note:
                    notes.append(note)
                    continue
                if src.is_dir():  # in-tree symlinked dir — skip (avoid cycles/dupes)
                    notes.append(f"{tag}: skipped (symlinked directory)")
                    continue
            if _unchanged(src, dst):
                continue  # identical — skip both backup and copy (bounds backup growth)
            if dst.exists() or dst.is_symlink():
                if _backup_target(slug, dst, ts=ts, root_label=label, rel=tag) is None:
                    ok = False
                    notes.append(f"{tag}: backup FAILED — overwrite refused")
                    continue
                backed_up.append(tag)
            try:
                sub = _copy_tree_or_file(src, dst, root_rel=rel, src_root=src_root, gate=gate)
            except (OSError, shutil.Error) as exc:  # best-effort: one bad entry never aborts the rest
                notes.append(f"{tag}: copy failed ({exc})")
                continue
            notes.extend(sub)  # per-file skips (denylisted/escaping entries inside a synced tree)
            copied.append(tag)
            if on_progress:
                on_progress(f"sync-{label} {tag}")

    return SyncReport(ok=ok, detail=_format_detail(label, copied, backed_up, notes),
                      copied=tuple(copied), backed_up=tuple(backed_up), notes=tuple(notes))


def _unchanged(src: Path, dst: Path) -> bool:
    """True iff dst exists and matches src, so we can skip a no-op copy + backup.

    Files: byte-exact (``filecmp.cmp(shallow=False)``). Dirs: a recursive ``dircmp`` (shallow stat —
    the standard rsync-default trade-off: a content change with an identical size+mtime is not
    detected, which is vanishingly rare for edited text)."""
    src_dir = src.is_dir() and not src.is_symlink()
    dst_dir = dst.is_dir() and not dst.is_symlink()
    if not dst.exists():
        return False
    if src_dir and dst_dir:
        return not _dirs_differ(src, dst)
    if not src_dir and dst.is_file() and not dst.is_symlink():
        return filecmp.cmp(src, dst, shallow=False)
    return False  # type mismatch (file<->dir, or a symlink) — treat as changed


def _dirs_differ(a: Path, b: Path) -> bool:
    cmp = filecmp.dircmp(a, b, ignore=list(_TREE_EXCLUDE_NAMES))
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return True
    return any(_dirs_differ(a / sub, b / sub) for sub in cmp.common_dirs)


def _copy_tree_or_file(src: Path, dst: Path, *, root_rel: str, src_root: Path, gate: bool) -> list[str]:
    """Copy one allowlist entry. Dir entries are copied via a FILTERED recursive walk (not a blind
    ``shutil.copytree``) so the denylist + symlink-escape guards apply to EVERY nested entry, not just
    the top-level one (invariants 1 & 5). Dir entries MERGE into an existing dst (overwrite same-named,
    keep others — deletions don't propagate in v1). Returns per-entry skip notes."""
    if src.is_dir() and not src.is_symlink():
        if dst.exists() and not (dst.is_dir() and not dst.is_symlink()):
            _remove_path(dst)  # dst was a file/symlink where src is a dir — replace
        return _copy_dir_filtered(src, dst, root_rel=root_rel, src_root=src_root, gate=gate)
    _replace_with_file(src, dst)  # a file (or an already-vetted in-tree file symlink) — deref-copy
    return []


def _copy_dir_filtered(src_dir: Path, dst_dir: Path, *, root_rel: str, src_root: Path,
                       gate: bool) -> list[str]:
    """Recursively copy ``src_dir`` -> ``dst_dir``, applying per-entry guards (the security boundary):

    - cruft names (``_TREE_EXCLUDE_NAMES``) are skipped;
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
        if name in _TREE_EXCLUDE_NAMES:
            continue
        if gate and (name == "settings.json" or denylist.is_denied_path(name)):
            notes.append(f"{rel}: skipped (denylisted name)")
            continue
        dst = dst_dir / name
        if entry.is_symlink():
            note = _check_symlink(entry, rel, src_root=src_root, gate=gate)
            if note:
                notes.append(note)
                continue
            if entry.is_dir():
                notes.append(f"{rel}: skipped (symlinked directory)")
                continue
            _replace_with_file(entry, dst)
        elif entry.is_dir():
            notes += _copy_dir_filtered(entry, dst, root_rel=rel, src_root=src_root, gate=gate)
        else:
            _replace_with_file(entry, dst)
    return notes


def _check_symlink(link: Path, rel: str, *, src_root: Path, gate: bool) -> str | None:
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


def _replace_with_file(src: Path, dst: Path) -> None:
    """Deref-copy a file to ``dst`` (never copy2 *through* an existing symlink — could write out of tree)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        _remove_path(dst)
    shutil.copy2(src, dst)


def _remove_path(p: Path) -> None:
    if p.is_symlink():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def _backup_target(slug: str, dst: Path, *, ts: str, root_label: str, rel: str) -> str | None:
    """Copy ``dst`` into ``backups_dir(slug)/<ts>/<in|out>/<root>/<rel>`` before it is overwritten.

    Returns the rel tag on success, or None if the backup failed (caller then REFUSES the overwrite —
    "nothing truly lost"). Backups live in the STATE tier (never synced) and are removed on delete."""
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


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def _bootstrap_claude_md(project: Project) -> str | None:
    """Write a minimal stub CLAUDE.md into the asset source iff CLAUDE.md is a synced workspace
    entry and exists in NEITHER the asset source nor the workspace bind. Returns a note or None."""
    slug = project.slug
    if "CLAUDE.md" not in project.sync.workspace:
        return None
    asset = config.project_assets_workspace_dir(slug) / "CLAUDE.md"
    bind = config.workspace_dir(slug) / "CLAUDE.md"
    if asset.exists() or bind.exists():
        return None
    try:
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text(_stub_claude_md(slug), encoding="utf-8")
    except OSError as exc:
        return f"bootstrap CLAUDE.md failed: {exc}"
    return f"bootstrapped {asset}"


def _stub_claude_md(slug: str) -> str:
    aw = config.project_assets_workspace_dir(slug) / "CLAUDE.md"
    ac = config.project_assets_claude_dir(slug)
    return (
        f"# CLAUDE.md — project: {slug}\n\n"
        "<!--\n"
        f"Project instructions for Claude Code in the claude-man `{slug}` container.\n\n"
        f"Synced from: {aw}\n"
        "Edit it here on the host, or in-container at /workspace/CLAUDE.md — changes sync back out\n"
        "when the container stops. Drop skills/agents under\n"
        f"  {ac}/skills/ , {ac}/agents/\n"
        "to have them appear under ~/.claude inside the container.\n"
        "-->\n"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _timestamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _format_detail(label: str, copied: list[str], backed_up: list[str], notes: list[str]) -> str:
    if not copied and not notes:
        return ""  # genuinely nothing happened — keep the lifecycle Result quiet
    parts = [f"asset sync-{label}: {len(copied)} copied"]
    if backed_up:
        parts.append(f"{len(backed_up)} backed up")
    detail = ", ".join(parts)
    if notes:
        detail += "; " + "; ".join(notes)
    return detail
