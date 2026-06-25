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

# `docker stop` grace before SIGKILL. The baked CMD is `sleep infinity` as PID 1, which ignores
# SIGTERM (the kernel applies no default disposition to PID 1), so `docker stop` ALWAYS waits the
# full grace then SIGKILLs — a 10s default makes stop/quit feel frozen. A short grace bounds it.
DOCKER_STOP_GRACE_S = 2
OVERLAYS = ("base", "python", "rust", "node", "python-node", "terraform")
EGRESS_MODES = ("open", "strict")

# ---------------------------------------------------------------------------
# Strict egress (Phase 4) — a squid proxy sidecar on a no-direct-route internal network.
# `--cap-drop ALL` forbids in-container iptables (CLAUDE.md invariant 3), so the firewall lives at
# the network layer: the agent runs on a per-project `--internal` network with NO route out, and the
# only path to the internet is the squid sidecar (CONNECT-tunnel allowlist, no MITM). The proxy is
# trusted infra (our fixed image + rendered config, no agent code) so it is NOT the hardened sandbox —
# the AGENT keeps the full floor; only its egress is constrained.
# ---------------------------------------------------------------------------
PROXY_IMAGE = "proxy"                 # built as claude-man:proxy from images/proxy/Dockerfile
PROXY_PORT = 3128                     # squid http_port — the agent's HTTP(S)_PROXY target
EGRESS_NET_PREFIX = "claude-man-net-"          # per-project internal network: claude-man-net-<slug>
PROXY_CONTAINER_PREFIX = "claude-man-proxy-"   # per-project squid sidecar: claude-man-proxy-<slug>
# Buildable image tags: the project overlays + the standalone proxy sidecar (not an overlay of base).
BUILDABLE_IMAGES = OVERLAYS + (PROXY_IMAGE,)

# FALLBACK claude version for image builds — used only when the channel can't be resolved (offline /
# unreadable config; see lifecycle.resolve_build_version). A bare `image build` and the on-start
# update check both resolve the configured pin / tracked channel first, so this is never the normal
# path. Keep in sync with images/base/Dockerfile's CLAUDE_VERSION ARG default.
DEFAULT_CLAUDE_VERSION = "2.1.173"
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
# Per-session scratch / data-transfer dir, a SUBDIR of the /workspace bind (NOT a new mount — so the
# hardened floor is byte-identical, invariant 2). A known drop-zone for moving files in/out of the
# container; lifecycle WIPES it on every start and stop, so it never persists. The injected CLAUDE.md
# note points the agent here for "provided files" (scratch.py).
SCRATCH_DIRNAME = "scratch"
CONTAINER_SCRATCH = CONTAINER_WORKSPACE + "/" + SCRATCH_DIRNAME
# Optional persistent shell-history bind (opt-in via `config shell-history on`): a per-project
# state-tier dir bound here read-write so $HISTFILE survives recreate. Dedicated path (NOT under the
# read-only ~/.local/bin claude install, NOT the XDG_STATE tmpfs) — the ONLY writable surface beyond
# the hardened floor, and only when the operator opts in (default off → byte-identical floor).
CONTAINER_SHELL_HISTORY_DIR = "/home/agent/.persistent-history"
CONTAINER_SSH_DIR = "/home/agent/.ssh"            # ssh-conditional writable tmpfs (known_hosts/config)
CONTAINER_SSH_AGENT_SOCK = "/ssh-agent"           # forwarded host ssh-agent socket (path, not a secret)
# Redirect git's global config + gh's config onto the writable .cache tmpfs — the rootfs is read-only,
# so the default ~/.gitconfig / ~/.config/gh are not writable and `git config --global` / `gh` would
# fail ("could not lock config file … Read-only file system"). Identity itself is injected via
# GIT_CONFIG_COUNT env (no file needed); these just make `git config --global` / `gh auth` work too.
CONTAINER_GITCONFIG = CONTAINER_CACHE + "/gitconfig"   # GIT_CONFIG_GLOBAL
CONTAINER_GH_CONFIG = CONTAINER_CACHE + "/gh"          # GH_CONFIG_DIR
# Yarn (Berry/corepack) defaults its global folder to ~/.yarn — EROFS under the read-only rootfs
# (the `mkdir /home/agent/.yarn` failure). Redirect the small global folder (metadata/telemetry) to
# the writable .cache tmpfs; the bulk package cache goes to the disk-backed workspace bind via
# CONTAINER_YARN_CACHE below (was project-local; now a shared disk cache Yarn Classic v1 honours too).
CONTAINER_YARN_GLOBAL = CONTAINER_CACHE + "/yarn"      # YARN_GLOBAL_FOLDER (Berry)
# Yarn Classic (v1) IGNORES the Berry vars above and uses its own defaults: it caches to ~/.cache/yarn
# (the 256m .cache tmpfs -> ENOSPC on a large install) and writes ~/.yarnrc (read-only rootfs -> EROFS).
# Point its cache at the disk-backed /workspace bind (hundreds of GB, persistent); the image symlinks
# ~/.yarnrc onto the writable .cache tmpfs (see images/base/Dockerfile). Berry honours YARN_CACHE_FOLDER
# too, so both yarns share this one disk-backed cache (still on the persistent workspace bind).
CONTAINER_YARN_CACHE = CONTAINER_WORKSPACE + "/.yarn-cache"   # YARN_CACHE_FOLDER (Yarn v1 + Berry)
# pip / uv write to HOME dotdirs that sit on the read-only rootfs: pip's cache + uv's cache (~/.cache,
# the size-capped 256m tmpfs that ENOSPC'd a large yarn install) AND uv's downloaded interpreters + tool
# venvs (~/.local/share/uv — read-only -> `uv sync` dies with EROFS creating ~/.local/share/uv/python).
# Redirect every pip/uv dir onto the disk-backed, persistent /workspace bind, same philosophy as
# YARN_CACHE_FOLDER. Env-only, additive (no new writable surface — they ride the existing /workspace
# bind), so the hardened floor is byte-identical.
CONTAINER_PIP_CACHE = CONTAINER_WORKSPACE + "/.pip-cache"      # PIP_CACHE_DIR
CONTAINER_UV_DIR = CONTAINER_WORKSPACE + "/.uv"               # uv's home on the disk-backed bind
CONTAINER_UV_CACHE = CONTAINER_UV_DIR + "/cache"             # UV_CACHE_DIR
CONTAINER_UV_PYTHON = CONTAINER_UV_DIR + "/python"           # UV_PYTHON_INSTALL_DIR (downloaded interpreters)
CONTAINER_UV_BIN = CONTAINER_UV_DIR + "/bin"                # UV_PYTHON_BIN_DIR + UV_TOOL_BIN_DIR (exe links)
CONTAINER_UV_TOOLS = CONTAINER_UV_DIR + "/tools"            # UV_TOOL_DIR (tool venvs)

# Env keys that must NEVER be passed into a container (they would silently
# outrank CLAUDE_CODE_OAUTH_TOKEN and can bill the wrong account).
SCRUBBED_ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# The optional GitHub token's env name. Injected ONLY from the dedicated state-tier store
# (gh_token.py) as a pass-through; it must never be sourced from project.env / env_file / the host
# env (so it can't leak into argv or land in the secret-free config.toml). See CLAUDE.md invariant 1.
GH_TOKEN_ENV = "GH_TOKEN"
# The Claude OAuth token env name (the one secret auth var). Single source of truth; runner re-exports.
OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

# Env-var NAMES an operator-supplied value (a `[[project.env_mount]]` kind="env", or project.env) may
# NEVER use: the scrubbed keys (misbill), the OAuth token, and GH_TOKEN — each has dedicated, sole-
# sourced handling, so letting an arbitrary env var shadow them would breach invariant 1.
FORBIDDEN_ENV_NAMES = SCRUBBED_ENV_KEYS + (OAUTH_TOKEN_ENV, GH_TOKEN_ENV)


def is_forbidden_env_name(name: str) -> bool:
    """True if ``name`` is a reserved/auth env var — incl. case + underscore-padding variants.

    Env var names are case-sensitive, so a plain ``in`` check lets ``gh_token`` / ``_GH_TOKEN`` /
    ``GH_TOKEN_`` slip past the exact-match guard. Normalising (strip leading/trailing ``_`` + upper)
    rejects those padding/case variants of a reserved name while still allowing genuinely-different
    names like ``GH_TOKEN_BACKUP`` or ``MY_GH_TOKEN``."""
    norm = name.strip("_").upper()
    return any(norm == f.strip("_").upper() for f in FORBIDDEN_ENV_NAMES)

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
# Claude Code release channel (the on-start "is a newer claude available?" check)
# ---------------------------------------------------------------------------
# A plain-text version pointer per channel — the SAME endpoint claude's native installer reads
# (``install.sh`` GETs ``…/latest`` to learn the version). A cheap, TOKEN-LESS GET that reads no quota.
# claude-man compares the channel version to the image's baked ``claude-man.claude-version`` label and
# offers a host-side image REBUILD before start (never an in-container write — the ``~/.local`` install
# is on the read-only rootfs, which is why ``claude update`` fails inside a container). See updates.py.
RELEASES_BASE_URL = "https://downloads.claude.ai/claude-code-releases"
CLAUDE_CHANNELS = ("latest", "stable")
DEFAULT_CLAUDE_CHANNEL = "latest"


# ---------------------------------------------------------------------------
# Local model backend (Phase 9 — the dynamic model-management framework, models/)
# ---------------------------------------------------------------------------
# Ollama runs as a HOST daemon (default 127.0.0.1:11434, NO auth). claude-man's model management
# (`claudemanctl model …`) drives its HTTP API host-side via stdlib urllib — installing Ollama itself is
# a documented host PREREQUISITE (see docs/MODELS.md), claude-man manages the MODELS within it. The
# registry endpoint is the token-less manifest probe used to detect a newer model build WITHOUT a
# multi-GB pull (the updates.py shape). The daemon URL is overridable via CLAUDE_MAN_OLLAMA_URL
# (secret-free — Ollama carries no token).
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"
OLLAMA_REGISTRY_URL = "https://registry.ollama.ai"   # /v2/library/<model>/manifests/<tag> -> manifest digest
OLLAMA_URL_ENV = "CLAUDE_MAN_OLLAMA_URL"


def ollama_url() -> str:
    """The host Ollama daemon base URL (env override, else the 127.0.0.1 default). No trailing slash."""
    return (os.environ.get(OLLAMA_URL_ENV) or OLLAMA_DEFAULT_URL).rstrip("/")


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


def scratch_dir(slug: str) -> Path:
    """Per-session scratch / data-transfer dir inside the /workspace bind (== CONTAINER_SCRATCH).

    A subdir of the existing workspace bind — NOT a new mount, so the hardened floor (invariant 2) is
    unchanged. ``lifecycle`` wipes + recreates it on every container start/stop, so anything dropped
    here is ephemeral; durable work belongs in a repo under workspace/."""
    return workspace_dir(slug) / SCRATCH_DIRNAME


def claude_config_dir(slug: str) -> Path:
    """Host dir bind-mounted to the container's CLAUDE_CONFIG_DIR (/home/agent/.claude)."""
    return project_state_dir(slug) / "claude-config"


def baseline_path(slug: str) -> Path:
    """Sync-back 3-way baseline manifest. Sibling of the mount, never inside it."""
    return project_state_dir(slug) / "baseline.json"


def project_env_path(slug: str) -> Path:
    """0600 JSON ({NAME: value}) holding a project's `kind="env"` env-mount VALUES.

    State tier (never the secret-free config.toml, never synced) — the registry stores only the
    NAMES; values are injected pass-through (`-e NAME`), like GH_TOKEN. See CLAUDE.md invariant 1."""
    return project_state_dir(slug) / "env.json"


def backups_dir(slug: str) -> Path:
    return project_state_dir(slug) / "backups"


def project_shell_dir(slug: str) -> Path:
    """Host dir bound read-write to ``CONTAINER_SHELL_HISTORY_DIR`` when persistent shell history is
    enabled (``config shell-history on``). State tier (holds only the operator's bash history, no
    secret); created 0700 owned by the current uid so the in-container agent (uid 1000) can write it."""
    return project_state_dir(slug) / "shell"


def packs_manifest_path(slug: str) -> Path:
    """JSON manifest of the pack-managed asset paths + content hashes (state tier, never synced).

    What separates "ours to re-stamp" from operator-authored files: deselecting a pack removes
    exactly the manifest-listed paths, and an existing un-manifested file is never overwritten
    (operator wins). See docs/PACKS.md."""
    return project_state_dir(slug) / "packs-manifest.json"


def profile_state_dir(name: str) -> Path:
    return state_home() / "profiles" / name


def profile_token_path(name: str) -> Path:
    """0600 file holding the long-lived CLAUDE_CODE_OAUTH_TOKEN for this profile."""
    return profile_state_dir(name) / "token"


def profile_seed_dir(name: str) -> Path:
    """Canonical config seed copied into a new project's claude-config/."""
    return profile_state_dir(name) / "seed"


def sync_audit_dir() -> Path:
    """Git repo: per-session commit of accepted sync-back (free revert log)."""
    return state_home() / "sync-audit"


def syncback_lock_path() -> Path:
    """GLOBAL advisory lock serialising sync-back merges.

    The merge TARGET is the operator's singular host ``~/.claude`` (one config regardless of profile),
    so the lock is global — NOT per-project/per-profile. Lives in the state tier (holds no secret)."""
    return state_home() / ".syncback-merge.lock"


def gh_token_path() -> Path:
    """0600 file holding the optional GitHub token injected as ``GH_TOKEN`` into every container.

    State tier (NEVER the secret-free ``config.toml``, never synced). Global / opt-in: absent unless
    the operator sets one via ``config gh-token``. Mirrors ``profile_token_path``'s 0600 model."""
    return state_home() / "gh-token"


def write_secret_file(path: Path, data: str | bytes) -> None:
    """Write a ``0600`` secret file under a ``0700`` parent, with NO world-readable window.

    One audited implementation for every state-tier secret store (the OAuth token, ``GH_TOKEN``, the
    per-project env values). Create the parent ``0700``, open ``O_CREAT|O_TRUNC`` at ``0600`` so the
    file is never briefly group/world-readable between write and chmod (a ``write_text`` then
    ``chmod`` would leave that gap), write, then re-chmod ``0600`` to tighten a *pre-existing* file
    (``O_CREAT`` keeps an existing file's mode)."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def managed_ssh_agent_sock() -> Path:
    """Stable socket for a claude-man-managed ssh-agent (used only when no session agent exists).

    A fixed path so the agent survives across TUI restarts and a recreated container can re-forward it.
    Holds no secret (a unix socket); the keys live in the agent process, never on disk here."""
    return state_home() / "ssh-agent.sock"


def container_name(slug: str) -> str:
    return f"{CONTAINER_PREFIX}{slug}"


def egress_net_name(slug: str) -> str:
    """The per-project ``--internal`` docker network the agent attaches to under strict egress."""
    return f"{EGRESS_NET_PREFIX}{slug}"


def proxy_container_name(slug: str) -> str:
    """The per-project squid sidecar container name (also its in-network DNS name for HTTP(S)_PROXY)."""
    return f"{PROXY_CONTAINER_PREFIX}{slug}"


def squid_conf_path(slug: str) -> Path:
    """Host path of the rendered squid.conf (bind-mounted read-only into the sidecar). State tier —
    derived from the registry allowlist, holds no secret."""
    return project_state_dir(slug) / "squid.conf"


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


def library_packs_dir() -> Path:
    """The curated pack library shipped in this repo (``library/packs/<tier>/<pack>/``).

    Repo-relative like ``images/`` — the operator runs claude-man from the checkout, and curation
    is "edit the file, commit". See docs/PACKS.md."""
    return repo_root() / "library" / "packs"


def image_tag(overlay: str) -> str:
    """The local docker tag for an overlay (``claude-man:<overlay>``)."""
    return f"{IMAGE_REPO}:{overlay}"


def image_dockerfile(overlay: str) -> Path:
    """Absolute path to the Dockerfile that builds ``claude-man:<overlay>``."""
    if overlay == "base":
        return repo_root() / "images" / "base" / "Dockerfile"
    if overlay == PROXY_IMAGE:
        # The strict-egress squid sidecar — standalone (NOT FROM claude-man:base), so it has its own
        # top-level dir rather than living under overlays/.
        return repo_root() / "images" / "proxy" / "Dockerfile"
    return repo_root() / "images" / "overlays" / f"{overlay}.Dockerfile"
