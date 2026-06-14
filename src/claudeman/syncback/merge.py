"""Review-gated three-way merge of in-container Claude config changes back to the host (Phase 5).

``apply_accepted(slug, decisions)`` writes ONLY the changes the operator accepted into the operator's
real global ``~/.claude``, under a GLOBAL ``flock`` (the merge target is the singular host config,
regardless of profile). The denylist is re-asserted at apply time AND again at git-staging
(invariant 5). The order, per accepted change:

  * **backup first** — every host target is copied into the per-project ``backups/`` via
    ``fsmerge.backup_target(root_label="merge")``; a backup failure REFUSES the overwrite, so nothing
    is ever lost.
  * **trees** copy container→host through the SAME gated primitive as asset-sync (per-entry denylist
    re-assert + ``check_symlink`` escape guard + containment + deref-copy) — never a blind copytree.
  * **settings.json** is an INVERSE field-patch: start from the HOST dict, overlay only accepted
    top-level keys from the container value, NEVER touching ``hooks``/``statusLine``
    (``STRUCTURAL_IMMUNE_KEYS``) or any ``is_denied_json_key``; atomic temp + ``os.replace`` write.
  * **deletions** (only when explicitly accepted) remove the host path under containment.
  * **MCP** is gate-only in v1 — detected/diffed, never applied (no ``claude mcp`` exec).
  * accepted file artifacts are mirrored into ``config.sync_audit_dir()/<slug>/`` and git-committed
    (a free revert log), with ``is_denied_path`` re-asserted over the staged set before ``git add``.
  * the baseline is refreshed from the REAL post-merge on-disk state (partial-merge safe).
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .. import config, gitconfig
from ..checkout.repos import is_within
from . import artifacts, baseline, denylist, detect, fsmerge


@dataclass(frozen=True)
class MergeReport:
    ok: bool
    applied: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    audit_commit: str = ""

    @property
    def detail(self) -> str:
        parts = [f"sync-back: {len(self.applied)} applied"]
        if self.refused:
            parts.append(f"{len(self.refused)} refused")
        if self.audit_commit:
            parts.append(f"audit {self.audit_commit}")
        out = ", ".join(parts)
        extras = list(self.refused) + list(self.notes)
        if extras:
            out += "; " + "; ".join(extras)
        return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def apply_accepted(slug: str, decisions: dict[str, str]) -> MergeReport:
    """Apply the accepted (``decisions[label] == "accept"``) changes for ``slug``.

    ``decisions`` maps ``Change.label`` → ``accept`` / ``reject`` / ``skip``. The merge re-detects
    fresh and matches accepted labels (robust to any plan→apply drift — content is re-read here)."""
    lock_path = config.syncback_lock_path()
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "w")
    except OSError as exc:
        return MergeReport(False, notes=(f"could not open merge lock: {exc}",))
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return MergeReport(False, notes=("another sync-back merge is in progress; retry",))
    try:
        return _apply_locked(slug, decisions)
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


# ---------------------------------------------------------------------------
# Internals (run under the global lock)
# ---------------------------------------------------------------------------
def _apply_locked(slug: str, decisions: dict[str, str]) -> MergeReport:
    accepted = [c for c in detect.detect_changes(slug) if decisions.get(c.label) == "accept"]
    ts = fsmerge.timestamp()
    applied: list[str] = []
    refused: list[str] = []
    notes: list[str] = []
    staged: list[tuple[str, Path, bool]] = []  # (audit_rel, host_path, deleted)

    for c in accepted:
        if c.kind in ("tree", "tree-symlink", "file"):
            ok, audit_rel, host_path, deleted, reason, sub = _apply_tree_change(slug, c, ts)
            notes.extend(sub)
            if ok:
                applied.append(c.label)
                staged.append((audit_rel, host_path, deleted))
            else:
                refused.append(f"{c.label}: {reason}")
        elif c.kind == "mcp":
            notes.append(f"{c.label}: MCP apply deferred (gate-only in v1)")

    settings_changes = [c for c in accepted if c.kind == "json-keys"]
    if settings_changes:
        ok, host_path, applied_keys, sub = _apply_settings(slug, settings_changes, ts)
        notes.extend(sub)
        if ok:
            applied.extend(f"settings.json/{k}" for k in applied_keys)
            if applied_keys:
                staged.append(("settings.json", host_path, False))
        else:
            refused.extend(f"settings.json/{c.rel}: backup failed — not written" for c in settings_changes)

    audit_commit = _audit_commit(slug, staged, ts) if staged else ""

    try:
        baseline.write_baseline(slug)  # refresh from the REAL post-merge state (partial-merge safe)
    except OSError as exc:
        notes.append(f"baseline refresh failed: {exc}")

    return MergeReport(not refused, tuple(applied), tuple(refused), tuple(notes), audit_commit)


def _apply_tree_change(slug: str, c: "detect.Change", ts: str
                       ) -> tuple[bool, str, Path | None, bool, str, list[str]]:
    """Apply one accepted tree/file change. Returns ``(ok, audit_rel, host_path, deleted, reason,
    notes)``. Every guard the asset-sync walk applies is RE-ASSERTED here (the apply re-reads disk)."""
    art = _artifact(c.artifact)
    if art is None:
        return False, "", None, False, "unknown artifact", []
    rel = c.rel
    # Re-assert the denylist on every path segment (detect already filtered, but apply re-reads disk).
    if rel and any(denylist.is_denied_path(seg) for seg in rel.split("/")):
        return False, "", None, False, "denied path", []

    container_root = config.claude_config_dir(slug) / art.container_rel
    host_root = Path(art.host_target)
    src = container_root / rel if rel else container_root
    dst = host_root / rel if rel else host_root
    audit_rel = f"{art.container_rel}/{rel}" if rel else art.container_rel

    # Containment: the host destination must stay inside the artifact's host root (no `../` escape).
    if rel and not is_within(dst, host_root):
        return False, "", None, False, "escapes host target", []

    notes: list[str] = []
    # Backup the existing host target before any mutation (refuse on backup failure — nothing lost).
    if dst.exists() or dst.is_symlink():
        if fsmerge.backup_target(slug, dst, ts=ts, root_label="merge", rel=audit_rel) is None:
            return False, "", None, False, "backup failed — overwrite refused", []

    if c.change_type == "deleted":
        if not (dst.exists() or dst.is_symlink()):
            return True, audit_rel, dst, True, "", ["already absent on host"]
        fsmerge.remove_path(dst)
        return True, audit_rel, dst, True, "", notes

    # added / modified — re-assert the symlink guard, then deref-copy (never copy *through* a symlink).
    if not (src.exists() or src.is_symlink()):
        return False, "", None, False, "source vanished", []
    if src.is_symlink():
        note = fsmerge.check_symlink(src, rel, src_root=container_root, gate=True)
        if note:
            return False, "", None, False, note.split(": ", 1)[-1], []
        if src.is_dir():
            return False, "", None, False, "symlinked directory", []
    try:
        # Read the source via an all-O_NOFOLLOW component walk anchored at the BIND ROOT (whose parent
        # is not bind-mounted, so the agent can't symlink-swap it). Anchoring at container_root would
        # be unsafe — its first component (e.g. `skills`) is agent-writable and swappable.
        rel_parts = tuple(p for p in (*art.container_rel.split("/"), *rel.split("/")) if p)
        fsmerge.replace_with_file(dst, anchor=config.claude_config_dir(slug), rel_parts=rel_parts)
    except OSError as exc:
        return False, "", None, False, f"copy failed ({exc})", []
    return True, audit_rel, dst, False, "", notes


def _apply_settings(slug: str, changes: list["detect.Change"], ts: str
                    ) -> tuple[bool, Path | None, list[str], list[str]]:
    """Inverse field-patch: HOST dict overlaid with accepted container keys; immune/denied keys never
    touched; atomic write. One backup + one write for ALL accepted keys. Returns ``(ok, host_path,
    applied_keys, notes)``."""
    art = _artifact("settings.json")
    if art is None:
        return False, None, [], ["settings.json artifact missing"]
    host_path = Path(art.host_target)
    container_path = config.claude_config_dir(slug) / "settings.json"
    host_data = _load_json_obj(host_path)
    container_data = _load_json_obj(container_path)

    notes: list[str] = []
    applied_keys: list[str] = []
    for c in changes:
        key = c.rel
        if key in denylist.STRUCTURAL_IMMUNE_KEYS:
            notes.append(f"settings.json/{key}: host-structural key never overwritten")
            continue
        if denylist.is_denied_json_key(key):
            notes.append(f"settings.json/{key}: denied key skipped")
            continue
        if c.change_type == "deleted":
            host_data.pop(key, None)
            applied_keys.append(key)
        elif key in container_data:
            host_data[key] = container_data[key]
            applied_keys.append(key)
        else:
            notes.append(f"settings.json/{key}: container value vanished")

    if not applied_keys:
        return True, host_path, [], notes

    # Backup the host settings.json before the atomic overwrite (refuse on failure).
    if host_path.exists():
        if fsmerge.backup_target(slug, host_path, ts=ts, root_label="merge",
                                 rel="settings.json") is None:
            return False, host_path, [], notes
    try:
        host_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = host_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(host_data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, host_path)
    except OSError as exc:
        return False, host_path, [], notes + [f"settings.json write failed: {exc}"]
    return True, host_path, applied_keys, notes


# ---------------------------------------------------------------------------
# Audit repo (a free revert log; denylist re-asserted at staging)
# ---------------------------------------------------------------------------
def _audit_commit(slug: str, staged: list[tuple[str, Path, bool]], ts: str) -> str:
    """Mirror the accepted host artifacts under ``sync_audit_dir()/<slug>/`` and commit. Returns the
    short sha, or "" (git missing / nothing staged / denied-only)."""
    audit_root = config.sync_audit_dir()
    try:
        audit_root.mkdir(parents=True, exist_ok=True)
        if not (audit_root / ".git").is_dir():
            cp = subprocess.run(["git", "init", "-q", str(audit_root)],
                                capture_output=True, text=True, timeout=15, check=False)
            if cp.returncode != 0:
                return ""
        slug_root = audit_root / slug
        for audit_rel, host_path, deleted in staged:
            if denylist.is_denied_path(audit_rel):  # staging-time re-assert (invariant 5)
                continue
            mirror = slug_root / audit_rel
            if not is_within(mirror, slug_root):
                continue
            if deleted:
                fsmerge.remove_path(mirror)
            elif host_path is not None and host_path.is_file() and not host_path.is_symlink():
                fsmerge.copy_regular_file(host_path, mirror)  # trusted: the host file we just wrote
        add = subprocess.run(["git", "-C", str(audit_root), "add", "-A", slug],
                             capture_output=True, text=True, timeout=15, check=False)
        if add.returncode != 0:
            return ""
        status = subprocess.run(["git", "-C", str(audit_root), "status", "--porcelain"],
                                capture_output=True, text=True, timeout=15, check=False)
        if not status.stdout.strip():
            return ""  # nothing actually changed
        name, email = gitconfig.resolve_identity()
        name = name or "claude-man"
        email = email or "claude-man@localhost"
        commit = subprocess.run(
            ["git", "-C", str(audit_root),
             "-c", f"user.name={name}", "-c", f"user.email={email}",
             "-c", "commit.gpgsign=false",
             "commit", "-q", "-m", f"sync-back {slug} {ts}"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if commit.returncode != 0:
            return ""
        rev = subprocess.run(["git", "-C", str(audit_root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=15, check=False)
        return rev.stdout.strip() if rev.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _artifact(name: str) -> artifacts.Artifact | None:
    return next((a for a in artifacts.default_artifacts() if a.name == name), None)


def _load_json_obj(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
