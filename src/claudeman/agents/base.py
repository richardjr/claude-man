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
from dataclasses import dataclass

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
    identity_file: str          # the onboarding/identity stub the seed writes (e.g. ".claude.json")


@dataclass(frozen=True)
class ImageSpec:
    """The image-bake seam: the build-arg carrying the agent version into the Dockerfile and the
    IMAGE label key (``<LABEL_PREFIX>.<version_label>``) the build stamps + ``image_version`` reads."""
    version_build_arg: str
    version_label: str
    default_version: str


@dataclass(frozen=True)
class UpdateSpec:
    """The on-start update seam: a token-less GET of ``<releases_url>/<channel>`` returns the
    channel's newest version (updates.py). ``user_agent`` because the Anthropic endpoint rate-limits
    a generic UA harder."""
    releases_url: str
    channels: tuple[str, ...]
    default_channel: str
    user_agent: str


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
