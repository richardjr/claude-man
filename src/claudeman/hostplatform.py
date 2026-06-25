"""Host-platform seams — Linux is the reference platform; macOS and Windows-via-WSL2 are hosts too.

The CONTAINER side is identical everywhere: the image is Linux regardless of the host OS (Docker
Desktop runs it in a Linux VM), so the hardened profile, the mounts, and the baked container paths
never vary. What differs per host is a small set of seams, centralised here as pure functions over
an explicit ``platform`` string (defaulting to the live ``sys.platform``) so every branch is
unit-testable on any OS:

  * which terminal emulators / file-manager openers exist (``tui/terminals.py`` consumes this)
  * how the host ssh-agent socket reaches a container (Docker Desktop on macOS cannot bind-mount an
    arbitrary host unix socket into its VM; it forwards the host's DEFAULT agent at a magic path)
  * whether host-side uid advisories are meaningful (Docker Desktop synthesises bind ownership)

Native Windows is intentionally NOT a target — Windows is supported via WSL2, where claude-man IS
Linux (plus the small WSL conveniences below). See README "Platform support".
"""

from __future__ import annotations

import functools
import os
import sys

# Docker Desktop (macOS) forwards the host's DEFAULT ssh-agent at this fixed path inside its VM; an
# arbitrary host socket path cannot cross the VM boundary, so this is the only agent a container can
# reach there. Mounted in place of $SSH_AUTH_SOCK by docker/runner.py on Darwin.
DOCKER_DESKTOP_SSH_SOCK = "/run/host-services/ssh-auth.sock"


def is_macos(platform: str | None = None) -> bool:
    return (platform or sys.platform) == "darwin"


@functools.lru_cache(maxsize=1)
def _wsl_kernel_hint() -> bool:
    """The WSL kernel self-identifies in /proc/version ("…-microsoft-standard-WSL2 …")."""
    try:
        with open("/proc/version", encoding="utf-8") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def is_wsl(platform: str | None = None) -> bool:
    """True when running inside WSL2 (a Linux process on a Microsoft kernel).

    ``WSL_DISTRO_NAME`` is set by the WSL launcher for every session; the /proc/version probe
    covers daemons/cron started outside one."""
    if (platform or sys.platform) != "linux":
        return False
    return bool(os.environ.get("WSL_DISTRO_NAME")) or _wsl_kernel_hint()


def uid_checks_meaningful(platform: str | None = None) -> bool:
    """Whether "host uid vs container uid" advisories mean anything on this host.

    On macOS, Docker Desktop's file sharing (VirtioFS) synthesises bind-mount ownership — files
    appear owned by whichever side asks — so host uid 501 vs container uid 1000 is NOT a problem
    there, and the Linux "dubious ownership" advisory would be permanent noise."""
    return not is_macos(platform)


def docker_ssh_auth_sock(host_sock: str | None, platform: str | None = None) -> str | None:
    """The HOST-side path to bind-mount as the container's ssh-agent socket (None = nothing to mount).

    Linux/WSL2: the live ``SSH_AUTH_SOCK`` — the daemon shares the host kernel, so any unix socket
    binds straight in (including claude-man's managed agent). macOS: always the Docker Desktop
    forwarded socket — the VM cannot reach an arbitrary host socket path, and Docker Desktop
    forwards the host's default (launchd) agent there. The managed-agent fallback therefore does
    not apply on macOS: keys must be loaded into the DEFAULT agent (``config ssh load`` does)."""
    if is_macos(platform):
        return DOCKER_DESKTOP_SSH_SOCK
    return host_sock


# The in-container hostname that resolves to the host's loopback — where a host-run model server
# (Ollama) listens. Same name everywhere; reachability is wired by ``host_gateway_create_args``.
HOST_GATEWAY_HOST = "host.docker.internal"


def host_loopback_host(platform: str | None = None) -> str:
    """The hostname a container uses to reach the host (Phase 9 — the gateway sidecar -> host Ollama)."""
    return HOST_GATEWAY_HOST


def host_gateway_create_args(platform: str | None = None) -> list[str]:
    """``--add-host`` so a container can resolve ``host.docker.internal`` to the host's loopback.

    Docker Desktop (macOS) resolves it AUTOMATICALLY -> ``[]``. Native Linux docker does NOT, so map it
    to the bridge gateway via docker's ``host-gateway`` magic value. Used by the Phase-9 gateway sidecar
    to reach the host Ollama daemon (the AGENT never needs it — it talks only to the in-network sidecar).
    """
    if is_macos(platform):
        return []
    return [f"--add-host={HOST_GATEWAY_HOST}:host-gateway"]
