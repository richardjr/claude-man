"""Mint / renew a profile's long-lived OAuth token via host ``claude setup-token`` (Phase 2).

For a work Teams/Enterprise seat, ``claude auth login --sso`` is run first. The captured
~1-year token is written ``0600`` to ``config.profile_token_path(name)`` (dir ``0700``);
mint time is recorded for the TUI's expiry warning. The token is NEVER copied into an
image layer or committed.
"""

from __future__ import annotations


def mint(name: str, *, sso: bool = False, default: bool = False) -> None:
    raise NotImplementedError("phase 2: profile token minting — see ROADMAP.md")


def renew(name: str) -> None:
    raise NotImplementedError("phase 2: profile token renewal — see ROADMAP.md")
