"""Load / list profile definitions and resolve the effective profile for a project.

Canonical TOML shape (see templates/profile.toml.example)::

    [profile]
    name = "work"
    display_name = "Work (3ADAPT)"
    account_email = "richard@3adapt.example"
    default = false

    [profile.seed]
    source = "~/.claude"
    include = ["settings.json", "agents/", "skills/", "commands/", "plugins/"]

    [profile.scrub]
    keep_identity_fields = ["emailAddress", "displayName", "organizationName"]
"""

from __future__ import annotations

import tomllib

from .. import config
from .schema import Profile, ProfileSeed, ValidationError


def _parse(data: dict, name_hint: str | None = None) -> Profile:
    p = data.get("profile")
    if not isinstance(p, dict):
        raise ValidationError("missing [profile] table")
    name = p.get("name", name_hint)
    if not name:
        raise ValidationError("profile.name is required")
    seed_tbl = p.get("seed", {}) or {}
    scrub_tbl = p.get("scrub", {}) or {}
    seed = ProfileSeed(
        source=seed_tbl.get("source", "~/.claude"),
        include=tuple(seed_tbl.get("include", ProfileSeed().include)),
    )
    return Profile(
        name=name,
        display_name=p.get("display_name", ""),
        account_email=p.get("account_email", ""),
        default=bool(p.get("default", False)),
        keep_identity_fields=tuple(
            scrub_tbl.get("keep_identity_fields", Profile(name=name).keep_identity_fields)
        ),
        seed=seed,
    )


def load(name: str) -> Profile:
    path = config.profile_toml_path(name)
    if not path.exists():
        raise FileNotFoundError(f"no profile {name!r} at {path}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return _parse(data, name_hint=name)


def list_names() -> list[str]:
    d = config.profiles_config_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.toml"))


def list_profiles() -> list[Profile]:
    out: list[Profile] = []
    for name in list_names():
        try:
            out.append(load(name))
        except (ValidationError, FileNotFoundError):
            continue
    return out


def default_profile() -> Profile | None:
    profiles = list_profiles()
    for p in profiles:
        if p.default:
            return p
    return profiles[0] if profiles else None


def resolve_for_project(project) -> Profile:
    """Effective profile: the project's explicit choice, else the default."""
    if project.profile:
        return load(project.profile)
    d = default_profile()
    if d is None:
        raise ValidationError(
            f"project {project.slug!r} has no profile and no default profile is defined; "
            f"run `claudemanctl profile add <name> --default`"
        )
    return d
