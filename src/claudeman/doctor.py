"""Host prerequisite checks — the engine behind ``claudemanctl doctor`` and the TUI setup wizard.

Nothing here existed before issue #31: a fresh machine surfaced a missing docker daemon as
``failed to build claude-man:base (docker build exited 1)`` and a missing host ``claude`` as a
bare ``[Errno 2] No such file or directory: 'claude'``. This module turns each prerequisite into
a ``CheckResult`` with a factual detail line and an actionable fix hint.

Pure/impure split (the ``updates.py`` / ``ssh_agent.py`` pattern): the ``classify_*`` functions
are pure verdicts over primitives — unit-testable with no docker/network — and the ``probe_*``
wrappers shell out but NEVER raise; every failure folds into a result. Platform branching comes
in as arguments resolved via ``hostplatform`` (no inline ``sys.platform``). No textual imports —
the CLI and the dependency-free tests use this module directly.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass

from . import config, hostplatform
from .registry import profiles as profiles_registry

OK, WARN, FAIL = "ok", "warn", "fail"

_PROBE_TIMEOUT_S = 6.0
# Second-attempt budget for `docker version` after a first-attempt timeout (issue #34). A timeout
# never means "daemon down" — a down daemon with no socket is a fast `Cannot connect` rc 1. It means
# the connection was ACCEPTED and not answered: a socket-activated dockerd (`docker.socket` enabled,
# `docker.service` not — the Arch default) that systemd is starting for its FIRST client, which on a
# fresh boot is the TUI's startup probe. dockerd takes ~8 s to `API listen` (buildkit init dominates),
# so a lone 6 s attempt false-FAILed every morning. The retry waits out a cold start; only a daemon
# that stays silent for the whole budget is reported as hung.
_DOCKER_COLD_START_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class CheckResult:
    id: str        # "platform" | "docker" | "claude" | "image" | "terminal" | "profiles" | "config"
    label: str
    status: str    # OK | WARN | FAIL
    detail: str    # one factual line
    hint: str = ""  # the actionable fix ("" when nothing to do)


@dataclass(frozen=True)
class Report:
    checks: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        """No FAIL present (WARNs — e.g. no image built yet — don't block a working setup)."""
        return all(c.status != FAIL for c in self.checks)

    def get(self, check_id: str) -> CheckResult | None:
        return next((c for c in self.checks if c.id == check_id), None)


# ---------------------------------------------------------------------------
# Pure classifiers.
# ---------------------------------------------------------------------------
def classify_platform(*, macos: bool, wsl: bool) -> CheckResult:
    name = "macOS (Docker Desktop)" if macos else "Windows (WSL2)" if wsl else "Linux"
    return CheckResult("platform", "Platform", OK, name)


def _docker_install_hint(*, macos: bool, wsl: bool) -> str:
    if macos:
        return "install Docker Desktop — https://docs.docker.com/desktop/"
    if wsl:
        return ("install Docker Desktop with the WSL2 backend (or docker-ce inside the distro) — "
                "https://docs.docker.com/desktop/")
    return ("install Docker Engine — https://docs.docker.com/engine/install/ — then "
            "`sudo systemctl enable --now docker`")


def classify_docker(*, which_found: bool, rc: int | None, stdout: str, stderr: str,
                    macos: bool = False, wsl: bool = False,
                    slow_start_s: float | None = None) -> CheckResult:
    """Verdict over a ``docker version --format {{.Server.Version}}`` attempt.

    The three fresh-machine states are deliberately distinguished — binary missing, daemon down,
    and socket permission denied each get a different fix hint (they all used to surface as the
    same silent empty table / opaque build failure).

    ``slow_start_s`` is set by the probe when its FIRST attempt timed out and it retried: the total
    seconds waited across both attempts. With ``rc == 0`` that is a socket-activated cold start the
    retry waited out (OK, and the detail says so); with ``rc is None`` the daemon stayed silent for
    the whole cold-start budget (issue #34)."""
    if not which_found:
        return CheckResult("docker", "Docker", FAIL, "docker not found on PATH",
                           _docker_install_hint(macos=macos, wsl=wsl))
    if rc == 0:
        server = stdout.strip()
        detail = f"daemon reachable (server {server})" if server else "daemon reachable"
        if slow_start_s is not None:
            detail += f" — answered after {slow_start_s:.0f}s (cold start)"
        return CheckResult("docker", "Docker", OK, detail)
    err = stderr.strip().splitlines()[0] if stderr.strip() else ""
    if "permission denied" in stderr.lower():
        return CheckResult(
            "docker", "Docker", FAIL, err or "docker socket permission denied",
            "add yourself to the docker group: `sudo usermod -aG docker $USER`, "
            "then log out and back in")
    if rc is None:
        # The socket accepted the connection but nothing answered — a hung daemon, or one still
        # starting past the retry budget. NOT "daemon down" (that is a fast `Cannot connect` rc 1),
        # so the hint is status/restart, never a bare `start`.
        waited = f"{slow_start_s:.0f}s" if slow_start_s is not None else "the probe budget"
        detail = f"docker version got no answer in {waited} — daemon hung or still starting"
        hint = ("restart Docker Desktop if it stays unresponsive" if (macos or wsl)
                else "check `systemctl status docker`; `sudo systemctl restart docker` if it is hung")
        return CheckResult("docker", "Docker", FAIL, detail, hint)
    detail = err or f"docker daemon not reachable (docker version exited {rc})"
    hint = ("start Docker Desktop" if (macos or wsl)
            else "start the daemon: `sudo systemctl start docker`")
    return CheckResult("docker", "Docker", FAIL, detail, hint)


def classify_claude(*, which_found: bool, rc: int | None, stdout: str) -> CheckResult:
    """Verdict over a host ``claude --version`` attempt. WARN, never FAIL — the host claude is
    only needed to mint profile tokens (`claude setup-token`), not to run containers."""
    if not which_found:
        return CheckResult(
            "claude", "Claude CLI", WARN, "claude not found on PATH",
            "needed once per account to mint profile tokens — install Claude Code on the host: "
            "https://claude.com/claude-code")
    if rc == 0:
        version = stdout.strip().splitlines()[0] if stdout.strip() else "version unknown"
        return CheckResult("claude", "Claude CLI", OK, version)
    detail = ("claude --version timed out" if rc is None
              else f"claude --version exited {rc}")
    return CheckResult("claude", "Claude CLI", WARN, detail,
                       "reinstall Claude Code on the host, or check `claude doctor`")


def classify_terminal(resolved_name: str | None, error: str) -> CheckResult:
    if resolved_name:
        return CheckResult("terminal", "Terminal", OK, f"launcher: {resolved_name}")
    return CheckResult(
        "terminal", "Terminal", FAIL, error or "no terminal launcher resolvable",
        "pick one in the TUI (Settings `,` → `e`) or `claudemanctl config terminal`")


def classify_image(*, docker_ok: bool, exists: bool, claude_version: str | None) -> CheckResult:
    tag = config.image_tag(config.DEFAULT_OVERLAY)
    if not docker_ok:
        return CheckResult("image", "Base image", WARN, "unknown (docker unavailable)")
    if not exists:
        return CheckResult(
            "image", "Base image", WARN, f"{tag} not built",
            "built automatically on first project create, or `claudemanctl image build base`")
    detail = f"{tag} built" + (f" (claude {claude_version})" if claude_version else "")
    return CheckResult("image", "Base image", OK, detail)


def classify_profiles(profiles: Sequence[tuple[str, bool]]) -> CheckResult:
    """``profiles`` = (name, has_token) pairs. WARN, never FAIL — a profileless install runs
    containers fine; only the in-container claude auth needs a minted token."""
    if not profiles:
        return CheckResult(
            "profiles", "Profiles", WARN, "no account profiles defined",
            "create one in the TUI setup wizard (Settings `,` → `w`) or "
            "`claudemanctl profile add <name> --default`")
    tokenless = [name for name, has_token in profiles if not has_token]
    names = ", ".join(name for name, _ in profiles)
    if tokenless:
        return CheckResult(
            "profiles", "Profiles", WARN,
            f"{len(profiles)} profile(s) ({names}) — no token for: {', '.join(tokenless)}",
            "mint one with `claudemanctl profile renew <name>`")
    return CheckResult("profiles", "Profiles", OK, f"{len(profiles)} profile(s) ({names})")


def classify_config(*, exists: bool, path: str) -> CheckResult:
    if exists:
        return CheckResult("config", "Config", OK, path)
    return CheckResult("config", "Config", OK,
                       "not created yet — defaults in effect (the setup wizard or any "
                       "`config` change creates it)")


# ---------------------------------------------------------------------------
# Impure probes — never raise.
# ---------------------------------------------------------------------------
def _run(argv: list[str], timeout: float) -> tuple[int | None, str, str]:
    """rc (None on timeout), stdout, stderr. The binary is which-checked by the caller."""
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", ""
    except OSError as exc:
        return 127, "", str(exc)
    return cp.returncode, cp.stdout, cp.stderr


def probe_docker(timeout: float = _PROBE_TIMEOUT_S,
                 cold_start_timeout: float = _DOCKER_COLD_START_TIMEOUT_S) -> CheckResult:
    """One `docker version` attempt within ``timeout``; on a TIMEOUT (connection accepted, no
    answer) one retry within ``cold_start_timeout`` so a socket-activated daemon that systemd is
    still starting for us is waited out instead of declared dead (issue #34). Every other outcome
    — rc 0, `Cannot connect`, permission denied — is final on the first attempt."""
    macos, wsl = hostplatform.is_macos(), hostplatform.is_wsl()
    if shutil.which("docker") is None:
        return classify_docker(which_found=False, rc=None, stdout="", stderr="",
                               macos=macos, wsl=wsl)
    argv = ["docker", "version", "--format", "{{.Server.Version}}"]
    started = time.monotonic()
    rc, out, err = _run(argv, timeout)
    slow_start_s: float | None = None
    if rc is None:
        rc, out, err = _run(argv, cold_start_timeout)
        slow_start_s = time.monotonic() - started
    return classify_docker(which_found=True, rc=rc, stdout=out, stderr=err, macos=macos, wsl=wsl,
                           slow_start_s=slow_start_s)


def probe_claude(timeout: float = _PROBE_TIMEOUT_S) -> CheckResult:
    if shutil.which("claude") is None:
        return classify_claude(which_found=False, rc=None, stdout="")
    rc, out, _err = _run(["claude", "--version"], timeout)
    return classify_claude(which_found=True, rc=rc, stdout=out)


def probe_terminal() -> CheckResult:
    from .tui import terminals  # textual-free module under tui/ — one resolution truth

    try:
        return classify_terminal(terminals.resolve_spec().name, "")
    except RuntimeError as exc:
        return classify_terminal(None, str(exc))


def probe_image(*, docker_ok: bool) -> CheckResult:
    from .docker import images

    if not docker_ok:
        return classify_image(docker_ok=False, exists=False, claude_version=None)
    try:
        exists = images.image_exists(config.DEFAULT_OVERLAY)
        version = images.image_claude_version(config.DEFAULT_OVERLAY) if exists else None
    except OSError:
        exists, version = False, None
    return classify_image(docker_ok=True, exists=exists, claude_version=version)


def probe_profiles() -> CheckResult:
    try:
        pairs = tuple((name, profiles_registry.load_token(name) is not None)
                      for name in profiles_registry.list_names())
    except OSError:
        pairs = ()
    return classify_profiles(pairs)


def probe_config() -> CheckResult:
    path = config.settings_toml_path()
    return classify_config(exists=path.exists(), path=str(path))


def run_all() -> Report:
    """Every check, fixed order. Never raises; blocks up to a few seconds on the subprocess
    probes (up to the docker cold-start budget when the daemon is being socket-activated or is
    hung) — call off the UI thread from the TUI."""
    macos, wsl = hostplatform.is_macos(), hostplatform.is_wsl()
    docker = probe_docker()
    return Report((
        classify_platform(macos=macos, wsl=wsl),
        docker,
        probe_claude(),
        probe_image(docker_ok=docker.status == OK),
        probe_terminal(),
        probe_profiles(),
        probe_config(),
    ))
