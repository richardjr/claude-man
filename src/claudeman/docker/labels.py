"""The claude-man docker label model.

Labels make ``docker ps`` self-describing, but they are a PROJECTION of the TOML
registry, never the source of truth. Complex data (the repo URL list) stays in
TOML; only a count goes in a label. On divergence the registry wins.
"""

from __future__ import annotations

from .. import agents, config
from ..agents import AgentProvider
from ..registry.schema import Project

# Fully-qualified label keys
SLUG = f"{config.LABEL_PREFIX}.slug"
AGENT = f"{config.LABEL_PREFIX}.agent"       # the provider id (a container self-describes its agent, invariant 4)
PROFILE = f"{config.LABEL_PREFIX}.profile"
OVERLAY = f"{config.LABEL_PREFIX}.overlay"
TOOLS = f"{config.LABEL_PREFIX}.tools"       # the approved-tool selection baked into its image (csv)
EGRESS = f"{config.LABEL_PREFIX}.egress"
AUTH = f"{config.LABEL_PREFIX}.auth"
REPOS = f"{config.LABEL_PREFIX}.repos"
VERSION = f"{config.LABEL_PREFIX}.version"
CREATED = f"{config.LABEL_PREFIX}.created"



def image_version_label(provider: AgentProvider = agents.DEFAULT) -> str:
    """The IMAGE (not container) label carrying the agent version baked at build time (set by the
    provider's Dockerfile fragment — ``claude-man.claude-version`` for claude; overlays inherit it
    from their base). Distinct from VERSION above, which is STAMPED on a container at create time.
    Read off `docker image inspect` to decide the on-start update (images.image_claude_version)."""
    return f"{config.LABEL_PREFIX}.{provider.image.version_label}"


IMAGE_VERSION = image_version_label()   # the default (claude) provider's key

# Filter that selects every claude-man container regardless of slug.
SELECTOR = f"label={SLUG}"


def build(project: Project, *, profile: str, version: str, created_iso: str) -> dict[str, str]:
    """Build the label dict stamped onto a container at create time."""
    return {
        SLUG: project.slug,
        AGENT: project.agent,
        PROFILE: profile,
        OVERLAY: project.overlay,
        TOOLS: ",".join(project.tools),
        EGRESS: project.egress,
        AUTH: project.auth,
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
