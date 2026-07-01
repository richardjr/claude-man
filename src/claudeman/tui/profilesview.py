"""Profile-picker view model — pure listing + current-marking + token-age formatting.

Like ``packsview``/``splash``/``rowfx``, this stays dependency-free (no textual import) so its
logic is unit-testable without a running app. The ``ProfileSelectScreen`` is a thin DataTable
wrapper over ``rows()``.

Token age is shown only as a factual hint (which account is live / never set up) — claude-man
never mints or refreshes here; a stale token means the operator re-runs ``profile renew`` on the
host. It reads only the token file's mtime (``profiles.token_age_days``), never the token itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..registry import profiles as profiles_registry

# setup-token OAuth tokens last ~1 year and cannot self-refresh; flag the last stretch so the
# operator doesn't switch a project onto an account whose token is about to 401 in-container.
_TOKEN_AGING_DAYS = 330.0


@dataclass(frozen=True)
class Row:
    """One selectable profile row (all fields display-ready; ``key`` is the value dismissed)."""

    key: str            # the profile name — the value the screen dismisses on select
    name: str           # profile name (what the projects-table PROFILE column shows)
    account: str        # account_email, or "-" when unset
    default: bool       # marked as the default profile
    marked: bool        # the project's effective current profile (gets the " ←" cue)
    token: str          # factual token-age hint ("120d" / "45d aging" / "no token")


def token_status(age_days: float | None) -> str:
    """Format a profile's token age as a short, non-alarmist hint.

    ``None`` (no token file) → ``"no token"`` so the operator doesn't switch onto an account that
    was never set up (it would 401 in-container). Otherwise the age in whole days, tagged
    ``aging`` once it nears the ~1-year setup-token cliff.
    """
    if age_days is None:
        return "no token"
    days = int(age_days)
    return f"{days}d aging" if age_days >= _TOKEN_AGING_DAYS else f"{days}d"


def rows(current: str) -> list[Row]:
    """All registry profiles (sorted, malformed ones skipped) as display rows.

    ``current`` is the project's *effective* profile name; the matching row is ``marked`` so the
    screen can cue it and treat re-picking it as a no-op.
    """
    out: list[Row] = []
    for p in profiles_registry.list_profiles():
        out.append(
            Row(
                key=p.name,
                name=p.name,
                account=p.account_email or "-",
                default=p.default,
                marked=(p.name == current),
                token=token_status(profiles_registry.token_age_days(p.name)),
            )
        )
    return out
