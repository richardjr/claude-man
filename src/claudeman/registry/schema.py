"""Dataclasses + validation for the project / profile TOML definitions.

These are plain in-memory representations. Reading is done with stdlib ``tomllib``
(see ``projects.py`` / ``profiles.py``); writing preserves operator comments via
``tomlkit``. The dataclasses validate shape so the rest of the app can rely on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import config

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ValidationError(ValueError):
    """Raised when a TOML definition is malformed."""


@dataclass(frozen=True)
class Repo:
    url: str
    branch: str = "main"
    dir: str = ""  # relative path under workspace/; defaults to the repo name

    def resolved_dir(self) -> str:
        if self.dir:
            return self.dir
        tail = self.url.rstrip("/").rsplit("/", 1)[-1]
        return tail[:-4] if tail.endswith(".git") else tail


@dataclass(frozen=True)
class Project:
    slug: str
    profile: str | None = None          # None -> inherit the default profile
    overlay: str = config.DEFAULT_OVERLAY
    egress: str = config.DEFAULT_EGRESS  # "open" | "strict"
    env: dict[str, str] = field(default_factory=dict)
    env_file: str | None = None
    extra_apt: tuple[str, ...] = ()
    repos: tuple[Repo, ...] = ()
    allowlist: tuple[str, ...] = ()      # extra egress dstdomains (strict mode only)

    def __post_init__(self) -> None:
        if not _SLUG_RE.match(self.slug):
            raise ValidationError(
                f"invalid slug {self.slug!r}: must match {_SLUG_RE.pattern}"
            )
        if self.overlay not in config.OVERLAYS:
            raise ValidationError(
                f"invalid overlay {self.overlay!r}: one of {config.OVERLAYS}"
            )
        if self.egress not in config.EGRESS_MODES:
            raise ValidationError(
                f"invalid egress {self.egress!r}: one of {config.EGRESS_MODES}"
            )
        for k in config.SCRUBBED_ENV_KEYS:
            if k in self.env:
                raise ValidationError(
                    f"env key {k!r} is forbidden (it would outrank the OAuth token); remove it"
                )

    @property
    def image(self) -> str:
        return f"{config.IMAGE_REPO}:{self.overlay}"

    @property
    def container(self) -> str:
        return config.container_name(self.slug)


@dataclass(frozen=True)
class ProfileSeed:
    source: str = "~/.claude"
    include: tuple[str, ...] = (
        "settings.json",
        "agents/",
        "skills/",
        "commands/",
        "plugins/",
    )


@dataclass(frozen=True)
class Profile:
    name: str
    display_name: str = ""
    account_email: str = ""
    default: bool = False
    keep_identity_fields: tuple[str, ...] = (
        "emailAddress",
        "displayName",
        "organizationName",
    )
    seed: ProfileSeed = field(default_factory=ProfileSeed)

    def __post_init__(self) -> None:
        if not _SLUG_RE.match(self.name):
            raise ValidationError(
                f"invalid profile name {self.name!r}: must match {_SLUG_RE.pattern}"
            )
