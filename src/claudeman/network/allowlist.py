"""The base egress allowlist for strict-mode projects.

These are ``squid`` ``dstdomain`` values (a leading dot matches the domain and all subdomains; a
bare name matches exactly). ``claude.ai`` is REQUIRED — it is the OAuth subscription token-refresh
path on this host. Omitting it makes token refresh fail opaquely. (See CLAUDE.md invariant 3.)

The base set covers what Claude Code and the standard toolchains reach on a normal run: the
Anthropic API + OAuth (the `.anthropic.com` wildcard covers `api.`/`statsig.`), GitHub
(clone/fetch/release assets via the `.github.com` / `.githubusercontent.com` wildcards), and the
package registries (npm, PyPI, yarn) + Debian apt mirrors so `npm install` / `pip install` /
`apt-get install` work under lock (ROADMAP IMG-4). A project adds anything else via its
``[project.egress].allowlist`` extras.

IMPORTANT (squid will not start otherwise): a single ``dstdomain`` ACL must NOT contain a bare host
that is also covered by a leading-dot wildcard in the same ACL — squid treats the overlap as a fatal
(or warning) config error. ``build_allowlist`` enforces this by dropping any bare host already
covered by a wildcard, and it rejects over-broad junk (``.``/``*``/ports/paths/bare-TLDs) so a
hand-edited ``allowlist = ["."]`` can never silently turn a locked project into allow-all.
"""

from __future__ import annotations

import re

# Anthropic / Claude. `.anthropic.com` (wildcard) covers api.anthropic.com + statsig.anthropic.com —
# listing those bare too would make squid reject the config, so don't.
ANTHROPIC = (
    ".anthropic.com",
    "claude.ai",            # OAuth refresh — do not remove (invariant 3); exact (no wildcard) so distinct
    "downloads.claude.ai",  # claude release downloads (distinct host under claude.ai, no wildcard)
    "sentry.io",
)

# Package registries Claude/tools commonly need: npm, PyPI, yarn (IMG-4).
PACKAGES = (
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
    "registry.yarnpkg.com",
    "repo.yarnpkg.com",
)

# Debian apt mirrors (the base image is debian:trixie-slim), so `apt-get install` works under lock.
APT = (
    "deb.debian.org",
    "security.debian.org",
)

# GitHub (clone / fetch / raw / release assets). The two wildcards cover codeload.github.com,
# raw.githubusercontent.com, objects.githubusercontent.com — listing those bare too would break squid.
# `.github.com` also covers ssh.github.com, GitHub's SSH-over-443 endpoint (issue #12).
GITHUB = (
    ".github.com",
    ".githubusercontent.com",
)

# GitLab / Bitbucket. Needed so a LOCKED project reaches these forges, AND so git-over-ssh works under
# strict egress via their SSH-over-443 alt endpoints — altssh.gitlab.com / altssh.bitbucket.org, which
# the `.gitlab.com` / `.bitbucket.org` wildcards cover (issue #12). Azure DevOps has no 443 SSH endpoint,
# so it gets no SSH path under lock (HTTPS remote or open mode — see docs/SECURITY.md).
GITLAB = (
    ".gitlab.com",
)
BITBUCKET = (
    ".bitbucket.org",
)

BASE_ALLOWLIST: tuple[str, ...] = ANTHROPIC + PACKAGES + APT + GITHUB + GITLAB + BITBUCKET

# A valid dstdomain: an optional leading dot (subdomain wildcard) then ≥2 dot-separated labels ending
# in an alphabetic TLD. This rejects the squid catch-all ``.``, a bare ``*``, anything with a
# port/scheme/path/whitespace, and bare or single-label-TLD entries (``com`` / ``.com``) — any of
# which would widen egress far beyond intent. Legitimate forms like ``.internal.example.com`` pass.
_HOST_RE = re.compile(r"^\.?([a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$", re.IGNORECASE)


def is_valid_dstdomain(host: str) -> bool:
    """True if ``host`` is a safe squid ``dstdomain`` value (see ``_HOST_RE``). Used to drop over-broad
    or malformed allowlist entries so they can never widen egress (fail-closed: a dropped entry stays
    blocked, never allow-all)."""
    return bool(_HOST_RE.match(host))


def build_allowlist(project_extras: tuple[str, ...] = ()) -> list[str]:
    """Base allowlist + the project's ``[project.egress].allowlist`` extras → a deduped, squid-safe list.

    Order-preserving (base first, then extras). Drops: blank entries; invalid/over-broad entries
    (``.``/``*``/ports/paths/bare-TLDs — see ``is_valid_dstdomain``); and any bare host already covered
    by a leading-dot wildcard in the final set (squid rejects such an overlap inside one ``dstdomain``
    ACL — this is what makes a locked project actually start). Every drop fails CLOSED — a removed
    entry simply stays blocked, never silently allow-all.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for host in (*BASE_ALLOWLIST, *project_extras):
        h = host.strip()
        if h and is_valid_dstdomain(h) and h not in seen:
            seen.add(h)
            merged.append(h)
    wildcards = [h for h in merged if h.startswith(".")]

    def covered_by_wildcard(host: str) -> bool:
        # `.example.com` covers the apex `example.com` and any `*.example.com`.
        return any(host == w[1:] or host.endswith(w) for w in wildcards)

    return [h for h in merged if h.startswith(".") or not covered_by_wildcard(h)]
