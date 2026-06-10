"""Render the strict-egress squid.conf (Phase 4).

PURE — no daemon, no filesystem — so the rendered config is unit-testable (same pattern as
``docker/runner.build_create_argv``). claude-man writes the result to the project's state tier and
bind-mounts it read-only into the squid sidecar (see ``network/egress.py``).

squid enforces a ``dstdomain`` allowlist over CONNECT tunnels — no MITM, no CA install, so HTTPS
stays end-to-end. Denied requests are logged to stdout so ``docker logs <proxy>`` can surface them
in the TUI for allowlist tuning. The orchestration (networks + sidecar container) lives in
``egress.py``; this module only turns an allowlist into a config string.
"""

from __future__ import annotations

from .. import config
from .allowlist import build_allowlist

__all__ = ["build_allowlist", "render_squid_conf"]


def render_squid_conf(allowlist: list[str]) -> str:
    """Render squid.conf for the given allowlist (already base+extras, deduped via build_allowlist).

    CONNECT-tunnel only on 443, plain HTTP on 80/443; allow the allowlisted ``dstdomain``s, then
    ``deny all``. ``deny all`` is logged (TCP_DENIED) so the operator can see what a project tried to
    reach and add it to ``egress.allowlist`` if legitimate.
    """
    lines = [
        "# Rendered by claude-man (network/squid.py) — do not edit by hand; changes are overwritten.",
        "# Strict-egress allowlist proxy. CONNECT tunnel, no MITM, no CA install (HTTPS end-to-end).",
        f"http_port {config.PROXY_PORT}",
        "",
        "# Allowlisted destinations (base set + the project's egress.allowlist extras).",
    ]
    # One acl line per host keeps the rendered config trivially diffable and order-stable.
    lines += [f"acl allowed_dst dstdomain {host}" for host in allowlist]
    lines += [
        "",
        "acl SSL_ports port 443",
        "acl Safe_ports port 80",
        "acl Safe_ports port 443",
        "acl CONNECT method CONNECT",
        "# Defence-in-depth: never proxy to loopback or link-local (the cloud metadata endpoint",
        "# 169.254.169.254 lives here) even if the allowlist is ever mis-set — closes an SSRF relay.",
        "acl to_localhost dst 127.0.0.0/8 0.0.0.0/8 ::1",
        "acl to_linklocal dst 169.254.0.0/16 fe80::/10",
        "",
        "http_access deny !Safe_ports",
        "http_access deny CONNECT !SSL_ports",
        "http_access deny to_localhost",
        "http_access deny to_linklocal",
        "# Allow allowlisted hosts; deny (and log) everything else.",
        "http_access allow allowed_dst",
        "http_access deny all",
        "",
        "# squid setuid-drops to the 'proxy' user and CANNOT write /dev/stdout, so log to proxy-owned",
        "# files. The image entrypoint tails access.log to the container's stdout, so `docker logs",
        "# <proxy>` still surfaces denials for `project egress-log` / the TUI Egress log.",
        "access_log /var/log/squid/access.log squid",
        "cache_log /var/log/squid/cache.log",
        "cache deny all",
        "# Don't disclose the client address / proxy in forwarded requests.",
        "forwarded_for delete",
        "via off",
        "",
    ]
    return "\n".join(lines)
