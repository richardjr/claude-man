"""Seed a project's ``claude-config`` host dir before the first container start (review WIRE-3).

The writable bind at ``/home/agent/.claude`` must be pre-seeded host-side or the first in-container
``claude`` re-onboards / mis-identifies the account. We write a scrubbed ``.claude.json`` identity
stub (onboarding suppressed, display-safe identity only — never uuids) and, when a profile ``seed/``
dir exists (Phase 2 populates it), copy the allowlisted entries through the denylist.

The dir is created ``0700`` and owned by the host uid, which matches the container's ``--user 1000``
so in-container writes keep correct ownership.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .. import config
from ..registry.schema import Profile, Project
from ..syncback import denylist
from . import identity


def seed_project_config(
    project: Project, profile: Profile | None, *, overwrite_identity: bool = False
) -> Path:
    """Create + seed ``claude-config/`` for ``project``; returns the host dir path.

    The identity stub is written only if absent, unless ``overwrite_identity`` is set (used when
    a project is switched to a different account so the seeded identity follows the new profile).
    """
    cfg = config.claude_config_dir(project.slug)
    cfg.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg, 0o700)

    claude_json = cfg / ".claude.json"
    if overwrite_identity or not claude_json.exists():
        email = profile.account_email if profile else ""
        keep = profile.keep_identity_fields if profile else identity.DEFAULT_KEEP
        oauth = {"emailAddress": email} if email else {}
        stub = identity.build_identity_stub(oauth, keep)
        claude_json.write_text(json.dumps(stub, indent=2), encoding="utf-8")

    if profile is not None:
        _copy_profile_seed(profile, cfg)
    return cfg


def read_seeded_email(slug: str) -> str:
    """The account email recorded in a project's seeded ``.claude.json``, or '' if none."""
    path = config.claude_config_dir(slug) / ".claude.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    oauth = data.get("oauthAccount") if isinstance(data, dict) else None
    return oauth.get("emailAddress", "") if isinstance(oauth, dict) else ""


# Machine-local / cruft names never captured into a profile seed (review SYNC-2).
_TREE_EXCLUDE_NAMES = frozenset(
    {"cache", "data", "node_modules", "blocklist.json", ".DS_Store", ".git", "statsig"}
)


def _ignore_names(directory: str, names: list[str]) -> set[str]:
    return {n for n in names if n in _TREE_EXCLUDE_NAMES}


def _patch_settings(src: Path, dst: Path) -> None:
    """Copy settings.json with host hooks/statusLine + identity keys stripped (review SYNC-2).

    The host ``SessionEnd`` hook (``sync-claude.sh --save``) and bun statusLine must never run
    inside a container, so they are dropped from the seeded copy.
    """
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if isinstance(data, dict):
        for key in denylist.STRUCTURAL_IMMUNE_KEYS:  # hooks, statusLine
            data.pop(key, None)
        data = {k: v for k, v in data.items() if not denylist.is_denied_json_key(k)}
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, indent=2), encoding="utf-8")


def capture_profile_seed(profile: Profile) -> list[str]:
    """Capture the host's allowlisted ``~/.claude`` assets into the profile's ``seed/`` dir.

    Trees are copied dereferencing symlinks (so seeded skills resolve inside a container, where a
    host-absolute symlink would dangle) and excluding machine-local cruft; ``settings.json`` is
    field-patched. New projects on this profile then inherit these via ``_copy_profile_seed``.
    Returns the list of captured entries.
    """
    src_root = Path(os.path.expanduser(profile.seed.source))
    dest_root = config.profile_seed_dir(profile.name)
    dest_root.mkdir(parents=True, exist_ok=True)
    captured: list[str] = []
    for entry in profile.seed.include:
        rel = entry.rstrip("/")
        if denylist.is_denied_path(rel):
            continue
        src = src_root / rel
        if not src.exists():
            continue
        dst = dest_root / rel
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(
                src, dst, symlinks=False, ignore=_ignore_names,
                dirs_exist_ok=True, ignore_dangling_symlinks=True,
            )
        elif rel == "settings.json":
            _patch_settings(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        captured.append(rel)
    return captured


def _copy_profile_seed(profile: Profile, cfg: Path) -> None:
    """Copy the profile's ``seed/`` include list into ``cfg``, denylist-filtered, never clobbering."""
    seed_dir = config.profile_seed_dir(profile.name)
    if not seed_dir.exists():
        return
    for entry in profile.seed.include:
        rel = entry.rstrip("/")
        if denylist.is_denied_path(rel):
            continue
        src, dst = seed_dir / rel, cfg / rel
        if not src.exists() or dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
