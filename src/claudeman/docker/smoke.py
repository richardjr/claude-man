"""Smoke-test a built image under the FULL hardened profile (review IMG-3).

This is the gate ARCHITECTURE.md / SECURITY.md rely on to prove invariant 2 holds *at runtime*:
a throwaway container created with the SAME hardened argv the real launcher uses
(``runner.build_create_argv`` — ``--read-only --cap-drop ALL --user 1000:1000`` + the tmpfs/bind
set), then a battery of ``docker exec`` probes run as the container's uid 1000. It fails on
``EROFS`` / ``getpwuid`` / permission errors so a broken image — e.g. the IMG-1 symlink bug that
strands ``claude`` under root-only ``/root`` — is caught before any project trusts the image.

A build-time ``claude --version`` (which runs as root) does NOT exercise this; only an unprivileged
exec under the read-only rootfs does.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from .. import config
from ..registry import profiles as profiles_registry
from ..registry.schema import Project
from . import images, runner

# Substrings in any probe's combined output that fail the smoke regardless of exit code.
_FORBIDDEN = (
    "EROFS",
    "Read-only file system",
    "getpwuid",
    "Permission denied",
    "Operation not permitted",
    "cannot find name for user ID",
)

@dataclass(frozen=True)
class Probe:
    name: str
    argv: list[str]            # the command to exec inside the container
    required: bool             # required probes fail the smoke on non-zero exit
    expect: str = ""           # if set, combined output must contain this substring
    timeout: int = 15          # per-probe wall-clock ceiling (best-effort probes may want network)


@dataclass
class SmokeResult:
    ok: bool
    overlay: str
    lines: list[str] = field(default_factory=list)


def _base_probes() -> list[Probe]:
    return [
        # claude must resolve AND execute as the agent user (catches IMG-1: the binary
        # stranded under root-only /root, unreachable by uid 1000).
        Probe("claude --version", ["claude", "--version"], required=True),
        # whoami exercises getpwuid(1000) — fails loudly if the /etc/passwd entry is missing.
        Probe("passwd entry (getpwuid)", ["whoami"], required=True, expect="agent"),
        # ripgrep must be the apt binary on the read-only path, not an extracted temp copy.
        Probe("ripgrep is /usr/bin/rg", ["sh", "-lc", "command -v rg"], required=True,
              expect="/usr/bin/rg"),
        # writable surfaces actually accept writes (the .claude bind + the state tmpfs).
        Probe("writable .claude bind", ["sh", "-lc", "touch /home/agent/.claude/.smoke && echo ok"],
              required=True, expect="ok"),
        # the .cache tmpfs must be agent-WRITABLE (XDG_CACHE_HOME + claude's XDG_STATE_HOME live here,
        # and node/corepack mkdir ~/.cache/node) — a root:root 755 tmpfs fails this with EACCES.
        Probe("writable .cache tmpfs", ["sh", "-lc", "mkdir -p /home/agent/.cache/node && echo ok"],
              required=True, expect="ok"),
        # gh must be installed + runnable as the agent (Debian has no `gh` package — it's the upstream .deb).
        Probe("gh present", ["gh", "--version"], required=True, expect="gh version"),
        # `git config --global` must write somewhere (GIT_CONFIG_GLOBAL -> writable .cache), not EROFS the
        # read-only ~/.gitconfig — else in-container git is unusable.
        Probe("git config --global writable",
              ["sh", "-lc", "git config --global user.email smoke@example.com && echo ok"],
              required=True, expect="ok"),
        # claude doctor surfaces config/runtime write-path errors; best-effort (may want network).
        Probe("claude doctor", ["claude", "doctor"], required=False, timeout=20),
    ]


def classify(probe: Probe, rc: int, out: str) -> tuple[bool, str, str]:
    """Pure verdict for one probe result → (failed, mark, detail). No docker/IO.

    A forbidden marker (EROFS/getpwuid/…) fails ANY probe regardless of exit code. A *required*
    probe also fails on a non-zero exit or a missing ``expect`` substring; a best-effort probe
    only warns on those.
    """
    first = out.strip().splitlines()[0][:80] if out.strip() else ""
    forbidden = next((m for m in _FORBIDDEN if m in out), None)
    bad_exit = probe.required and rc != 0
    bad_expect = probe.required and bool(probe.expect) and probe.expect not in out
    failed = bool(forbidden) or bad_exit or bad_expect
    mark = "FAIL" if failed else ("ok  " if rc == 0 else "warn")
    detail = f"forbidden marker {forbidden!r}: {first}" if forbidden else first
    return failed, mark, detail


def _exec(container: str, probe: Probe) -> tuple[int, str]:
    cp = subprocess.run(
        ["docker", "exec", "-u", f"{config.CONTAINER_UID}:{config.CONTAINER_GID}", container, *probe.argv],
        capture_output=True, text=True, check=False, timeout=probe.timeout,
    )
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


def _resolve_token() -> str | None:
    """A token from the default profile, if one has been minted (enables the one-shot probe)."""
    prof = profiles_registry.default_profile()
    if prof is None:
        return None
    path = config.profile_token_path(prof.name)
    if not path.exists():
        return None
    token = path.read_text(encoding="utf-8").strip()
    return token or None


def smoke(overlay: str) -> SmokeResult:
    """Create a throwaway hardened container from ``claude-man:<overlay>`` and probe it."""
    res = SmokeResult(ok=False, overlay=overlay)

    if shutil.which("docker") is None:
        res.lines.append("docker not found on PATH")
        return res
    image = config.image_tag(overlay)
    if not images.image_exists(overlay):
        res.lines.append(f"image {image!r} not built — run `claudemanctl image build {overlay}`")
        return res

    slug = f"smoke-{overlay}"
    project = Project(slug=slug, overlay=overlay)
    container = project.container
    token = _resolve_token()

    probes = _base_probes()
    if token:
        probes.append(Probe("one-shot claude -p (auth+egress)",
                            ["claude", "-p", "reply with the single word ok"],
                            required=False, timeout=60))

    with tempfile.TemporaryDirectory(prefix="claude-man-smoke-") as tmp:
        cfg_dir, ws_dir = f"{tmp}/claude-config", f"{tmp}/workspace"
        os.makedirs(cfg_dir, exist_ok=True)
        os.makedirs(ws_dir, exist_ok=True)

        argv = runner.build_create_argv(
            project, profile_name="smoke", created_iso="smoke",
            claude_config_path=cfg_dir, workspace_path=ws_dir,
            inject_token=bool(token),
        )
        env = dict(os.environ)
        for key in config.SCRUBBED_ENV_KEYS:
            env.pop(key, None)
        if token:
            env[runner.OAUTH_TOKEN_ENV] = token

        # Clean any leftover from a prior run, then create + start.
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
        created = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
        if created.returncode != 0:
            res.lines.append(f"docker create failed: {created.stderr.strip()}")
            return res
        try:
            started = subprocess.run(["docker", "start", container],
                                     capture_output=True, text=True, check=False)
            if started.returncode != 0:
                res.lines.append(f"docker start failed: {started.stderr.strip()}")
                return res

            ok = True
            for probe in probes:
                try:
                    rc, out = _exec(container, probe)
                except subprocess.TimeoutExpired:
                    rc, out = 124, "(timed out)"
                failed, mark, detail = classify(probe, rc, out)
                res.lines.append(f"[{mark}] {probe.name}: {detail}")
                if failed:
                    ok = False
            res.ok = ok
        finally:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)

    return res
