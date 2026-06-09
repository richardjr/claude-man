"""Dataclasses + validation for the project / profile TOML definitions.

These are plain in-memory representations. Reading is done with stdlib ``tomllib``
(see ``projects.py`` / ``profiles.py``); writing preserves operator comments via
``tomlkit``. The dataclasses validate shape so the rest of the app can rely on it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .. import config

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ValidationError(ValueError):
    """Raised when a TOML definition is malformed."""


def _validate_subdir(rel: str) -> None:
    """Reject a workspace-relative repo dir that could escape ``workspace/``.

    This is the load-bearing containment guard (review: dir-containment). It runs in
    ``Repo.__post_init__`` — which re-fires on ``dataclasses.replace`` and on every
    ``_parse`` load — so it is the ONE placement no code path can bypass (the CLI/TUI
    add flows, the TOML loader, and ``add_repo`` all construct a ``Repo`` and thus hit
    it). ``clone_one``/``_purge`` re-check the *resolved* path against the workspace root
    as defence-in-depth, but the shape check here is what makes a malicious/typo'd
    ``dir = "../../etc"`` un-saveable in the first place.
    """
    if not rel:
        raise ValidationError("repo dir resolves to an empty path; set an explicit dir=")
    if rel.startswith("~"):
        raise ValidationError(f"repo dir {rel!r} must not start with '~'")
    p = PurePosixPath(rel)
    if p.is_absolute():
        raise ValidationError(f"repo dir {rel!r} must be relative to workspace/, not absolute")
    if any(part == ".." for part in p.parts):
        raise ValidationError(f"repo dir {rel!r} must not contain a '..' component")


_ENV_MOUNT_KINDS = ("ssh", "file", "env")

# A valid POSIX-ish env var name for a kind="env" mount.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Container destinations a file env-mount may NEVER target. Two reasons: (a) it would land inside a
# claude-man-managed mount (the claude-config bind, the workspace bind, the tmpfs) and collide with the
# repos/config sync; (b) — load-bearing — a bind onto /home/agent/.claude/.credentials.json smuggles a
# working credentials file into the container (an invariant-1 attack; the design verifier proved it). A
# file env-mount creates its OWN bind, so it must sit OUTSIDE every managed mount.
# Exact container paths a file env-mount may never target. Bare tmpfs/managed-mount paths are listed
# here AND as prefixes below — a -v at the exact mountpoint shadows the tmpfs (collapsing invariant 2's
# nosuid/size/ephemerality), and a child path lands inside a managed bind.
# `.claude.json` (a SIBLING of the config dir) is inert today only because CLAUDE_CONFIG_DIR relocates
# config into .claude/ — denying it removes that silent dependency.
_MOUNT_FORBIDDEN_DST_EXACT = (
    "/", "/etc", "/usr", "/bin", "/sbin", "/lib",
    config.CONTAINER_HOME,                       # /home/agent (read-only rootfs anchor)
    config.CONTAINER_WORKSPACE,                  # /workspace (would shadow the repos bind)
    config.CONTAINER_CLAUDE_CONFIG,              # /home/agent/.claude (never inject auth)
    config.CONTAINER_CLAUDE_CONFIG + ".json",    # /home/agent/.claude.json (identity sibling)
    config.CONTAINER_CACHE,                      # /home/agent/.cache (bare tmpfs)
    "/tmp",                                       # bare tmpfs
    config.CONTAINER_SSH_DIR,                    # /home/agent/.ssh (ssh tmpfs)
    config.CONTAINER_SSH_AGENT_SOCK,             # /ssh-agent (forwarded socket)
)
# NOTE: /workspace/<subpath> is intentionally ALLOWED (a workspace-root CLAUDE.md is a primary use
# case). gitstate reads the registry, not the workspace filesystem, so a mounted file there doesn't
# pollute it; the only risk — Docker root-creating the nested mountpoint — is handled by
# lifecycle._ensure_workspace_mountpoints pre-creating it operator-owned before create. Bare
# /workspace stays blocked (you can't replace the whole repos bind).
_MOUNT_FORBIDDEN_DST_PREFIXES = (
    config.CONTAINER_CLAUDE_CONFIG + "/",   # /home/agent/.claude/  (config bind; the creds attack)
    config.CONTAINER_CACHE + "/",           # /home/agent/.cache/  (tmpfs)
    config.CONTAINER_SSH_DIR + "/",         # /home/agent/.ssh/  (no binding a private key in — agent-forward)
    config.CONTAINER_HOME + "/.local/",     # /home/agent/.local/  (the baked claude install + launcher)
    "/tmp/",                                 # tmpfs (ephemeral)
)


# Real lowercase container roots an operator would realistically target a FILE at. A dst that
# case-INsensitively matches one but differs in case (e.g. /Workspace for /workspace) is almost
# certainly a typo — Linux paths are case-sensitive, so the file would land at a useless sibling path.
# Longest first; only roots where the corrected suggestion is itself a legal target.
_CONTAINER_ROOTS = (config.CONTAINER_HOME, config.CONTAINER_WORKSPACE, "/home")


def _case_typo_correction(norm: str) -> str | None:
    """If ``norm`` is a wrong-case near-miss of a known container root, return the corrected path."""
    low = norm.lower()
    for root in _CONTAINER_ROOTS:
        if low == root or low.startswith(root + "/"):
            if not (norm == root or norm.startswith(root + "/")):  # case differs in the root segment
                return root + norm[len(root):]
            return None  # correctly-cased under this root — stop
    return None


def _validate_container_dst(dst: str) -> None:
    """Reject a file env-mount container destination that is relative, escapes via '..', a wrong-case
    near-miss of a real container root, or targets a claude-man-managed mount. The file-mount analogue
    of ``_validate_subdir`` — runs in ``EnvMount.__post_init__`` (the one placement every add/load/
    replace path hits).

    POSIX preserves a leading ``//`` (``PurePosixPath('//etc').as_posix() == '//etc'``) which the kernel
    later collapses to ``/etc`` — so the denylist runs against a leading-slash-collapsed ``norm`` or a
    ``//`` prefix would slip the whole check (a verified ``//…/.credentials.json`` bypass).
    """
    if not dst or not dst.strip():
        raise ValidationError("file env mount needs a container path (dst=)")
    p = PurePosixPath(dst)
    if not p.is_absolute():
        raise ValidationError(f"mount dst {dst!r} must be an absolute container path (e.g. /home/agent/.netrc)")
    if any(part == ".." for part in p.parts):
        raise ValidationError(f"mount dst {dst!r} must not contain a '..' component")
    norm = re.sub(r"^/+", "/", p.as_posix())  # collapse a leading // before the denylist checks
    if norm in _MOUNT_FORBIDDEN_DST_EXACT:
        raise ValidationError(f"mount dst {norm!r} is reserved; choose another container path")
    for pre in _MOUNT_FORBIDDEN_DST_PREFIXES:
        if norm.startswith(pre):
            raise ValidationError(
                f"mount dst {norm!r} is inside a claude-man-managed mount ({pre.rstrip('/')}); "
                f"pick a path outside it"
            )
    corrected = _case_typo_correction(norm)
    if corrected is not None:
        raise ValidationError(
            f"mount dst {dst!r} — container paths are case-sensitive; did you mean {corrected!r}?"
        )


@dataclass(frozen=True)
class EnvMount:
    """One environment mount synced into the hardened container (Phase 3.x).

    ``kind="ssh"``: forward the host ssh-agent socket + a writable ~/.ssh tmpfs (keys never enter the
    container; config/known_hosts are seeded post-start). ``kind="file"``: bind a host ``src`` file/dir
    read-only (``ro``, default) — or ``rw`` — at an absolute container ``dst``. PATHS ONLY, never a
    secret value (the definition store stays secret-free / git-versionable).
    """
    kind: str
    src: str = ""          # host path (file kind); "" for ssh/env
    dst: str = ""          # absolute container path (file kind); "" for ssh/env
    ro: bool = True        # file kind: read-only inside the container (default)
    name: str = ""         # env var NAME (env kind); the value lives 0600 in the state tier
    error: str = ""        # set ONLY by lenient() for a load-time-invalid mount (never reaches docker)

    @classmethod
    def lenient(cls, *, kind: str, src: str = "", dst: str = "", ro: bool = True,
                name: str = "") -> EnvMount:
        """Construct from stored TOML WITHOUT raising — an entry that fails validation is returned
        flagged (``error`` set) instead.

        Validation is strict at the ADD boundary (``EnvMount(...)`` raises) but lenient at LOAD: a rule
        tightened after a config was saved (e.g. the case-typo guard) must not crash ``projects.load``.
        A flagged mount stays visible/removable in the UI and round-trips through ``save``, but the
        render + lifecycle SKIP it, so an invalid (even security-invalid) mount never reaches docker.
        """
        try:
            return cls(kind=kind, src=src, dst=dst, ro=ro, name=name)
        except ValidationError as exc:
            obj = cls(kind="ssh")  # a guaranteed-valid placeholder; override fields without re-validating
            for fld, val in (("kind", kind), ("src", src), ("dst", dst), ("ro", ro),
                             ("name", name), ("error", str(exc))):
                object.__setattr__(obj, fld, val)
            return obj

    def __post_init__(self) -> None:
        if self.kind not in _ENV_MOUNT_KINDS:
            raise ValidationError(f"invalid env mount kind {self.kind!r}: one of {_ENV_MOUNT_KINDS}")
        if self.kind == "file":
            if not self.src or not self.src.strip():
                raise ValidationError("file env mount needs a host path (src=)")
            resolved = self.resolved_src()
            if not os.path.isabs(resolved):
                raise ValidationError(
                    f"file env mount src must resolve to an absolute host path, got {resolved!r} "
                    f"(a relative/unexpanded path becomes a docker named volume, not a host bind)"
                )
            # Trailing-slash dst means "into this directory" — append the source basename, like `cp`
            # (so `~/Work/CLAUDE.md` -> `/workspace/` becomes `/workspace/CLAUDE.md`).
            if len(self.dst) > 1 and self.dst.endswith("/"):
                base = os.path.basename(resolved.rstrip("/"))
                if base:
                    object.__setattr__(self, "dst", self.dst.rstrip("/") + "/" + base)
            _validate_container_dst(self.dst)
        elif self.kind == "env":
            name = self.name.strip()
            if not _ENV_NAME_RE.match(name):
                raise ValidationError(
                    f"env mount name {self.name!r} must be a valid env var name (letters/digits/_, "
                    f"not starting with a digit)"
                )
            if config.is_forbidden_env_name(name):
                raise ValidationError(
                    f"env var {name!r} is reserved (it has dedicated handling / would breach auth) — "
                    f"use `config gh-token` for GH_TOKEN; ANTHROPIC_*/the OAuth token are never settable"
                )

    def resolved_src(self) -> str:
        """The host source path with ``~`` and ``$VARS`` expanded (host-side)."""
        return os.path.expanduser(os.path.expandvars(self.src)) if self.src else ""

    @property
    def target(self) -> str:
        """Short identity for list display + remove-matching: a file's dst, an env var's name, or 'ssh'."""
        if self.kind == "file":
            return self.dst
        if self.kind == "env":
            return self.name
        return "ssh"


# Default per-project asset-sync allowlists. CLAUDE.md at the workspace root; skills/agents/commands
# at USER scope (~/.claude). settings.json is intentionally NOT here (machine-local perms/hooks risk)
# and is refused even if hand-added (see assets._assert_claude_allowlist_safe).
DEFAULT_SYNC_WORKSPACE = ("CLAUDE.md",)
DEFAULT_SYNC_CLAUDE = ("skills", "agents", "commands")


@dataclass(frozen=True)
class Sync:
    """Per-project asset-sync allowlists (host-side copy of CLAUDE.md + skills/agents on start/stop).

    ``workspace`` paths sync to/from the ``/workspace`` bind root; ``claude`` paths to/from the
    ``~/.claude`` (claude-config) bind. Entries are relative in-tree paths (a file or a dir); the
    containment guard mirrors ``_validate_subdir`` so a hand-edited ``..``/absolute entry is
    un-saveable. ``enabled=False`` turns sync off for the project entirely.
    """
    enabled: bool = True
    workspace: tuple[str, ...] = DEFAULT_SYNC_WORKSPACE
    claude: tuple[str, ...] = DEFAULT_SYNC_CLAUDE

    def __post_init__(self) -> None:
        for rel in self.workspace + self.claude:
            if not rel or not rel.strip():
                raise ValidationError("sync entry must be a non-empty relative path")
            if rel.startswith(("/", "~")):
                raise ValidationError(f"sync entry {rel!r} must be relative (no leading '/' or '~')")
            if any(part == ".." for part in PurePosixPath(rel).parts):
                raise ValidationError(f"sync entry {rel!r} must not contain a '..' component")


@dataclass(frozen=True)
class Repo:
    url: str
    branch: str = "main"
    dir: str = ""  # relative path under workspace/; defaults to the repo name

    def __post_init__(self) -> None:
        if not self.url or not self.url.strip():
            raise ValidationError("repo url is required")
        _validate_subdir(self.resolved_dir())

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
    claude_version: str = ""             # per-project exact claude pin; "" -> the global channel/pin
    env: dict[str, str] = field(default_factory=dict)
    env_file: str | None = None
    workdir: str = ""                    # where claude/shell launch (else: lone repo's dir, else /workspace)
    extra_apt: tuple[str, ...] = ()
    repos: tuple[Repo, ...] = ()
    env_mount: tuple[EnvMount, ...] = ()  # ssh / file mounts synced into the container
    allowlist: tuple[str, ...] = ()      # extra egress dstdomains (strict mode only)
    sync: Sync = field(default_factory=Sync)  # per-project asset sync (CLAUDE.md + skills/agents)

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
        if config.GH_TOKEN_ENV in self.env:
            raise ValidationError(
                f"env key {config.GH_TOKEN_ENV!r} must not be set in [project.env] — that would store a "
                f"secret in the git-versionable config and leak it into argv. Configure it with "
                f"`claudemanctl config gh-token` instead (injected pass-through from the 0600 state tier)."
            )

    @property
    def image(self) -> str:
        return config.image_tag(self.overlay)

    @property
    def container(self) -> str:
        return config.container_name(self.slug)

    @property
    def launch_workdir(self) -> str:
        """Container dir where ``claude`` / a shell launch (``docker exec -w``).

        Explicit ``workdir`` wins (relative -> under ``/workspace``, absolute -> as-is); else a lone
        repo's checkout dir (so a single-repo project drops you straight into it); else ``/workspace``.
        """
        if self.workdir:
            w = self.workdir
            return w if w.startswith("/") else f"{config.CONTAINER_WORKSPACE}/{w.strip('/')}"
        if len(self.repos) == 1:
            return f"{config.CONTAINER_WORKSPACE}/{self.repos[0].resolved_dir()}"
        return config.CONTAINER_WORKSPACE


@dataclass(frozen=True)
class Settings:
    """Global claude-man settings (``~/.config/claude-man/config.toml``) — the "general features" tier.

    Secret-free and git-versionable like every other definition: ``ssh_keys`` holds host private-key
    PATHS, never key material. claude-man ensures these keys are loaded into the host ssh-agent on
    startup (and on add) so the operator never has to ``ssh-add`` by hand before using claude-man; the
    keys themselves stay host-side (only the agent socket is forwarded into a container). Extend with
    new fields as features land — keep every value a non-secret.
    """
    ssh_keys: tuple[str, ...] = ()       # host private-key paths to ensure-load into the agent
    ssh_auto_load: bool = True           # load them on TUI/CLI startup (and on add)
    git_user_name: str = ""              # git author identity injected into containers ("" -> inherit host)
    git_user_email: str = ""             # "" -> inherit the host's `git config --global user.email`
    # On-start claude-version update: before `up`, GET the tracked channel's latest version and offer to
    # rebuild the project's image to it when it's newer than the baked one (host-side image rebuild —
    # never an in-container write; invariant 2 is untouched). A version pin overrides the channel.
    image_update_check: bool = True      # run the on-start "newer claude available?" check
    claude_channel: str = config.DEFAULT_CLAUDE_CHANNEL  # "latest" | "stable" — which channel to track
    claude_version_pin: str = ""         # exact version pin; "" -> track the channel

    def __post_init__(self) -> None:
        for k in self.ssh_keys:
            if not k or not k.strip():
                raise ValidationError("ssh key path must be a non-empty string")
        if self.claude_channel not in config.CLAUDE_CHANNELS:
            raise ValidationError(
                f"claude_channel {self.claude_channel!r} must be one of {config.CLAUDE_CHANNELS}"
            )


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
