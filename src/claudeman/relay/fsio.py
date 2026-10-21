"""Host-side IO into a project's ``/workspace/.relay`` mailbox — the relay's attack surface.

Everything under the workspace bind is CONTAINER-WRITABLE: the agent can plant a symlink, a FIFO, a
hard link or a huge file anywhere in it, and swap a directory for a symlink between two host calls.
A naive host ``open(path)`` would follow an in-container symlink to ``/home/<operator>/.ssh/…`` — the
host resolves absolute symlink targets in the HOST namespace — turning the relay into a read/exfil
or write primitive against the operator's machine. So every operation here is:

* **fd-anchored**: the workspace dir (a host path the container can't replace — its parent isn't
  mounted) is opened once, and each component below it is opened relative to its parent's fd with
  ``O_NOFOLLOW | O_DIRECTORY``; a component that is not a real directory is removed (unlink — never
  followed) and recreated;
* **no-follow on the leaf**: reads open with ``O_NOFOLLOW | O_NONBLOCK`` and require a regular,
  single-link file under a size cap; writes go to an ``O_EXCL`` temp file then ``rename`` (which
  replaces a planted symlink rather than writing through it).

Never raises ``OSError`` to the relay loop for a hostile layout — a refusal comes back as data.
"""

from __future__ import annotations

import os
import secrets
import shutil
import stat
import time
from dataclasses import dataclass

from .. import config
from . import protocol

OUT = "out"
IN = "in"
BIN = "bin"
DIRECTORY = "directory.json"
HELPER = "relay"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


@dataclass(frozen=True)
class OutboxItem:
    request_id: str
    data: bytes = b""
    error: str = ""          # set when the entry was refused unread (symlink / not a file / too big)


def _open_workspace(slug: str) -> int:
    return os.open(config.workspace_dir(slug), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)


def _open_child_dir(parent_fd: int, name: str, *, create: bool) -> int | None:
    """Open ``name`` under ``parent_fd`` as a real directory (never through a symlink). With
    ``create``, a missing component is made and a non-directory one (symlink, file, FIFO) is unlinked
    and replaced; without it, anything but a real directory → None."""
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            return None
    except (NotADirectoryError, OSError) as exc:
        # ELOOP (symlink) / ENOTDIR (a file): refuse, and with create= replace it.
        if not create or isinstance(exc, PermissionError):
            return None
        try:
            st = os.lstat(name, dir_fd=parent_fd)
        except OSError:
            return None
        if stat.S_ISDIR(st.st_mode):
            return None          # a real dir we couldn't open (permissions) — leave it alone
        os.unlink(name, dir_fd=parent_fd)
    os.mkdir(name, 0o755, dir_fd=parent_fd)
    return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)


def _open_relay(slug: str, *parts: str, create: bool) -> int | None:
    """fd of ``<workspace>/.relay[/<parts>…]`` (each component a real directory), or None."""
    try:
        fd = _open_workspace(slug)
        for name in (config.RELAY_DIRNAME, *parts):
            try:
                child = _open_child_dir(fd, name, create=create)
            finally:
                os.close(fd)
            if child is None:
                return None
            fd = child
        return fd
    except OSError:
        return None


def ensure_layout(slug: str) -> bool:
    """Create ``.relay/{out,in,bin}`` (replacing any planted non-directory). → ok."""
    for sub in (OUT, IN, BIN):
        fd = _open_relay(slug, sub, create=True)
        if fd is None:
            return False
        os.close(fd)
    return True


def remove_layout(slug: str) -> None:
    """Remove the project's ``.relay`` tree (it stopped being a requester). A planted symlink is
    unlinked, never followed; ``shutil.rmtree`` is fd-based on Linux (symlink-attack safe)."""
    path = config.relay_dir(slug)
    try:
        st = os.lstat(path)
    except OSError:
        return
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            os.unlink(path)
        except OSError:
            pass


def has_layout(slug: str) -> bool:
    """Cheap check (no create) — the relay only scans projects that have a mailbox."""
    fd = _open_relay(slug, OUT, create=False)
    if fd is None:
        return False
    os.close(fd)
    return True


def read_outbox(slug: str, *, limit: int, max_bytes: int = protocol.REQUEST_MAX_BYTES) -> list[OutboxItem]:
    """Drain up to ``limit`` request files from ``out/``: each is read (if it is a safe regular file)
    and REMOVED, so a request is ingested at most once; the rest wait for a later pass (the caller's
    per-sender budget — back-pressure against a flooding container). Names that aren't request files
    (the helper's in-flight temp files) are left alone. Sorted by name (time-prefixed ids ≈ FIFO)."""
    fd = _open_relay(slug, OUT, create=False)
    if fd is None or limit <= 0:
        if fd is not None:
            os.close(fd)
        return []
    items: list[OutboxItem] = []
    try:
        names = os.listdir(fd)
        _sweep_junk(fd, names)
        for name in sorted(names):
            rid = protocol.id_from_filename(name)
            if rid is None:
                continue
            if len(items) >= limit:
                break
            items.append(_read_one(fd, name, rid, max_bytes))
            try:
                os.unlink(name, dir_fd=fd)
            except OSError:
                pass
    except OSError:
        pass
    finally:
        os.close(fd)
    return items


JUNK_AGE_S = 600       # a non-request outbox entry older than this is stale (a crashed write, junk)
_JUNK_PER_PASS = 500


def _sweep_junk(dir_fd: int, names: list[str]) -> None:
    """Unlink stale non-request entries from ``out/`` (never followed; directories are left alone) so
    junk can't pile up and slow every pass. Bounded work per pass."""
    now = time.time()
    swept = 0
    for name in names:
        if swept >= _JUNK_PER_PASS:
            return
        if protocol.id_from_filename(name) is not None:
            continue
        try:
            st = os.lstat(name, dir_fd=dir_fd)
            if not stat.S_ISDIR(st.st_mode) and now - st.st_mtime > JUNK_AGE_S:
                os.unlink(name, dir_fd=dir_fd)
                swept += 1
        except OSError:
            continue


def _read_one(dir_fd: int, name: str, rid: str, max_bytes: int) -> OutboxItem:
    try:
        f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
    except OSError as exc:
        return OutboxItem(rid, error=f"refused: not a readable regular file ({exc.strerror})")
    try:
        st = os.fstat(f)
        if not stat.S_ISREG(st.st_mode):
            return OutboxItem(rid, error="refused: not a regular file")
        if st.st_nlink != 1:
            return OutboxItem(rid, error="refused: a hard-linked file")
        if st.st_size > max_bytes:
            return OutboxItem(rid, error=f"refused: larger than {max_bytes} bytes")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(f, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                return OutboxItem(rid, error=f"refused: larger than {max_bytes} bytes")
            chunks.append(chunk)
        return OutboxItem(rid, data=b"".join(chunks))
    finally:
        os.close(f)


def _write_at(dir_fd: int, name: str, data: bytes, mode: int) -> None:
    tmp = f".{name}.{secrets.token_hex(6)}.tmp"
    f = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode,
                dir_fd=dir_fd)
    try:
        os.write(f, data)
        os.fchmod(f, mode)
    finally:
        os.close(f)
    try:
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except OSError:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise


def write_file(slug: str, sub: str | None, name: str, data: bytes, *, mode: int = 0o644) -> str:
    """Atomically write ``.relay/[<sub>/]<name>`` (a planted symlink at ``name`` is replaced, never
    written through). → '' or an error note."""
    parts = (sub,) if sub else ()
    fd = _open_relay(slug, *parts, create=True)
    if fd is None:
        return f"relay: could not open {config.RELAY_DIRNAME}/{sub or ''} in {slug}'s workspace"
    try:
        _write_at(fd, name, data, mode)
    except OSError as exc:
        return f"relay: could not write {name} for {slug} ({exc.strerror or exc})"
    finally:
        os.close(fd)
    return ""


def write_status(slug: str, request_id: str, data: bytes) -> str:
    return write_file(slug, IN, protocol.request_filename(request_id), data)


def remove_status(slug: str, request_id: str) -> None:
    """Drop a pruned request's status file from ``.relay/in/`` (unlink never follows). Best-effort."""
    fd = _open_relay(slug, IN, create=False)
    if fd is None:
        return
    try:
        os.unlink(protocol.request_filename(request_id), dir_fd=fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def read_workspace_text(slug: str, name: str, *, max_bytes: int = 1 << 20) -> str | None:
    """Read a workspace-ROOT file (the provider context file) without following a symlink. None when
    absent; raises ``OSError`` for a refused/unsafe entry so the caller doesn't overwrite blindly."""
    try:
        ws = _open_workspace(slug)
    except FileNotFoundError:
        return None
    try:
        try:
            f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=ws)
        except FileNotFoundError:
            return None
        try:
            st = os.fstat(f)
            if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
                raise OSError(f"{name} is not a regular file under {max_bytes} bytes")
            chunks: list[bytes] = []
            total = 0
            while True:      # bounded even if a running container keeps appending to it
                chunk = os.read(f, 65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise OSError(f"{name} grew past {max_bytes} bytes while being read")
                chunks.append(chunk)
        finally:
            os.close(f)
    finally:
        os.close(ws)
    return b"".join(chunks).decode("utf-8", errors="replace")


def write_workspace_text(slug: str, name: str, text: str) -> None:
    """Atomically write a workspace-ROOT file (temp + rename — replaces a planted symlink)."""
    ws = _open_workspace(slug)
    try:
        _write_at(ws, name, text.encode("utf-8"), 0o644)
    finally:
        os.close(ws)
