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

import time
import tomllib
from pathlib import Path

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


def save(profile: Profile, *, make_default: bool = False) -> Path:
    """Write a profile definition (comment-preserving via tomlkit).

    When ``make_default`` is set, the ``default`` flag is cleared on every other profile first so
    exactly one profile is ever the default.
    """
    try:
        import tomlkit
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on env
        raise RuntimeError("writing profile TOML requires the 'tomlkit' dependency") from exc

    if make_default:
        _clear_other_defaults(profile.name)

    doc = tomlkit.document()
    p = tomlkit.table()
    p["name"] = profile.name
    if profile.display_name:
        p["display_name"] = profile.display_name
    if profile.account_email:
        p["account_email"] = profile.account_email
    p["default"] = bool(profile.default or make_default)
    if profile.seed.include:
        seed = tomlkit.table()
        seed["source"] = profile.seed.source
        seed["include"] = list(profile.seed.include)
        p["seed"] = seed
    doc["profile"] = p

    path = config.profile_toml_path(profile.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc))
    return path


def _clear_other_defaults(except_name: str) -> None:
    for other in list_profiles():
        if other.name == except_name or not other.default:
            continue
        save(
            Profile(
                name=other.name,
                display_name=other.display_name,
                account_email=other.account_email,
                default=False,
                keep_identity_fields=other.keep_identity_fields,
                seed=other.seed,
            )
        )


def token_age_days(name: str) -> float | None:
    """Days since the profile's token file was last written, or None if no token exists.

    setup-token tokens last ~1 year and cannot self-refresh, so the TUI/CLI warn as this nears
    the cliff — a 401 in a container then means *re-mint* (``profile renew``), not a code bug.
    """
    path = config.profile_token_path(name)
    if not path.exists():
        return None
    return (time.time() - path.stat().st_mtime) / 86400.0


def load_token(name: str) -> str | None:
    """Read the 0600 long-lived OAuth token for a profile, or None if not minted yet.

    Interim Phase-1 auth: the operator runs `claude setup-token` on the host and drops the
    token at ``config.profile_token_path(name)``. Phase 2 mints it via ``profiles.setup_token``.
    """
    path = config.profile_token_path(name)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip() or None


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
