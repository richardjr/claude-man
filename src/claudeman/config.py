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
DEFAULT_CLAUDE_VERSION = "2.1.160"
# Pinned GitHub CLI version baked into the image (keep in sync with the Dockerfile GH_VERSION ARG).
DEFAULT_GH_VERSION = "2.93.0"

# ---------------------------------------------------------------------------
# Baked container paths (must match images/base/Dockerfile)
# ---------------------------------------------------------------------------
CONTAINER_UID = 1000
CONTAINER_GID = 1000
CONTAINER_HOME = "/home/agent"
CONTAINER_CLAUDE_CONFIG = "/home/agent/.claude"   # = CLAUDE_CONFIG_DIR in the container
CONTAINER_CACHE = "/home/agent/.cache"
CONTAINER_STATE = "/home/agent/.cache/state"      # XDG_STATE_HOME (under the writable .cache tmpfs)
CONTAINER_WORKSPACE = "/workspace"
CONTAINER_SSH_DIR = "/home/agent/.ssh"            # ssh-conditional writable tmpfs (known_hosts/config)
CONTAINER_SSH_AGENT_SOCK = "/ssh-agent"           # forwarded host ssh-agent socket (path, not a secret)
# Redirect git's global config + gh's config onto the writable .cache tmpfs — the rootfs is read-only,
# so the default ~/.gitconfig / ~/.config/gh are not writable and `git config --global` / `gh` would
# fail ("could not lock config file … Read-only file system"). Identity itself is injected via
# GIT_CONFIG_COUNT env (no file needed); these just make `git config --global` / `gh auth` work too.
CONTAINER_GITCONFIG = CONTAINER_CACHE + "/gitconfig"   # GIT_CONFIG_GLOBAL
CONTAINER_GH_CONFIG = CONTAINER_CACHE + "/gh"          # GH_CONFIG_DIR

# Env keys that must NEVER be passed into a container (they would silently
# outrank CLAUDE_CODE_OAUTH_TOKEN and can bill the wrong account).
SCRUBBED_ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# The optional GitHub token's env name. Injected ONLY from the dedicated state-tier store
# (gh_token.py) as a pass-through; it must never be sourced from project.env / env_file / the host
# env (so it can't leak into argv or land in the secret-free config.toml). See CLAUDE.md invariant 1.
GH_TOKEN_ENV = "GH_TOKEN"

# ---------------------------------------------------------------------------
# Subscription usage endpoint (the 5-hour + weekly windows Claude Code's /usage shows)
# ---------------------------------------------------------------------------
# GET this with a profile's OAuth bearer token to read per-account 5h/weekly utilization.
OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_USAGE_BETA = "oauth-2025-04-20"
# Mint scopes that enable /api/oauth/usage. `claude setup-token` defaults to `user:inference` only,
# which 403s on the usage endpoint; minting with `user:profile user:inference` (via the
# CLAUDE_CODE_OAUTH_SCOPES env var) lets the same token also read subscription usage.
OAUTH_USAGE_SCOPES = "user:profile user:inference"
# The usage endpoint rate-limits a generic User-Agent aggressively; send the claude-code UA.
CLAUDE_CODE_USER_AGENT = f"claude-code/{DEFAULT_CLAUDE_VERSION}"


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
def settings_toml_path() -> Path:
    """Global claude-man settings (general features + ssh keys) — secret-free, git-versionable."""
    return config_home() / "config.toml"


def projects_config_dir() -> Path:
    return config_home() / "projects"


def profiles_config_dir() -> Path:
    return config_home() / "profiles"


def assets_config_dir() -> Path:
    """Per-project synced-asset SOURCE root (config tier).

    Lives under ``config_home()`` — NOT the state tier — so it rides the operator's external
    ``~/.config/claude-man`` sync across machines. Holds only non-secret content (CLAUDE.md +
    skills/agents/commands); the asset-sync denylist gate keeps credentials/session state out.
    """
    return config_home() / "assets"


def project_assets_dir(slug: str) -> Path:
    return assets_config_dir() / slug


def project_assets_workspace_dir(slug: str) -> Path:
    """Asset subtree synced to/from the ``/workspace`` bind root (e.g. a project CLAUDE.md)."""
    return project_assets_dir(slug) / "workspace"


def project_assets_claude_dir(slug: str) -> Path:
    """Asset subtree synced to/from the ``~/.claude`` (claude-config) bind (skills/agents/commands)."""
    return project_assets_dir(slug) / "claude"


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


def gh_token_path() -> Path:
    """0600 file holding the optional GitHub token injected as ``GH_TOKEN`` into every container.

    State tier (NEVER the secret-free ``config.toml``, never synced). Global / opt-in: absent unless
    the operator sets one via ``config gh-token``. Mirrors ``profile_token_path``'s 0600 model."""
    return state_home() / "gh-token"


def managed_ssh_agent_sock() -> Path:
    """Stable socket for a claude-man-managed ssh-agent (used only when no session agent exists).

    A fixed path so the agent survives across TUI restarts and a recreated container can re-forward it.
    Holds no secret (a unix socket); the keys live in the agent process, never on disk here."""
    return state_home() / "ssh-agent.sock"


def container_name(slug: str) -> str:
    return f"{CONTAINER_PREFIX}{slug}"


# ---------------------------------------------------------------------------
# Repo / image-build paths (package-relative so an auto-build triggered from the
# TUI resolves the Dockerfiles regardless of the process CWD; the CLI used to rely
# on being run from the checkout root).
# ---------------------------------------------------------------------------
def repo_root() -> Path:
    """The claude-man checkout root — holds ``images/`` and ``src/``.

    config.py lives at ``src/claudeman/config.py``; ``parents[2]`` is the checkout root.
    """
    return Path(__file__).resolve().parents[2]


def image_tag(overlay: str) -> str:
    """The local docker tag for an overlay (``claude-man:<overlay>``)."""
    return f"{IMAGE_REPO}:{overlay}"


def image_dockerfile(overlay: str) -> Path:
    """Absolute path to the Dockerfile that builds ``claude-man:<overlay>``."""
    if overlay == "base":
        return repo_root() / "images" / "base" / "Dockerfile"
    return repo_root() / "images" / "overlays" / f"{overlay}.Dockerfile"
