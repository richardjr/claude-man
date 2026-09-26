"""The ``claude`` provider — Claude Code, the reference agent (Phase 7a).

Every value here is the policy claude-man has always applied; the constants live in ``config`` so
the container image, the runner and the tests keep one source of truth for the paths/env names,
and this object is how the rest of the code REACHES them without hard-coding "claude".
"""

from __future__ import annotations

from .. import config
from .base import AgentProvider, AuthSpec, ImageSpec, UpdateSpec

# Anthropic / Claude egress a LOCKED container must always reach (invariant 3). `.anthropic.com`
# (wildcard) covers api.anthropic.com + statsig.anthropic.com — listing those bare too would make
# squid reject the config, so don't. `claude.ai` is the OAuth refresh path — do not remove.
REQUIRED_HOSTS = (
    ".anthropic.com",
    "claude.ai",            # OAuth refresh — do not remove (invariant 3); exact (no wildcard) so distinct
    "downloads.claude.ai",  # claude release downloads (distinct host under claude.ai, no wildcard)
    "sentry.io",
)

PROVIDER = AgentProvider(
    id="claude",
    display_name="Claude Code",
    binary="claude",
    proc_comm="claude",     # the image's native install runs as a process named `claude`
    config_dir=config.CONTAINER_CLAUDE_CONFIG,
    config_dir_env="CLAUDE_CONFIG_DIR",
    auth=AuthSpec(
        token_env=config.OAUTH_TOKEN_ENV,
        scrub_env=config.SCRUBBED_ENV_KEYS,
        credential_file=".credentials.json",
        identity_file=".claude.json",
    ),
    image=ImageSpec(
        version_build_arg="CLAUDE_VERSION",
        version_label="claude-version",
        default_version=config.DEFAULT_CLAUDE_VERSION,
    ),
    updates=UpdateSpec(
        releases_url=config.RELEASES_BASE_URL,
        channels=config.CLAUDE_CHANNELS,
        default_channel=config.DEFAULT_CLAUDE_CHANNEL,
        user_agent=config.CLAUDE_CODE_USER_AGENT,
    ),
    required_hosts=REQUIRED_HOSTS,
)
