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


def seed_project_config(project: Project, profile: Profile | None) -> Path:
    """Create + seed ``claude-config/`` for ``project``; returns the host dir path."""
    cfg = config.claude_config_dir(project.slug)
    cfg.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg, 0o700)

    claude_json = cfg / ".claude.json"
    if not claude_json.exists():
        email = profile.account_email if profile else ""
        keep = profile.keep_identity_fields if profile else identity.DEFAULT_KEEP
        oauth = {"emailAddress": email} if email else {}
        stub = identity.build_identity_stub(oauth, keep)
        claude_json.write_text(json.dumps(stub, indent=2), encoding="utf-8")

    if profile is not None:
        _copy_profile_seed(profile, cfg)
    return cfg


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
