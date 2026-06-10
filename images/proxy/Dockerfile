# claude-man strict-egress proxy sidecar — squid on debian-slim.
#
# This is NOT an agent sandbox: it runs no agent code, only squid with a config that claude-man
# RENDERS per-project (network/squid.py) from the egress allowlist and bind-mounts read-only at
# /etc/squid/squid.conf. squid enforces a `dstdomain` allowlist over CONNECT tunnels (no MITM, no CA
# install — HTTPS stays end-to-end). It sits on a per-project `--internal` network (where the agent
# also lives, with no other route out) plus the default bridge for actual egress, so it is the ONLY
# path to the internet for a locked project (CLAUDE.md invariant 3 — the firewall is at the network
# layer, never in-container iptables).
#
# Same base distro as images/base/Dockerfile (debian:trixie-slim) so squid is the trixie build.
FROM debian:trixie-slim

# Declared but unused: claude-man's pure build_argv always passes --build-arg CLAUDE_VERSION; accept it
# here so `docker build` doesn't warn. The proxy has nothing to do with the claude version.
ARG CLAUDE_VERSION

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends squid ca-certificates; \
    rm -rf /var/lib/apt/lists/*; \
    # squid drops from root to the `proxy` user for its workers — make its runtime dirs proxy-owned so
    # that drop can write the pid/log/cache paths (the container rootfs is writable; we don't --read-only
    # the sidecar, only the agent). The rendered config disables on-disk caching, so /var/spool is unused.
    # NOTE: squid can NOT write /dev/stdout after the drop (root-owned pipe), so its config logs to
    # /var/log/squid/access.log (proxy-owned) and the entrypoint tails it to the container's stdout.
    mkdir -p /var/run/squid /var/log/squid /var/spool/squid; \
    chown -R proxy:proxy /var/run/squid /var/log/squid /var/spool/squid

COPY images/proxy/entrypoint.sh /usr/local/bin/proxy-entrypoint.sh
RUN chmod 0755 /usr/local/bin/proxy-entrypoint.sh

EXPOSE 3128

# The entrypoint tails squid's access log to stdout (so `docker logs` surfaces denials) and execs squid
# foreground (-N, no daemonize) so the container's PID 1 is squid and `docker stop` works. The
# per-project config is bind-mounted at run. squid starts as root and setuid-drops to `proxy` for its
# workers (a privilege DROP, allowed under --security-opt no-new-privileges).
ENTRYPOINT ["/usr/local/bin/proxy-entrypoint.sh"]
