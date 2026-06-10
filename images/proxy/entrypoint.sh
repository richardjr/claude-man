#!/bin/sh
# Stream squid's access/denial log to the container's stdout so `docker logs <proxy>` — and thus
# claude-man's `project egress-log` / TUI Egress log (egress.denied_requests) — can surface blocked
# requests. squid setuid-drops to the 'proxy' user and CANNOT write /dev/stdout (a root-owned pipe),
# so its squid.conf logs to a proxy-owned file under /var/log/squid; we tail that file here. This
# process is PID 1, so its stdout IS the container's stdout.
set -e
log=/var/log/squid/access.log
: > "$log" 2>/dev/null || true        # create it so `tail -F` has something to follow immediately
chown proxy:proxy "$log" 2>/dev/null || true
tail -n +1 -F "$log" &               # background: stream every (incl. denied TCP_DENIED) line to stdout
exec squid -N -f /etc/squid/squid.conf   # foreground; becomes PID 1, drops to the 'proxy' user
