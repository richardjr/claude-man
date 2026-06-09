"""Resolve the latest/stable Claude Code release version, host-side, so claude-man can offer to
rebuild a project's image before start when a newer claude is available.

Claude's native installer reads a plain-text version pointer at
``{RELEASES_BASE_URL}/<channel>`` (``latest`` / ``stable``) — a cheap, TOKEN-LESS GET (no auth, reads
no quota). claude-man hits the same endpoint to learn the newest version, compares it to the version
baked into the project's image (the ``claude-man.claude-version`` label), and — on the operator's
confirmation — rebuilds the image pinned to it. The container itself stays byte-for-byte hardened
(invariant 2): the update is a host-side image rebuild, never an in-container write to the read-only
``~/.local`` install (which is exactly why ``claude update`` fails inside a container).

Pure parse/compare (``parse_version`` / ``compare`` / ``is_newer``) is split from the network fetch
(the usage_api.py pattern) so it is unit-testable with no sockets. The fetch NEVER raises — every
failure folds into a note so the on-start check fails OPEN (start proceeds on the existing image).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import config

_TIMEOUT_S = 4.0

# A semver-ish version string: X.Y.Z with an optional ``-prerelease`` suffix. Anchored so an HTML
# error page (a region block / outage body) is rejected rather than mistaken for a version — the
# native installer does the same guard before trusting the response.
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Drop redirects: a 30x on this fixed plain-text endpoint is anomalous, and following one could
    fetch an unrelated body we'd then try to parse as a version. Mirrors usage_api's hardened opener
    (there it also stops an auth-bearer cross-host leak; here there's no token, but refusing the
    redirect keeps the parse honest)."""

    def redirect_request(self, *args, **kwargs):  # noqa: ARG002 - intentionally drops all redirects
        return None


# Module-level opener (no redirects, default TLS verification). Reused across fetches.
_OPENER = urllib.request.build_opener(_NoRedirect())


@dataclass(frozen=True)
class ReleaseCheck:
    """A resolved channel version, or a short ``note`` explaining why there isn't one
    (``offline`` / ``http NNN`` / ``bad channel`` / ``bad resp``)."""

    version: str | None
    note: str = ""


# ---------------------------------------------------------------------------
# Pure layer (unit-tested without sockets)
# ---------------------------------------------------------------------------
def parse_version(text: str | None) -> str | None:
    """The trimmed version string if ``text`` is a bare ``X.Y.Z[-pre]``, else ``None`` (HTML/garbage)."""
    v = (text or "").strip()
    return v if _VERSION_RE.match(v) else None


def _key(v: str) -> tuple[int, int, int, int, str]:
    """Ordering key for a version: ``(major, minor, patch, release-rank, prerelease)``. A release
    outranks a prerelease of the same ``X.Y.Z`` (rank 1 vs 0); two prereleases compare lexically
    (good enough — claude ships plain ``X.Y.Z``). A non-numeric core segment degrades to 0."""
    core, _, pre = v.partition("-")
    nums: list[int] = []
    for part in core.split("."):
        try:
            nums.append(int(part))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2], 0 if pre else 1, pre)


def compare(a: str, b: str) -> int:
    """``-1`` / ``0`` / ``1`` for ``a<b`` / ``a==b`` / ``a>b`` under version ordering."""
    ka, kb = _key(a), _key(b)
    return (ka > kb) - (ka < kb)


def is_newer(target: str, current: str) -> bool:
    """True iff ``target`` is a strictly newer version than ``current`` (both must be non-empty)."""
    if not target or not current:
        return False
    return compare(target, current) > 0


# ---------------------------------------------------------------------------
# Network (needs a socket; not unit-tested)
# ---------------------------------------------------------------------------
def resolve_channel(channel: str = config.DEFAULT_CLAUDE_CHANNEL, *, timeout: float = _TIMEOUT_S) -> ReleaseCheck:
    """GET the plain-text version for ``channel``. NEVER raises — folds every failure into a note so
    the caller fails open (proceeds on the existing image)."""
    if channel not in config.CLAUDE_CHANNELS:
        return ReleaseCheck(None, f"bad channel {channel!r}")
    req = urllib.request.Request(
        f"{config.RELEASES_BASE_URL}/{channel}",
        headers={"User-Agent": config.CLAUDE_CODE_USER_AGENT},  # a generic UA is rate-limited harder
        method="GET",
    )
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            text = resp.read(256).decode("utf-8", "replace")  # a version is tiny; cap the read
    except urllib.error.HTTPError as exc:
        return ReleaseCheck(None, f"http {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError):
        return ReleaseCheck(None, "offline")
    v = parse_version(text)
    return ReleaseCheck(v, "" if v else "bad resp")
