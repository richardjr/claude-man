"""Strict-egress squid+dnsmasq sidecar generator (Phase 4).

Generates a two-service compose: a squid+dnsmasq sidecar on both an ``internal: true``
agent network and a normal egress bridge, with the hardened agent attached ONLY to the
internal network (no direct route -> the proxy cannot be bypassed). dnsmasq forwards
allowlisted names to the host resolver at 172.17.0.1. squid enforces the dstdomain
allowlist over CONNECT tunnels (no MITM). See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from .allowlist import build_allowlist  # noqa: F401  (used by the Phase 4 impl)


def render_squid_conf(allowlist: list[str]) -> str:
    raise NotImplementedError("phase 4: squid.conf generation — see ROADMAP.md")


def render_compose(slug: str, allowlist: list[str]) -> str:
    raise NotImplementedError("phase 4: strict-egress compose generation — see ROADMAP.md")
