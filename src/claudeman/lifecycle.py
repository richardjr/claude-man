"""Project lifecycle orchestration shared by the CLI and the TUI (Phase 1).

Chains the already-tested building blocks into the create / up / stop flows: resolve the effective
profile, seed the ``claude-config`` dir, host-side clone, then the hardened ``docker create`` and
``start``/``stop``. Pure-stdlib (no textual), so the CLI and tests import it without the TUI.

Persistence is the default: ``stop`` never removes a container, and ``up`` is create-if-needed
(idempotent via ``runner.exists`` — review WIRE-7), so restarts leave the binds untouched.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from . import config
from .checkout import repos
from .docker import runner
from .profiles import seed as seed_mod
from .registry import profiles as profiles_registry
from .registry import projects as projects_registry
from .registry.schema import Profile, Project


@dataclass(frozen=True)
class Result:
    ok: bool
    detail: str


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def effective_profile(project: Project) -> Profile | None:
    """The project's explicit profile, else the default, else None (no profile defined yet)."""
    if project.profile:
        try:
            return profiles_registry.load(project.profile)
        except FileNotFoundError:
            return None
    return profiles_registry.default_profile()


def ensure_created(project: Project) -> Result:
    """Create the hardened container if it doesn't exist (idempotent). Seeds config + clones repos."""
    if runner.exists(project.slug):
        return Result(True, f"{project.container} already exists")

    profile = effective_profile(project)
    profile_name = profile.name if profile else "none"
    token = profiles_registry.load_token(profile.name) if profile else None

    seed_mod.seed_project_config(project, profile)
    clone_failures = [r for r in repos.clone_all(project) if not r.ok] if project.repos else []

    cp = runner.create(project, profile_name=profile_name, token=token, created_iso=_now_iso())
    if cp.returncode != 0:
        return Result(False, f"docker create failed: {cp.stderr.strip() or cp.stdout.strip()}")

    notes = []
    if not token:
        notes.append("no token — in-container `claude` won't authenticate "
                     "(mint one with `claude setup-token` → "
                     f"{config.profile_token_path(profile_name)})" if profile
                     else "no profile/token — define one with `claudemanctl profile add`")
    if clone_failures:
        notes.append(f"{len(clone_failures)} repo(s) failed to clone")
    suffix = ("  [" + "; ".join(notes) + "]") if notes else ""
    return Result(True, f"created {project.container}{suffix}")


def up(project: Project) -> Result:
    """Create-if-needed, then start."""
    created = ensure_created(project)
    if not created.ok:
        return created
    cp = runner.start(project.slug)
    if cp.returncode != 0:
        return Result(False, f"docker start failed: {cp.stderr.strip() or cp.stdout.strip()}")
    prefix = created.detail + "; " if "created" in created.detail else ""
    return Result(True, f"{prefix}started {project.container}")


def stop(slug: str) -> Result:
    """Stop the container (never removes it — persistence is the default)."""
    cp = runner.stop(slug)
    if cp.returncode != 0:
        return Result(False, f"docker stop failed: {cp.stderr.strip() or cp.stdout.strip()}")
    return Result(True, f"stopped {config.container_name(slug)}")


def create_project(
    slug: str,
    *,
    profile: str | None = None,
    overlay: str | None = None,
    egress: str | None = None,
) -> Result:
    """Write (or load) the project definition, then create the container."""
    if projects_registry.exists(slug):
        project = projects_registry.load(slug)
    else:
        project = Project(
            slug=slug,
            profile=profile,
            overlay=overlay or config.DEFAULT_OVERLAY,
            egress=egress or config.DEFAULT_EGRESS,
        )
        projects_registry.save(project)
    return ensure_created(project)
