"""Host-side ssh-agent bootstrap: ensure an agent is reachable and the configured keys are loaded.

HOST-ONLY (CLAUDE.md invariant 1 family): private keys NEVER enter a container. claude-man runs
``ssh-add`` on the HOST, and a project's ``ssh`` env-mount forwards the agent SOCKET (a path, not a
secret) into the container. This module lets the TUI/CLI "fully load config needs into the environment"
— so the operator doesn't have to ``ssh-add`` by hand before using claude-man.

Two design choices (operator-confirmed):
  * When no session agent exists, claude-man STARTS a managed agent at a stable socket under the state
    dir and exports ``SSH_AUTH_SOCK`` into ``os.environ`` — so every container created this session
    forwards it (``docker/runner.py`` reads ``os.environ["SSH_AUTH_SOCK"]`` at create time). The managed
    agent daemonizes and PERSISTS across TUI restarts at that stable socket (only torn down on reboot or
    an explicit kill), so a container forwarding it keeps working after a restart — no recreate needed.
    The keys it holds stay loaded until the operator removes them (``ssh-add -d``) or kills the agent.
    A socket at the managed path is adopted ONLY if it is a socket owned by the current uid, and its
    parent dir is forced to ``0700`` — so another local user can't pre-plant an agent there and capture
    the operator's keys.
  * ``ssh-add`` is ALWAYS run non-interactively (no controlling tty + a forced-failing ``SSH_ASKPASS``)
    so a passphrase-protected key fails fast with a clear message instead of grabbing the terminal and
    corrupting the TUI. Passphraseless keys load silently.

The pure parser (fingerprint extraction) is split from the subprocess shell so it is unit-testable
without ssh installed.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config

AGENT_TIMEOUT_S = 10.0
# A SHA256 key fingerprint as printed by `ssh-add -l` / `ssh-keygen -lf` (the field after the bit count).
_FP_RE = re.compile(r"\bSHA256:[A-Za-z0-9+/=]+")


@dataclass(frozen=True)
class KeyResult:
    path: str
    ok: bool
    detail: str  # "loaded" | "already loaded" | "missing" | "needs passphrase …" | "no ssh-agent" | …


# ---------------------------------------------------------------------------
# Pure layer (unit-tested without ssh)
# ---------------------------------------------------------------------------
def parse_fingerprints(text: str) -> set[str]:
    """Pure: collect SHA256 key fingerprints from ``ssh-add -l`` / ``ssh-keygen -lf`` output."""
    return set(_FP_RE.findall(text or ""))


def expand(path: str) -> str:
    """A host key path with ``~`` and ``$VARS`` expanded (the form ssh tools want)."""
    return os.path.expanduser(os.path.expandvars(path)) if path else ""


# ---------------------------------------------------------------------------
# Thin subprocess shell (needs ssh / ssh-agent; not unit-tested)
# ---------------------------------------------------------------------------
def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=AGENT_TIMEOUT_S, **kw
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", f"{argv[0]} timed out")
    except FileNotFoundError:
        return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]} not found on PATH")


def agent_reachable() -> bool:
    """True if ``SSH_AUTH_SOCK`` points at a responsive agent (``ssh-add -l`` rc 0=has keys / 1=empty;
    rc 2 = could not connect to any agent)."""
    return _run(["ssh-add", "-l"]).returncode in (0, 1)


def _managed_socket_is_ours(sock: str) -> bool:
    """True iff ``sock`` exists, is a unix socket, and is owned by the current uid.

    The load-bearing guard before adopting a socket at the managed path: a socket another local user
    pre-planted there (a live ``ssh-agent -a`` of theirs) would otherwise capture our private keys when
    ``ensure_keys`` ``ssh-add``s into it — and ``runner`` would bind THAT socket into every container.
    We only ever adopt a socket we own. (A concurrent claude-man that won a start race bound the socket
    as the *same* uid, so adopting it is correct; a foreign socket is refused.)
    """
    getuid = getattr(os, "getuid", None)
    if getuid is None:  # non-POSIX: no unix sockets / ssh-agent anyway — never adopt
        return False
    try:
        st = os.stat(sock)
    except OSError:
        return False
    return stat.S_ISSOCK(st.st_mode) and st.st_uid == getuid()


def ensure_agent() -> str | None:
    """Ensure an agent is reachable; set + return ``SSH_AUTH_SOCK`` (``os.environ`` is updated so
    later container creates forward it). Prefers an existing session agent; else (re)uses or starts a
    managed agent at ``config.managed_ssh_agent_sock()``. Returns ``None`` if none could be established
    (e.g. ``ssh-agent`` not installed)."""
    if os.environ.get("SSH_AUTH_SOCK") and agent_reachable():
        return os.environ["SSH_AUTH_SOCK"]  # an existing session agent — use it as-is
    sock = str(config.managed_ssh_agent_sock())
    # Harden the parent dir to 0700 BEFORE we trust anything at the managed path (mkdir's mode is
    # umask-masked, so chmod explicitly) — closes the window for another user to plant a socket there.
    try:
        parent = Path(sock).parent
        parent.mkdir(parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
    except OSError:
        pass
    os.environ["SSH_AUTH_SOCK"] = sock
    if _managed_socket_is_ours(sock) and agent_reachable():
        return sock  # our managed agent from a previous run is still alive — reuse it
    # Clear only OUR own dead socket file (never a foreign one) so `ssh-agent -a` can bind.
    try:
        if _managed_socket_is_ours(sock):
            Path(sock).unlink()
    except OSError:
        pass
    _run(["ssh-agent", "-a", sock])  # daemonizes + persists; we know the socket, so don't parse stdout
    # Adopt ONLY if the now-bound socket is ours: a concurrent claude-man that won the race bound it as
    # our uid (safe), but a foreign socket that blocked our bind must be refused, not adopted.
    if _managed_socket_is_ours(sock) and agent_reachable():
        return sock
    os.environ.pop("SSH_AUTH_SOCK", None)
    return None


def loaded_fingerprints() -> set[str]:
    """Fingerprints currently loaded in the agent (empty set if no agent / none loaded)."""
    cp = _run(["ssh-add", "-l"])
    return parse_fingerprints(cp.stdout) if cp.returncode in (0, 1) else set()


def fingerprint_of(key_path: str) -> str | None:
    """The SHA256 fingerprint of a host key (private or public), or None if it can't be read."""
    cp = _run(["ssh-keygen", "-lf", expand(key_path)])
    if cp.returncode != 0:
        return None
    fps = parse_fingerprints(cp.stdout)
    return next(iter(fps), None)


def add_key(key_path: str) -> tuple[bool, str]:
    """``ssh-add`` a key NON-INTERACTIVELY so a passphrase key fails fast instead of prompting.

    No controlling terminal (``start_new_session``) + ``stdin`` closed + a forced-failing
    ``SSH_ASKPASS`` means ssh-add can never grab ``/dev/tty`` and corrupt the TUI: a passphraseless
    key loads; a passphrase key returns non-zero with a clear message.
    """
    env = dict(os.environ)
    env["SSH_ASKPASS"] = "/bin/false"        # passphrase path: ask via a program that always fails
    env["SSH_ASKPASS_REQUIRE"] = "force"     # OpenSSH ≥ 8.4: use askpass even with a tty present
    env.setdefault("DISPLAY", ":0")          # older ssh only consults askpass when DISPLAY is set
    cp = _run(["ssh-add", expand(key_path)], env=env,
              stdin=subprocess.DEVNULL, start_new_session=True)
    if cp.returncode == 0:
        return True, "loaded"
    out = (cp.stderr or cp.stdout or "").strip()
    last = out.splitlines()[-1].strip() if out else ""
    # A passphrase key without an interactive prompt fails here — name it actionably.
    return False, f"needs passphrase (run `ssh-add {key_path}` manually)" if not last else last


def key_status(path: str, loaded: set[str]) -> str:
    """One-word load status for a configured key path: ``missing`` | ``loaded`` | ``not loaded``.

    ``loaded`` is the set from ``loaded_fingerprints()`` — passed in so a caller queries the agent once
    for a whole list. Shared by the CLI ``config show`` and the TUI Settings screen so they can't drift.
    """
    if not os.path.exists(expand(path)):
        return "missing"
    fp = fingerprint_of(path)
    return "loaded" if (fp and fp in loaded) else "not loaded"


def discover_ssh_keys() -> list[str]:
    """Best-effort list of host private keys under ``~/.ssh`` (each ``*.pub`` with a private sibling).

    Used to populate the TUI's add-key picker — "selecting keys" rather than typing paths."""
    ssh_dir = Path(expand("~/.ssh"))
    out: list[str] = []
    if not ssh_dir.is_dir():
        return out
    for pub in sorted(ssh_dir.glob("*.pub")):
        priv = pub.with_suffix("")  # strip ".pub"
        if priv.is_file():
            out.append(str(priv))
    return out


def ensure_keys(keys: list[str] | tuple[str, ...]) -> list[KeyResult]:
    """Ensure each configured key is loaded into the agent (idempotent). Starts/uses an agent first.

    A missing key file -> ``missing`` (not loaded); an already-loaded key (matched by fingerprint) is a
    no-op; otherwise it is ``ssh-add``'d. Returns one ``KeyResult`` per key. Never raises — the TUI
    bootstrap surfaces the per-key detail."""
    if not keys:
        return []
    if ensure_agent() is None:
        return [KeyResult(k, False, "no ssh-agent available (is openssh installed?)") for k in keys]
    loaded = loaded_fingerprints()
    results: list[KeyResult] = []
    for k in keys:
        if not os.path.exists(expand(k)):
            results.append(KeyResult(k, False, "missing"))
            continue
        fp = fingerprint_of(k)
        if fp and fp in loaded:
            results.append(KeyResult(k, True, "already loaded"))
            continue
        ok, detail = add_key(k)
        if ok and fp:
            loaded.add(fp)  # so a duplicate path later in the list is reported "already loaded"
        results.append(KeyResult(k, ok, detail))
    return results
