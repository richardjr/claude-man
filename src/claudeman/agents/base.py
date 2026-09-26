"""The agent-provider seam — PURE value objects (Phase 7a, docs/AGENTS.md).

claude-man's security floor is agent-agnostic: the hardened ``docker create`` argv, the egress
sidecar, the labels, ports and env-mount plumbing know nothing about the binary inside. What IS
agent-specific clusters in a handful of seams — which program to exec, where its config dir lives
and which env var points at it, the auth env var, the image version build-arg + label, the release
pointer for the on-start update check, and the egress hosts a LOCKED container must always reach.
An ``AgentProvider`` owns exactly that policy DATA; every consumer resolves the provider through
``agents.resolve`` (the ``hostplatform.py`` "all branches go through here" discipline) instead of
hard-coding ``"claude"``.

Load-bearing principle (docs/AGENTS.md): a provider parameterises BEHAVIOUR, never SECURITY.
Invariants 1–3 are enforced BY the layer for every provider — no provider field can relax the
floor, copy a host credential in, or inject a mis-billing key.

Pure stdlib, no IO, no textual — importable by the CLI, lifecycle and the dependency-free tests.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

# A /proc ``comm`` name is at most 15 bytes and the probe interpolates it into a ``sh -c`` string,
# so it must be a plain identifier-ish token — never anything the shell could interpret.
_COMM_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


@dataclass(frozen=True)
class AuthSpec:
    """The auth seam (invariant 1). ``token_env`` is the ONE env var the layer injects pass-through
    (value via the child env, never argv) when the project runs in ``token`` auth mode.
    ``scrub_env`` are keys that would silently outrank it and mis-bill — never rendered from
    ``project.env`` / ``env_file`` / env-mounts. ``credential_file`` (relative to the config dir) is
    the agent's self-minted, self-refreshing credential: never copied in from the host, denylisted
    from sync-back, unlinked on a forced identity re-seed."""
    token_env: str
    scrub_env: tuple[str, ...]
    credential_file: str
    identity_file: str          # the onboarding/identity stub the seed writes (e.g. ".claude.json");
    #                             "" = the provider has no identity stub (no seed, no up-time verify)
    token_kind: str = "oauth-token"   # "oauth-token" (minted by the agent's own setup flow on the
    #                                    host) | "api-key" (pasted; billed at API rates)
    login_hint: str = ""        # operator instruction to mint the login-mode credential IN-CONTAINER
    #                             ("{slug}" is substituted); the credential lands in the config bind
    token_hint: str = ""        # operator instruction to mint the token-mode credential on the HOST

    def __post_init__(self) -> None:
        if self.token_kind not in ("oauth-token", "api-key"):
            raise ValueError(f"token_kind {self.token_kind!r} must be oauth-token | api-key")


@dataclass(frozen=True)
class ImageSpec:
    """The image-bake seam: the build-arg carrying the agent version into the Dockerfile and the
    IMAGE label key (``<LABEL_PREFIX>.<version_label>``) the build stamps + ``image_version`` reads."""
    version_build_arg: str
    version_label: str
    default_version: str
    tools: tuple[str, ...] = ()   # approved-tool registry entries that INSTALL this agent — pulled
    #                               into every project of this agent automatically (codex: ("codex",));
    #                               () = baked into the base image (claude)


@dataclass(frozen=True)
class UpdateSpec:
    """The on-start update seam: a token-less GET of ``<releases_url>/<channel>`` returns the
    channel's newest version (updates.py). ``user_agent`` because the Anthropic endpoint rate-limits
    a generic UA harder."""
    releases_url: str
    channels: tuple[str, ...]
    default_channel: str
    user_agent: str


PERMISSIONS = ("default", "edits", "full")   # headless-run permission levels (RunRequest.permission)


@dataclass(frozen=True)
class RunRequest:
    """ONE non-interactive agent session (the Phase 7-run headless seam; docs/V2-PLAN.md §4).

    ``prompt`` is delivered on the agent's STDIN (never argv — no length limit, no `ps` exposure).
    ``permission``: ``default`` = the agent's own headless default (tool calls needing approval are
    refused), ``edits`` = auto-accept file edits, ``full`` = every tool call auto-approved — INSIDE the
    hardened container, which is the actual sandbox (invariant 2; a provider whose own sandbox can't
    run under the floor, e.g. codex/bubblewrap, disables it here). ``resume`` continues a prior session
    by the provider's session id; ``model`` is the launch pin (claude ``--model``)."""
    prompt: str
    permission: str = "default"
    resume: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("a run needs a non-empty prompt")
        if self.permission not in PERMISSIONS:
            raise ValueError(f"permission {self.permission!r} must be one of {PERMISSIONS}")


@dataclass(frozen=True)
class AgentEvent:
    """A provider-NEUTRAL headless-run event (the normalised form of claude's stream-json /
    codex's exec JSONL). ``kind``: started | message | tool_use | tool_result | notice |
    turn_done | failed. ``usage`` = input/output/cache_read/cache_creation token counts when the
    provider reports them (turn_done). ``raw`` keeps the source record for `--json` / debugging."""
    kind: str
    text: str = ""
    session_id: str = ""
    tool: str = ""
    ok: bool = True
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RunSpec:
    """How a provider runs ONE headless session: ``argv(request)`` renders the in-container command
    (the prompt goes on stdin, so argv never carries it) and ``parse(record)`` turns one decoded JSON
    line of its output stream into zero or more ``AgentEvent``s. Both PURE (agents/run.py)."""
    argv: Callable[[RunRequest], tuple[str, ...]]
    parse: Callable[[dict], tuple[AgentEvent, ...]]


@dataclass(frozen=True)
class SyncbackPolicy:
    """The sync-back seam (invariant 5; docs/AGENTS.md seam 7): the policy DATA the Phase-5 engine
    applies for one provider — WHICH files/keys in the config dir are secret or machine-local
    (``deny_paths`` names/globs matched at ANY depth; ``deny_json_keys`` + prefixes), WHICH
    artifacts are syncable (``(rel, kind)`` — kinds tree | tree-symlink | json-keys | mcp | file;
    ``__mcp__`` is the narrow MCP read), WHERE they land on the host (``host_dir``), and the
    settings/MCP files (``""`` = the provider has none). The 3-way merge, masking, backup-first,
    flock and audit-commit are the engine's and identical for every provider."""
    host_dir: str
    deny_paths: tuple[str, ...]
    artifacts: tuple[tuple[str, str], ...]
    settings_file: str = ""          # the json-keys artifact's file name (claude: settings.json)
    mcp_file: str = ""               # the identity file read NARROWLY for mcpServers in the bind
    mcp_host_file: str = ""          # … and its host-side twin ("~/.claude.json")
    deny_json_keys: tuple[str, ...] = ()
    deny_json_key_prefixes: tuple[str, ...] = ()
    immune_keys: tuple[str, ...] = ()   # settings keys a merge never overwrites (host-structural)

    def __post_init__(self) -> None:
        if not self.host_dir.startswith("~/"):
            raise ValueError("SyncbackPolicy.host_dir must be a ~/-relative host path")
        for rel, kind in self.artifacts:
            if kind not in ("tree", "tree-symlink", "json-keys", "mcp", "file"):
                raise ValueError(f"artifact {rel!r}: unknown kind {kind!r}")
            if kind == "json-keys" and rel != self.settings_file:
                raise ValueError("a json-keys artifact must be the policy's settings_file")
            if kind == "mcp" and not (self.mcp_file and self.mcp_host_file):
                raise ValueError("an mcp artifact needs mcp_file + mcp_host_file")


@dataclass(frozen=True)
class ContextSpec:
    """The context-file seam (seam 8): the project-instructions file the agent reads at the
    workspace root, whether pack fragments are LINKED into it (claude's ``@path`` imports) or must
    be INLINED (codex reads a plain AGENTS.md), and which config-dir asset trees are syncable
    (the assets default-DENY allowlist)."""
    file: str = "CLAUDE.md"
    link: bool = True
    config_entries: tuple[str, ...] = ("skills", "agents", "commands")

    def __post_init__(self) -> None:
        if not re.match(r"^[A-Za-z][A-Za-z0-9._-]*\.md$", self.file):
            raise ValueError(f"context file {self.file!r} must be a plain .md basename")


@dataclass(frozen=True)
class AgentProvider:
    id: str                       # "claude" | (7c) "codex" | …
    display_name: str
    binary: str                   # the in-container program to exec
    proc_comm: str                # the /proc comm name the one-per-container probe looks for
    config_dir: str               # in-container config dir (a managed writable bind)
    config_dir_env: str           # the env var that points the agent at ``config_dir``
    auth: AuthSpec
    image: ImageSpec
    updates: UpdateSpec | None    # None -> the provider has no release-pointer update check
    required_hosts: tuple[str, ...]   # squid dstdomains a LOCKED container must always allow
    run: RunSpec | None = None    # the headless-run seam (7-run); None -> no non-interactive mode
    config_seed: tuple[tuple[str, str], ...] = ()   # (relpath, content) files written into the config
    #                               bind at seed time IF ABSENT (codex: config.toml with the sandbox
    #                               off + file credential store); never overwrites operator edits
    syncback: SyncbackPolicy | None = None   # the sync-back policy; None -> no sync-back for this
    #                               provider at all (no baseline, no pending note, `sync plan/review`
    #                               refused — never a silent partial sync)
    context: ContextSpec = field(default_factory=ContextSpec)   # the context-file seam

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.id):
            raise ValueError(f"provider id {self.id!r} must be a short lowercase slug")
        if not _COMM_RE.match(self.proc_comm):
            raise ValueError(f"provider {self.id}: proc_comm {self.proc_comm!r} is not a plain comm name")
        if not _COMM_RE.match(self.binary):
            raise ValueError(f"provider {self.id}: binary {self.binary!r} is not a plain program name")
        if not self.config_dir.startswith("/") or self.config_dir.endswith("/"):
            raise ValueError(f"provider {self.id}: config_dir must be an absolute path without a trailing slash")
        for name in (self.config_dir_env, self.auth.token_env, *self.auth.scrub_env):
            if not _ENV_NAME_RE.match(name):
                raise ValueError(f"provider {self.id}: {name!r} is not an env var name")
        if not self.required_hosts:
            raise ValueError(f"provider {self.id}: required_hosts must name the auth/inference hosts")

    @property
    def version_label_key(self) -> str:
        """The image label's short key (``labels`` prefixes it with the label namespace)."""
        return self.image.version_label
