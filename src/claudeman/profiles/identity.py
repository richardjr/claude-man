"""Build the scrubbed ``.claude.json`` identity stub seeded into a project's config dir.

Keeps only display-safe identity fields (never ``accountUuid``/``userID``/
``organizationUuid``) and sets the onboarding flags so the in-container CLI doesn't
prompt. The token itself is injected via env, never written here.
"""

from __future__ import annotations

from ..syncback import denylist

# Default display-safe fields kept from a host oauthAccount block.
DEFAULT_KEEP = ("emailAddress", "displayName", "organizationName")


def build_identity_stub(
    oauth_account: dict,
    keep_fields: tuple[str, ...] = DEFAULT_KEEP,
) -> dict:
    """Return a minimal ``.claude.json`` dict: scrubbed identity + onboarding flags."""
    kept = {
        k: v
        for k, v in oauth_account.items()
        if k in keep_fields and not denylist.is_denied_json_key(k)
    }
    return {
        "hasCompletedOnboarding": True,
        "installMethod": "native",
        "oauthAccount": kept,
    }
