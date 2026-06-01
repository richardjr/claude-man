"""The base egress allowlist for strict-mode projects.

``claude.ai`` is REQUIRED — it is the OAuth subscription token-refresh path on this host.
Omitting it makes token refresh fail opaquely. (See CLAUDE.md invariant 3.)
"""

from __future__ import annotations

# Anthropic / Claude
ANTHROPIC = (
    "api.anthropic.com",
    ".anthropic.com",
    "claude.ai",            # OAuth refresh — do not remove
    "downloads.claude.ai",
    "statsig.anthropic.com",
    "sentry.io",
)

# Package registries Claude/tools commonly need
PACKAGES = (
    "registry.npmjs.org",
)

# GitHub (clone / fetch / raw)
GITHUB = (
    ".github.com",
    "codeload.github.com",
    ".githubusercontent.com",
    "raw.githubusercontent.com",
)

BASE_ALLOWLIST: tuple[str, ...] = ANTHROPIC + PACKAGES + GITHUB


def build_allowlist(project_extras: tuple[str, ...] = ()) -> list[str]:
    """Base allowlist + the project's ``[project.egress].allowlist`` extras, de-duplicated."""
    seen: dict[str, None] = {}
    for host in (*BASE_ALLOWLIST, *project_extras):
        seen.setdefault(host, None)
    return list(seen)
