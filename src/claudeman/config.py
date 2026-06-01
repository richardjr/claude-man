"""Shared constants and XDG path resolution for claude-man.

Two on-disk tiers (see docs/ARCHITECTURE.md):

  * Definitions  -> $XDG_CONFIG_HOME/claude-man   (secret-free, git-versionable TOML)
  * State        -> $XDG_STATE_HOME/claude-man     (durable bytes; some secret; never committed)

Liveness is never stored here; it is read fresh from `docker ps`/`inspect`.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Identity / naming
# ---------------------------------------------------------------------------
APP_NAME = "claude-man"
LABEL_PREFIX = "claude-man"            # docker label namespace: claude-man.<key>
IMAGE_REPO = "claude-man"             # images tagged claude-man:<overlay>
CONTAINER_PREFIX = "claude-man-"      # container name = claude-man-<slug>

DEFAULT_OVERLAY = "base"
DEFAULT_EGRESS = "open"               # "open" | "strict"
OVERLAYS = ("base", "python", "rust", "node")
EGRESS_MODES = ("open", "strict")

# Pinned claude version baked into the image (override per build). Keep in sync
# with images/base/Dockerfile's CLAUDE_VERSION ARG default.
DEFAULT_CLAUDE_VERSION = "2.1.159"

# ---------------------------------------------------------------------------
# Baked container paths (must match images/base/Dockerfile)
# ---------------------------------------------------------------------------
CONTAINER_UID = 1000
CONTAINER_GID = 1000
CONTAINER_HOME = "/home/agent"
CONTAINER_CLAUDE_CONFIG = "/home/agent/.claude"   # = CLAUDE_CONFIG_DIR in the container
CONTAINER_CACHE = "/home/agent/.cache"
CONTAINER_WORKSPACE = "/workspace"

# Env keys that must NEVER be passed into a container (they would silently
# outrank CLAUDE_CODE_OAUTH_TOKEN and can bill the wrong account).
SCRUBBED_ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
def _xdg(env_var: str, default_rel: str) -> Path:
    base = os.environ.get(env_var)
    root = Path(base) if base else Path.home() / default_rel
    return root


def config_home() -> Path:
    """$XDG_CONFIG_HOME/claude-man — definitions (overridable via CLAUDE_MAN_CONFIG_HOME)."""
    override = os.environ.get("CLAUDE_MAN_CONFIG_HOME")
    if override:
        return Path(override)
    return _xdg("XDG_CONFIG_HOME", ".config") / APP_NAME


def state_home() -> Path:
    """$XDG_STATE_HOME/claude-man — durable state (overridable via CLAUDE_MAN_STATE_HOME)."""
    override = os.environ.get("CLAUDE_MAN_STATE_HOME")
    if override:
        return Path(override)
    return _xdg("XDG_STATE_HOME", ".local/state") / APP_NAME


# -- definition (config) tier -----------------------------------------------
def projects_config_dir() -> Path:
    return config_home() / "projects"


def profiles_config_dir() -> Path:
    return config_home() / "profiles"


def project_toml_path(slug: str) -> Path:
    return projects_config_dir() / f"{slug}.toml"


def profile_toml_path(name: str) -> Path:
    return profiles_config_dir() / f"{name}.toml"


# -- state tier --------------------------------------------------------------
def project_state_dir(slug: str) -> Path:
    return state_home() / "projects" / slug


def workspace_dir(slug: str) -> Path:
    """Host dir bind-mounted to /workspace; holds the checked-out repos."""
    return project_state_dir(slug) / "workspace"


def claude_config_dir(slug: str) -> Path:
    """Host dir bind-mounted to the container's CLAUDE_CONFIG_DIR (/home/agent/.claude)."""
    return project_state_dir(slug) / "claude-config"


def baseline_path(slug: str) -> Path:
    """Sync-back 3-way baseline manifest. Sibling of the mount, never inside it."""
    return project_state_dir(slug) / "baseline.json"


def backups_dir(slug: str) -> Path:
    return project_state_dir(slug) / "backups"


def profile_state_dir(name: str) -> Path:
    return state_home() / "profiles" / name


def profile_token_path(name: str) -> Path:
    """0600 file holding the long-lived CLAUDE_CODE_OAUTH_TOKEN for this profile."""
    return profile_state_dir(name) / "token"


def profile_identity_path(name: str) -> Path:
    return profile_state_dir(name) / "identity.json"


def profile_seed_dir(name: str) -> Path:
    """Canonical config seed copied into a new project's claude-config/."""
    return profile_state_dir(name) / "seed"


def sync_audit_dir() -> Path:
    """Git repo: per-session commit of accepted sync-back (free revert log)."""
    return state_home() / "sync-audit"


def container_name(slug: str) -> str:
    return f"{CONTAINER_PREFIX}{slug}"
