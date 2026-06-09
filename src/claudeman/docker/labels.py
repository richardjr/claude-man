"""The claude-man docker label model.

Labels make ``docker ps`` self-describing, but they are a PROJECTION of the TOML
registry, never the source of truth. Complex data (the repo URL list) stays in
TOML; only a count goes in a label. On divergence the registry wins.
"""

from __future__ import annotations

from .. import config
from ..registry.schema import Project

# Fully-qualified label keys
SLUG = f"{config.LABEL_PREFIX}.slug"
PROFILE = f"{config.LABEL_PREFIX}.profile"
OVERLAY = f"{config.LABEL_PREFIX}.overlay"
EGRESS = f"{config.LABEL_PREFIX}.egress"
REPOS = f"{config.LABEL_PREFIX}.repos"
VERSION = f"{config.LABEL_PREFIX}.version"
CREATED = f"{config.LABEL_PREFIX}.created"

# IMAGE (not container) label: the claude version baked at build time (set by images/base/Dockerfile;
# overlays inherit it from their base). Distinct from VERSION above, which is STAMPED on a container at
# create time. Read off `docker image inspect` to decide the on-start update (images.image_claude_version).
IMAGE_VERSION = f"{config.LABEL_PREFIX}.claude-version"

# Filter that selects every claude-man container regardless of slug.
SELECTOR = f"label={SLUG}"


def build(project: Project, *, profile: str, version: str, created_iso: str) -> dict[str, str]:
    """Build the label dict stamped onto a container at create time."""
    return {
        SLUG: project.slug,
        PROFILE: profile,
        OVERLAY: project.overlay,
        EGRESS: project.egress,
        REPOS: str(len(project.repos)),
        VERSION: version,
        CREATED: created_iso,
    }


def to_args(labels: dict[str, str]) -> list[str]:
    """Render a label dict as ``--label k=v`` argv pairs."""
    args: list[str] = []
    for key, value in labels.items():
        args += ["--label", f"{key}={value}"]
    return args


def parse(raw: dict[str, str]) -> dict[str, str]:
    """Pull just the claude-man.* keys out of a container's full label map."""
    prefix = config.LABEL_PREFIX + "."
    return {k: v for k, v in raw.items() if k.startswith(prefix)}
