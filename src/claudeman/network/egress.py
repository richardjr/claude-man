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
PURE argv renderers (``*_argv``) + ``parse_denied`` are unit-tested without a daemon; the thin
wrappers reuse ``docker/runner._run`` (which maps a missing docker / a wedged daemon to a non-zero
result rather than raising).
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


def parse_denied(log: str) -> list[str]:
    """Extract denied destinations from squid's access log (``docker logs <proxy>`` output).

    squid's default ``squid`` logformat is::

        <ts> <elapsed> <client> <result>/<status> <bytes> <method> <url> <user> <hier>/<srv> <type>

    A blocked request is ``TCP_DENIED/403`` (or any ``/403``). Returns the requested hosts in first-
    seen order, de-duplicated — the list the operator would add to ``egress.allowlist`` if legitimate.
    PURE so it is unit-tested against sample log text.
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in log.splitlines():
        parts = line.split()
        if len(parts) < 7:
            continue
        result = parts[3]
        if "TCP_DENIED" not in result and not result.endswith("/403"):
            continue
        url = parts[6]                     # method is parts[5]; url is parts[6]
        host = url.split("://", 1)[-1]     # strip scheme for a plain-HTTP url
        host = host.split("/", 1)[0]       # drop any path
        # CONNECT targets are host:port (e.g. `example.com:443`) — strip the port so the result is a
        # bare host the operator can paste straight into egress.allowlist (a `dstdomain host:port` is
        # invalid). HTTPS (the common case) is always CONNECT, so this matters.
        h, sep, port = host.rpartition(":")
        if sep and h and port.isdigit():
            host = h
        if host and host not in seen:
            seen.add(host)
            out.append(host)
    return out


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


def denied_requests(slug: str, *, tail: int = 500) -> list[str]:
    """The hosts the locked project tried to reach but the allowlist blocked (newest log tail).

    Reads ``docker logs`` of the sidecar (squid logs denials to stdout) and parses them. Empty when the
    sidecar isn't running / has logged nothing — the operator surfaces this in the TUI to tune the
    allowlist."""
    cp = runner._run(["docker", "logs", "--tail", str(tail), config.proxy_container_name(slug)])
    if cp.returncode != 0:
        return []
    return parse_denied((cp.stdout or "") + "\n" + (cp.stderr or ""))
