"""Per-account subscription usage — the 5-hour + weekly windows Claude Code's ``/usage`` shows.

Claude Code reads ``GET https://api.anthropic.com/api/oauth/usage`` with the account's OAuth bearer
token and renders the 5-hour rolling-window and weekly *utilization* percentages (0–100) plus reset
timestamps. claude-man queries the SAME endpoint host-side, once per profile, using that profile's
stored ``CLAUDE_CODE_OAUTH_TOKEN`` — so the operator sees how close each ACCOUNT is to its subscription
limits (the metric the transcript token-totals can't reveal). This is account-wide, not container-
scoped: it reflects all usage on that account, including the operator's own host sessions.

Requires the token to carry the ``user:profile`` scope. ``claude setup-token`` defaults to
``user:inference`` only (a 403 on this endpoint); minting with ``CLAUDE_CODE_OAUTH_SCOPES="user:profile
user:inference"`` (see ``profiles/setup_token.py``) makes the same token usage-capable. The fetch is
read-only and does NOT consume quota; the ``User-Agent`` MUST be ``claude-code/<ver>`` — a generic UA
lands in an aggressively rate-limited bucket.

Pure parsing/rendering (``parse_utilization`` / ``render_bar`` / ``level``) is split from the network
call so it is unit-testable with no sockets.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import config

_TIMEOUT_S = 8.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects — so a 30x never re-sends the ``Authorization`` bearer to another
    host. urllib's default redirect handler copies ``Authorization`` onto the redirect target WITHOUT
    stripping it on a cross-host hop; for this fixed JSON endpoint a redirect is anomalous and would
    leak the account credential (invariant 1). Returning None turns a 30x into an ``HTTPError`` instead,
    which the caller folds into a ``"http NNN"`` note — the token is never sent twice."""

    def redirect_request(self, *args, **kwargs):  # noqa: ARG002 - intentionally drops all redirects
        return None


# Module-level opener (no redirects, default TLS verification). Reused across fetches.
_OPENER = urllib.request.build_opener(_NoRedirect())


@dataclass(frozen=True)
class Window:
    """One usage window: percent utilized (0–100, or None when the window is absent/null) + reset."""
    pct: float | None
    resets_at: str = ""


@dataclass(frozen=True)
class Utilization:
    five_hour: Window
    seven_day: Window
    # Per-model weekly windows — parsed to round out the documented endpoint shape; not yet surfaced
    # in the TUI/CLI (which render five_hour + seven_day). Cheap to keep for a future per-model view.
    seven_day_opus: Window
    seven_day_sonnet: Window


@dataclass(frozen=True)
class UsageResult:
    """Either a parsed ``Utilization`` or a short ``note`` explaining why there isn't one
    (``"no token"`` / ``"re-mint"`` for a 403 missing-scope / ``"offline"`` / ``"http NNN"``)."""
    util: Utilization | None
    note: str = ""


# ---------------------------------------------------------------------------
# Pure layer (unit-tested without sockets)
# ---------------------------------------------------------------------------
def _window(d: object) -> Window:
    d = d if isinstance(d, dict) else {}
    u = d.get("utilization")
    # int|float only (bool is an int subclass → exclude it); reject NaN/inf so they can't read as a
    # band in level() (NaN >= 90 is False → would otherwise show 'ok').
    valid = isinstance(u, (int, float)) and not isinstance(u, bool) and math.isfinite(u)
    return Window(pct=float(u) if valid else None, resets_at=str(d.get("resets_at") or ""))


def parse_utilization(data: dict) -> Utilization:
    """Pure: the ``/api/oauth/usage`` JSON -> ``Utilization``. Missing/null windows -> ``pct=None``."""
    return Utilization(
        five_hour=_window(data.get("five_hour")),
        seven_day=_window(data.get("seven_day")),
        seven_day_opus=_window(data.get("seven_day_opus")),
        seven_day_sonnet=_window(data.get("seven_day_sonnet")),
    )


_FULL = "█"
_EMPTY = "░"


def render_bar(pct: float | None, width: int = 8) -> str:
    """A compact text percent bar, e.g. ``███░░░░░ 32%`` — or ``—`` when ``pct`` is None. Pure."""
    if pct is None:
        return "—"
    p = max(0.0, min(100.0, float(pct)))
    filled = int(round(p / 100.0 * width))
    return f"{_FULL * filled}{_EMPTY * (width - filled)} {p:.0f}%"


def level(pct: float | None) -> str:
    """Threshold band for colouring a bar: ``none`` | ``ok`` | ``warn`` | ``crit``. Pure."""
    if pct is None:
        return "none"
    if pct >= 90:
        return "crit"
    if pct >= 70:
        return "warn"
    return "ok"


# ---------------------------------------------------------------------------
# Network (needs a socket; not unit-tested)
# ---------------------------------------------------------------------------
def fetch_utilization(token: str, *, timeout: float = _TIMEOUT_S) -> UsageResult:
    """GET the usage endpoint for one token. Never raises — folds every failure into a short note.

    A 403 means the token lacks the ``user:profile`` scope (the ``setup-token`` default) -> ``re-mint``.
    """
    if not token:
        return UsageResult(None, "no token")
    req = urllib.request.Request(
        config.OAUTH_USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": config.OAUTH_USAGE_BETA,
            "User-Agent": config.CLAUDE_CODE_USER_AGENT,
            "Content-Type": "application/json",
        },
        method="GET",
    )
    try:
        with _OPENER.open(req, timeout=timeout) as resp:  # no-redirect opener; https + host pinned in config
            data = json.loads(resp.read().decode("utf-8"))
        return UsageResult(parse_utilization(data), "")
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return UsageResult(None, "re-mint")   # token lacks user:profile scope
        if exc.code == 401:
            return UsageResult(None, "auth")
        return UsageResult(None, f"http {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError):
        return UsageResult(None, "offline")
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return UsageResult(None, "bad resp")


def fetch_for_profile(name: str) -> UsageResult:
    """Convenience: load a profile's stored token, then ``fetch_utilization``."""
    from .registry import profiles as profiles_registry

    try:
        token = profiles_registry.load_token(name)
    except Exception:  # noqa: BLE001 - a missing/garbled profile must not break the panel
        token = None
    return fetch_utilization(token or "")
