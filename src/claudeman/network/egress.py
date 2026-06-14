"""Strict-egress orchestration (Phase 4): a per-project ``--internal`` network + a squid sidecar.

`--cap-drop ALL` forbids in-container iptables (CLAUDE.md invariant 3), so a locked project's
firewall is a network-layer boundary, not a rule inside the agent:

  * one per-project ``--internal`` docker network (``claude-man-net-<slug>``) — no gateway, so a
    container on it has NO route to the internet on its own;
  * the agent attaches to that network ONLY (rendered additively in
    ``docker/runner.build_create_argv`` — the hardened floor is byte-identical to an open project),
    with ``HTTP(S)_PROXY`` pointing at the sidecar;
  * the squid sidecar (``claude-man-proxy-<slug>``) sits on that internal network AND the default
    bridge, so it is the ONLY path out. squid enforces the ``dstdomain`` allowlist over CONNECT
    tunnels (no MITM); everything else is denied + logged.

The agent-side flags live in ``runner``; this module manages the network + sidecar they point at.
PURE argv renderers (``*_argv``) + the access-log parsers (``parse_access`` / ``parse_denied`` /
``summarize_access``) are unit-tested without a daemon; the thin wrappers reuse ``docker/runner._run``
(which maps a missing docker / a wedged daemon to a non-zero result rather than raising).
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import config
from ..docker import images, runner
from ..registry.schema import Project
from . import allowlist as allowlist_mod
from . import squid

# Callers pass a ``Callable[[str], None] | None`` for ``on_progress`` (the TUI log pane / CLI print);
# left untyped to keep this module import-light and textual-free.


@dataclass(frozen=True)
class Result:
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class AccessRecord:
    """One parsed squid access-log line (the default ``squid`` logformat).

    ``allowed`` is False for a blocked request (``TCP_DENIED`` / a ``/403`` status) — the SAME test
    ``parse_denied`` has always applied, just retained per-record. ``port`` is the destination port
    (the agent's HTTPS egress is always ``CONNECT host:port``); ``None`` for a plain-HTTP url with no
    explicit port. ``ts`` carries the raw ``%ts.%03tu`` epoch.ms field so ``summarize_access`` can report
    last-seen without re-splitting.

    BYTE CAVEAT: squid writes a CONNECT tunnel's access-log line only when the tunnel CLOSES, so an
    in-flight HTTPS transfer contributes 0 bytes until it ends — ``bytes`` (download direction, ``%<st``)
    reflects CLOSED connections, not live throughput. Denials are logged promptly, so the blocked view
    stays live; only allowed-tunnel byte totals lag to close.
    """

    ts: str
    host: str
    port: int | None
    allowed: bool
    bytes: int
    method: str
    status: str


@dataclass(frozen=True)
class HostStat:
    """Per-host aggregate over the access records: how many requests were allowed vs blocked, the
    distinct destination ports seen, the summed (closed-connection) bytes, and the timestamp + verdict
    of the most recent request. The operator view for allowlist tuning and for seeing what a locked
    project actually talks to."""

    host: str
    ports: tuple[int, ...]
    allowed_count: int
    denied_count: int
    total_bytes: int
    last_seen: str
    last_allowed: bool


# ---------------------------------------------------------------------------
# Pure argv renderers (no daemon) — unit-tested
# ---------------------------------------------------------------------------
def _label_args(slug: str, role: str) -> list[str]:
    """Stamp claude-man labels so an operator (or a future reconcile sweep) can find the per-project
    network/sidecar by slug + role, exactly like the agent container's labels (invariant 4)."""
    return ["--label", f"{config.LABEL_PREFIX}.slug={slug}",
            "--label", f"{config.LABEL_PREFIX}.role={role}"]


def network_create_argv(slug: str) -> list[str]:
    """`docker network create --internal …` — the no-route-out network the agent and sidecar share."""
    return ["docker", "network", "create", "--internal",
            *_label_args(slug, "egress-net"), config.egress_net_name(slug)]


def proxy_run_argv(slug: str, *, conf_path: str) -> list[str]:
    """`docker run -d …` for the squid sidecar on the project's internal network.

    The rendered squid.conf is bind-mounted read-only (the proxy never trusts a writable config). The
    sidecar is trusted infra (our fixed image + rendered config, no agent code) so it is NOT under the
    agent's hardened floor — but it still runs ``--security-opt no-new-privileges`` and joins ONLY the
    internal net at first (egress is added via a separate ``network connect bridge`` so the order is
    explicit). squid runs foreground (image ENTRYPOINT) and setuid-drops to the ``proxy`` user.
    """
    return [
        "docker", "run", "-d",
        "--name", config.proxy_container_name(slug),
        *_label_args(slug, "proxy"),
        "--network", config.egress_net_name(slug),
        "--restart", "no",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "256",   # squid forks few helpers — generous cap, defense-in-depth
        "-v", f"{conf_path}:/etc/squid/squid.conf:ro",
        config.image_tag(config.PROXY_IMAGE),
    ]


def bridge_connect_argv(slug: str) -> list[str]:
    """Attach the sidecar to the default bridge so it (and only it) can reach the internet."""
    return ["docker", "network", "connect", "bridge", config.proxy_container_name(slug)]


# squid's default ``squid`` logformat is a fixed, whitespace-separated field layout — the single
# source of truth for the magic indices used below (one place so the two parsers can't drift):
#   [0] %ts.%03tu  epoch.ms timestamp      [1] %6tr   elapsed ms      [2] %>a   client IP
#   [3] %Ss/%03Hs  result/status           [4] %<st   bytes to client [5] %rm   method
#   [6] %ru        URL (host:port for CONNECT)        [7] %un user     [8] %Sh/%<A hier/server
#   [9] %mt        content type
# result(3)+bytes(4)+method(5)+url(6) must all be present for a line to be an access record; user/
# hier/type may be absent on some lines, so the guard is the same ``< 7`` the original used.
_SQUID_MIN_FIELDS = 7


def _split_squid_fields(line: str) -> list[str] | None:
    """Whitespace-split a squid access-log line, or ``None`` if it is too short to carry
    result/bytes/method/url (a ``cache.log`` / entrypoint line, not an access line — skip, don't
    mis-index)."""
    parts = line.split()
    return parts if len(parts) >= _SQUID_MIN_FIELDS else None


def _host_port_from_url(url: str) -> tuple[str, int | None]:
    """Normalise a squid-logged URL to ``(host, port)``.

    Strips a scheme + path, then splits a CONNECT ``host:port`` into a bare host + int port (so the
    host pastes straight into ``egress.allowlist`` — a ``dstdomain host:port`` is invalid). Returns
    ``(host, None)`` for a plain-HTTP url with no explicit port (and ``('', None)`` for an empty url so
    callers can skip it). Shared by ``parse_denied`` + ``parse_access`` so the host normalisation can
    never diverge between them. Bracketed IPv6 literals (``[::1]:443``) split correctly — the final
    ``:`` precedes the numeric port.
    """
    if not url or url == "-":          # squid logs '-' for an early-denied request with no URL
        return "", None
    host = url.split("://", 1)[-1]     # strip scheme for a plain-HTTP url
    host = host.split("/", 1)[0]       # drop any path
    h, sep, port = host.rpartition(":")
    if sep and h and port.isdigit():
        return h, int(port)
    return host, None


def parse_access(log: str) -> list[AccessRecord]:
    """Parse squid's access log (``docker logs <proxy>`` output) into structured records.

    Captures BOTH allowed and denied requests — host, port, allow/deny verdict, bytes, method,
    status — feeding ``summarize_access`` for the TUI Network panel's per-project counts. One
    ``AccessRecord`` per parseable line, in
    first-seen (chronological) order, with NO de-duplication (callers aggregate). PURE (string in →
    records out, no daemon) so it is unit-tested against sample log text, exactly like the denied-only
    view it now backs. See ``AccessRecord`` for the field layout + the tunnel-byte caveat.
    """
    out: list[AccessRecord] = []
    for line in log.splitlines():
        parts = _split_squid_fields(line)
        if parts is None:
            continue
        result_status = parts[3]
        host, port = _host_port_from_url(parts[6])
        if not host:
            continue
        # Allow/deny is orthogonal to HTTP status: a permitted-but-upstream-failed tunnel
        # (TCP_TUNNEL/503) is still allowed. This is the EXACT predicate parse_denied used, so the
        # wrapper below reproduces today's output byte-for-byte.
        denied = "TCP_DENIED" in result_status or result_status.endswith("/403")
        out.append(AccessRecord(
            ts=parts[0],
            host=host,
            port=port,
            allowed=not denied,
            bytes=int(parts[4]) if parts[4].isdigit() else 0,
            method=parts[5],
            status=result_status.partition("/")[2],
        ))
    return out


def parse_denied(log: str) -> list[str]:
    """Destinations a locked project tried to reach but the allowlist BLOCKED — deduped, first-seen
    order, CONNECT port stripped (paste-able into ``egress.allowlist``).

    A thin filter over ``parse_access`` so the field layout + host normalisation live in exactly one
    place; the public contract is unchanged (the existing tests pin it). PURE, unit-tested.
    """
    out: list[str] = []
    seen: set[str] = set()
    for rec in parse_access(log):
        if not rec.allowed and rec.host not in seen:
            seen.add(rec.host)
            out.append(rec.host)
    return out


def summarize_access(records: list[AccessRecord]) -> list[HostStat]:
    """Fold access records into a per-host aggregate (allowed/denied counts, distinct ports, summed
    bytes, most-recent timestamp + verdict), in first-seen host order — the caller sorts if it wants.
    PURE. Takes records (not log text) so the TUI Network panel reads the sidecar once via
    ``access_log`` and reuses this aggregation without a second log read."""
    order: list[str] = []
    agg: dict[str, dict] = {}
    for rec in records:
        a = agg.get(rec.host)
        if a is None:
            a = {"allowed": 0, "denied": 0, "bytes": 0, "ports": set(),
                 "last_seen": rec.ts, "last_allowed": rec.allowed}
            agg[rec.host] = a
            order.append(rec.host)
        a["allowed" if rec.allowed else "denied"] += 1
        a["bytes"] += rec.bytes
        if rec.port is not None:
            a["ports"].add(rec.port)
        a["last_seen"] = rec.ts          # records arrive in chronological order, so the last wins
        a["last_allowed"] = rec.allowed
    return [HostStat(host=h, ports=tuple(sorted(agg[h]["ports"])),
                     allowed_count=agg[h]["allowed"], denied_count=agg[h]["denied"],
                     total_bytes=agg[h]["bytes"], last_seen=agg[h]["last_seen"],
                     last_allowed=agg[h]["last_allowed"]) for h in order]


# ---------------------------------------------------------------------------
# Thin daemon wrappers (need docker; not unit-tested)
# ---------------------------------------------------------------------------
def network_internal_state(slug: str) -> str:
    """`'true'`/`'false'` for the network's `--internal` flag, or `''` if it doesn't exist (or docker
    is absent). Used to ensure a reused network really has no route out (not just that it exists)."""
    cp = runner._run(["docker", "network", "inspect", "-f", "{{.Internal}}",
                      config.egress_net_name(slug)])
    return cp.stdout.strip() if cp.returncode == 0 else ""


def ensure_network(slug: str) -> Result:
    """Ensure the per-project network exists AND is ``--internal`` (idempotent).

    Verifying the ``--internal`` flag — not just existence — is the security point: a same-named
    network that already exists but is NOT internal would give the agent a direct route out, a silent
    bypass of strict egress while the project still shows ``Egress=strict``. So a non-internal
    collision is removed + recreated as internal; if it can't be removed (e.g. a container is
    attached), we FAIL CLOSED rather than reuse a leaky network."""
    name = config.egress_net_name(slug)
    state = network_internal_state(slug)
    if state == "true":
        return Result(True, "egress network present")
    if state == "false":
        rm = runner._run(["docker", "network", "rm", name])
        if rm.returncode != 0:
            return Result(False, f"egress network {name} exists but is NOT --internal and can't be "
                                 f"recreated (in use?): {rm.stderr.strip() or rm.stdout.strip()} — "
                                 f"remove it and retry")
    cp = runner._run(network_create_argv(slug))
    if cp.returncode != 0:
        return Result(False, f"could not create egress network: {cp.stderr.strip() or cp.stdout.strip()}")
    return Result(True, f"created {name}")


def render_conf(project: Project) -> str:
    """Render the project's squid.conf to the state tier and return its host path (a string).

    Derived purely from the registry allowlist (base set + ``project.allowlist`` extras) — no secret —
    so it is safe in the (non-synced) state dir and is overwritten on every lock so an allowlist edit
    applies on the next recreate.
    """
    al = allowlist_mod.build_allowlist(project.allowlist)
    path = config.squid_conf_path(project.slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(squid.render_squid_conf(al), encoding="utf-8")
    return str(path)


def ensure_proxy(project, *, on_progress=None) -> Result:
    """Build the proxy image if needed, render the config, (re)create the sidecar, and wire its egress.

    Recreated from scratch each call (rm any stale sidecar first) so a changed allowlist always takes
    effect — the sidecar is cheap and stateless. On a partial failure the half-built sidecar is removed
    so a locked project never starts with a proxy that has no egress (which would silently break ALL
    traffic rather than just the disallowed part).
    """
    slug = project.slug
    if not images.image_exists(config.PROXY_IMAGE):
        if on_progress:
            on_progress(f"building {config.image_tag(config.PROXY_IMAGE)} (one-time squid sidecar) …")
        rc = images.build_one(config.PROXY_IMAGE, on_line=on_progress)
        if rc != 0:
            return Result(False, f"failed to build {config.image_tag(config.PROXY_IMAGE)} "
                                 f"(docker build exited {rc})")
    conf = render_conf(project)
    runner._run(["docker", "rm", "-f", config.proxy_container_name(slug)])  # drop any stale sidecar
    cp = runner._run(proxy_run_argv(slug, conf_path=conf))
    if cp.returncode != 0:
        return Result(False, f"squid sidecar failed to start: {cp.stderr.strip() or cp.stdout.strip()}")
    bc = runner._run(bridge_connect_argv(slug))
    if bc.returncode != 0:
        runner._run(["docker", "rm", "-f", config.proxy_container_name(slug)])  # no half-wired proxy
        return Result(False, f"could not connect sidecar to egress bridge: "
                             f"{bc.stderr.strip() or bc.stdout.strip()}")
    n = len(allowlist_mod.build_allowlist(project.allowlist))
    return Result(True, f"egress locked ({n} allowed domains) via {config.proxy_container_name(slug)}")


# Teardown docker calls are TIME-BOUNDED: a wedged/contended daemon must never hang the caller
# forever. `stop_proxy` runs in `lifecycle.stop` for EVERY project (open ones just rm a non-existent
# sidecar — fast), so an unbounded `docker rm` here could stall the stop-all worker and trap the
# operator behind the progress modal. `runner._run` maps a timeout to a non-zero result (124).
_TEARDOWN_TIMEOUT_S = 20


def stop_proxy(slug: str) -> None:
    """Remove the sidecar (best-effort) when the agent stops. The network is left in place (the stopped
    agent is still attached, so it can't be removed yet, and it's recreated idempotently on next up)."""
    runner._run(["docker", "rm", "-f", config.proxy_container_name(slug)], timeout=_TEARDOWN_TIMEOUT_S)


def teardown(slug: str) -> None:
    """Remove the sidecar AND the per-project network (best-effort, idempotent). Called on delete and
    when a project is unlocked back to open egress, so no orphan network/sidecar lingers."""
    runner._run(["docker", "rm", "-f", config.proxy_container_name(slug)], timeout=_TEARDOWN_TIMEOUT_S)
    runner._run(["docker", "network", "rm", config.egress_net_name(slug)], timeout=_TEARDOWN_TIMEOUT_S)


# ---------------------------------------------------------------------------
# Lock smoke — does the allowlist actually enforce? (daemon-gated; pure verdict is unit-tested)
# ---------------------------------------------------------------------------
# git honours http(s)_proxy (via libcurl), and git is in every claude-man image — so `git ls-remote`
# is a dependency-light egress probe that works without a token. github.com is in the base allowlist;
# example.com never is.
ALLOWED_PROBE_URL = "https://github.com/git/git"
DENIED_PROBE_URL = "https://example.com/blocked-by-claude-man"


def egress_probe_argv(slug: str, url: str) -> list[str]:
    """`docker exec <agent> git ls-remote <url> HEAD` — a tokenless egress probe (git uses the proxy)."""
    return ["docker", "exec", config.container_name(slug), "git", "ls-remote", url, "HEAD"]


def smoke_verdict(allowed_reachable: bool, denied_reachable: bool) -> tuple[bool, str]:
    """PURE pass/fail for the lock smoke: an allowlisted host MUST be reachable and a non-allowlisted
    host MUST be blocked. Both are required — a proxy that lets everything through (or nothing) fails."""
    if allowed_reachable and not denied_reachable:
        return True, "allowlisted host reachable; non-allowlisted host blocked"
    problems = []
    if not allowed_reachable:
        problems.append("allowlisted host (github.com) was NOT reachable — allowlist/proxy too strict")
    if denied_reachable:
        problems.append("non-allowlisted host (example.com) was NOT blocked — egress not enforced")
    return False, "; ".join(problems)


def smoke(slug: str) -> tuple[bool, list[str]]:
    """Verify a locked, running project's egress end-to-end (daemon-gated, like ``image smoke``).

    Probes an allowlisted host (must reach) and a non-allowlisted host (must be blocked) from inside
    the agent, then folds the two results through ``smoke_verdict``. Returns (ok, lines)."""
    lines: list[str] = []
    if not runner.is_running(slug):
        return False, [f"{slug}: container not running — `project lock {slug}` then `project up {slug}` first"]
    allowed = runner._run(egress_probe_argv(slug, ALLOWED_PROBE_URL), timeout=60)
    lines.append(f"allowed  {ALLOWED_PROBE_URL}  -> rc={allowed.returncode}")
    denied = runner._run(egress_probe_argv(slug, DENIED_PROBE_URL), timeout=60)
    lines.append(f"denied   {DENIED_PROBE_URL}  -> rc={denied.returncode}")
    blocked = denied_requests(slug)
    if blocked:
        lines.append("proxy denied-log: " + ", ".join(blocked))
    ok, detail = smoke_verdict(allowed.returncode == 0, denied.returncode == 0)
    lines.append(("PASS: " if ok else "FAIL: ") + detail)
    return ok, lines


# `docker logs --tail N` (non-follow) returns promptly in the normal case; the timeout only guards a
# wedged daemon so the repeating TUI Network-panel poll can't pile up blocked reader threads.
_LOGS_TIMEOUT_S = 15


def _proxy_logs(slug: str, *, tail: int) -> str | None:
    """Newest ``tail`` lines of the sidecar's combined stdout+stderr, or ``None`` if it can't be read
    (no sidecar / wedged daemon). squid logs every request — allowed and denied — to stdout."""
    cp = runner._run(["docker", "logs", "--tail", str(tail), config.proxy_container_name(slug)],
                     timeout=_LOGS_TIMEOUT_S)
    if cp.returncode != 0:
        return None
    return (cp.stdout or "") + "\n" + (cp.stderr or "")


def denied_requests(slug: str, *, tail: int = 500) -> list[str]:
    """The hosts the locked project tried to reach but the allowlist blocked (newest log tail).

    Reads ``docker logs`` of the sidecar (squid logs denials to stdout) and parses them. Empty when the
    sidecar isn't running / has logged nothing — the operator surfaces this in the TUI to tune the
    allowlist."""
    log = _proxy_logs(slug, tail=tail)
    return parse_denied(log) if log is not None else []


def access_log(slug: str, *, tail: int = 500) -> list[AccessRecord]:
    """All recent proxy access records (allowed + denied) for a locked project — the data source for
    the TUI Network panel's Blocked/Allowed counts. Reads ``docker logs --tail`` of the sidecar and
    parses it; empty when the sidecar isn't running / has logged nothing."""
    log = _proxy_logs(slug, tail=tail)
    return parse_access(log) if log is not None else []
