"""Per-container network I/O via ``docker stats`` — the always-on Network panel's Traffic figure.

``docker stats --no-stream`` reports each container's CUMULATIVE network I/O since it started (RX/TX,
human-formatted as ``"12.3MB / 4.1MB"``), and one invocation can cover many containers — so the TUI
worker reads every running agent in a single subprocess. The argv renderer + ``parse_stats`` are PURE
(unit-tested without a daemon, like ``docker/runner.build_create_argv``); the thin ``container_net_io``
wrapper reuses ``docker/runner._run`` and is TIME-BOUNDED — ``docker stats --no-stream`` can hang on a
just-started / restarting container or a wedged daemon, and this runs on a repeating TUI poll.

NetIO is the WHOLE-container figure. For an OPEN project it is real internet I/O. For a LOCKED project
the agent is on the ``--internal`` net only, so its NetIO is the agent↔sidecar (proxied) traffic — still
"how much this container moved over the network", which is exactly what the panel's Traffic column
means; it is NOT a per-destination egress total. Per-destination detail for a locked project comes from
the squid access log (``network/egress``), via the CLI ``project egress-log``. NetIO resets to
zero whenever the container is recreated (claude-man's recreate-to-apply lifecycle), so it reads as
"traffic since this container started", not a lifetime total.
"""

from __future__ import annotations

from . import runner

# `docker stats --no-stream` samples once, but can still block on a wedged daemon / a container mid-
# restart, so the repeating panel poll bounds it (runner._run maps a timeout to rc 124, never raises).
_STATS_TIMEOUT_S = 20

_FORMAT = "{{.Name}}\t{{.NetIO}}"


def stats_argv(names: list[str]) -> list[str]:
    """`docker stats --no-stream` for the given container names — one ``name<TAB>NetIO`` line each."""
    return ["docker", "stats", "--no-stream", "--format", _FORMAT, *names]


def parse_stats(output: str) -> dict[str, str]:
    """Map container name → its NetIO display string (e.g. ``"12.3MB / 4.1MB"``) from ``stats_argv``
    output. PURE so it is unit-tested against sample text; blank / tab-less lines are skipped."""
    out: dict[str, str] = {}
    for line in output.splitlines():
        name, sep, netio = line.partition("\t")
        name = name.strip()
        netio = netio.strip()
        if sep and name and netio:
            out[name] = netio
    return out


def container_net_io(names: list[str]) -> dict[str, str]:
    """NetIO display string per RUNNING container (one ``docker stats`` call). Pass only running
    containers — ``docker stats`` errors on a name that no longer exists, which would zero the batch.
    Empty when docker is absent / the call fails / times out, so the caller renders ``—``."""
    names = [n for n in names if n]
    if not names:
        return {}
    cp = runner._run(stats_argv(names), timeout=_STATS_TIMEOUT_S)
    if cp.returncode != 0:
        return {}
    return parse_stats(cp.stdout or "")
