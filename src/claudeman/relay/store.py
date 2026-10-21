"""The relay's host-only state (``~/.local/state/claude-man/relay`` — never mounted into a container).

* ``requests/<id>.json`` — one record per request (atomic replace on every change).
* ``events.jsonl`` — the APPEND-ONLY audit log: every ingest, approval, denial, start, finish,
  cancel, with who did it (a project slug, ``operator`` or ``relay``). Everything agents ask of each
  other is persisted and attributable; there is no private channel.
* ``.store.lock`` — serialises record mutations across processes (the TUI's relay, a
  ``claudemanctl relay run``, a ``relay approve`` from another shell).
* ``.relay.lock`` — held for life by the ONE process acting as the relay (``acquire_relay``).

Text fields that came from an agent (notes, replies, failure detail) are secret-masked line by line
(``syncback.denylist.mask_line``) before they are stored or carried to the other project.
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from uuid import uuid4

from .. import config
from ..syncback.denylist import mask_line
from . import protocol


RETENTION_S = 30 * 86400          # finished records older than this are pruned when a relay starts
REJECTED_RETENTION_S = 86400      # …rejected ones after a day (a flooding sender's garbage stays small)
_WRAP = 1000                      # hard-wrap longer lines before masking (mask_line's key/value regex
#                                   backtracks quadratically on very long runs)


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> float:
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


def mask(text: str) -> str:
    """Secret-mask text line by line (lines hard-wrapped at ``_WRAP`` first — bounded cost)."""
    out: list[str] = []
    for line in text.splitlines():
        while len(line) > _WRAP:
            out.append(mask_line(line[:_WRAP]))
            line = line[_WRAP:]
        out.append(mask_line(line))
    return "\n".join(out)


def clean(text: str, limit: int) -> str:
    """Agent-authored text (a note, a reply, a failure detail) made safe to store, show and carry to
    another project: terminal controls stripped, TRUNCATED to ``limit`` (before masking — masking an
    unbounded string is the expensive step), then secret-masked."""
    return mask(protocol.strip_controls(text or "")[:limit])


@dataclass
class Record:
    id: str
    sender: str
    target: str
    task: str
    status: str
    params: dict[str, str] = field(default_factory=dict)
    note: str = ""
    detail: str = ""
    reply: str = ""
    created: str = ""
    updated: str = ""
    started: str = ""
    finished: str = ""
    approval: str = ""             # what policy required: "operator" | "auto" ("" for a rejected ingest)
    approval_reason: str = ""
    approved_by: str = ""          # "operator" | "auto"
    parent: str = ""               # the request the sender was serving when it raised this one
    chain: list[str] = field(default_factory=list)   # ancestor senders, nearest first (loop guard)
    nonce: str = ""                # fences the note/reply in rendered prompts
    session_id: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    queued_at: str = ""            # when it last became runnable — the queue TTL counts from here
    task_digest: str = ""          # the task definition it was checked/approved against

    @classmethod
    def from_dict(cls, obj: dict) -> Record:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in obj.items() if k in names})

    @property
    def open(self) -> bool:
        return self.status in protocol.OPEN


def _root() -> Path:
    return config.relay_state_dir()


def _requests_dir() -> Path:
    return _root() / "requests"


def _record_path(request_id: str) -> Path:
    if not protocol.ID_RE.fullmatch(request_id):
        raise ValueError(f"invalid request id {request_id!r}")
    return _requests_dir() / f"{request_id}.json"


def events_path() -> Path:
    return _root() / "events.jsonl"


@contextlib.contextmanager
def _locked():
    _requests_dir().mkdir(parents=True, exist_ok=True)
    with open(_root() / ".store.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write(rec: Record) -> None:
    path = _record_path(rec.id)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    tmp.write_text(json.dumps(asdict(rec), indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def load(request_id: str) -> Record | None:
    try:
        return Record.from_dict(json.loads(_record_path(request_id).read_text()))
    except (OSError, ValueError, TypeError):
        return None


def resolve_id(prefix: str) -> tuple[str | None, str]:
    """A full id from an unambiguous prefix (CLI convenience) → ``(id, "")`` or ``(None, error)``."""
    if protocol.ID_RE.fullmatch(prefix) and _record_path(prefix).exists():
        return prefix, ""
    matches = [r.id for r in list_records() if r.id.startswith(prefix)]
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        return None, f"no relay request {prefix!r}"
    return None, f"{prefix!r} is ambiguous ({', '.join(matches[:5])}…)"


def list_records() -> list[Record]:
    """Every record, oldest first."""
    d = _requests_dir()
    if not d.is_dir():
        return []
    out = []
    for p in d.glob("*.json"):
        try:
            out.append(Record.from_dict(json.loads(p.read_text())))
        except (OSError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda r: (r.created, r.id))


def prune(before: float) -> list[Record]:
    """Delete FINISHED records last updated before ``before`` (epoch secs; = now − ``RETENTION_S``) —
    rejected ones already after ``REJECTED_RETENTION_S``. Open ones are never
    pruned; the event log is never touched. → the removed records (so the caller can drop their
    status files from the requesters' inboxes)."""
    gone: list[Record] = []
    with _locked():
        for rec in list_records():
            cutoff = (before + RETENTION_S - REJECTED_RETENTION_S if rec.status == protocol.REJECTED
                      else before)
            if rec.status in protocol.TERMINAL and parse_iso(rec.updated) < cutoff:
                try:
                    _record_path(rec.id).unlink()
                    gone.append(rec)
                except OSError:
                    pass
    return gone


def create(rec: Record) -> bool:
    """Persist a NEW record. False when the id is already taken (a collision — the caller rejects)."""
    with _locked():
        if _record_path(rec.id).exists():
            return False
        rec.created = rec.created or now_iso()
        rec.updated = rec.created
        _write(rec)
    return True


def transition(request_id: str, *, allowed_from: frozenset[str] | set[str] | None,
               then: Callable[[Record], None] | None = None, **changes) -> Record | None:
    """Atomically apply ``changes`` to a record IF its status is in ``allowed_from`` (None = any) —
    the compare-and-set every approve/deny/cancel/dispatch step uses so two processes can't both act.
    ``then(rec)`` runs while the lock is still held (publishing the status file), so status files
    land in commit order and never go backwards. → the updated record, or None (absent / not in an
    allowed state)."""
    with _locked():
        rec = load(request_id)
        if rec is None or (allowed_from is not None and rec.status not in allowed_from):
            return None
        for k, v in changes.items():
            setattr(rec, k, v)
        rec.updated = now_iso()
        _write(rec)
        if then is not None:
            then(rec)
    return rec


def append_event(request_id: str, event: str, *, by: str, detail: str = "", **extra) -> None:
    """Append one line to the audit log (masked detail). Best-effort — never raises."""
    line = {"ts": now_iso(), "id": request_id, "event": event, "by": by, "detail": mask(detail),
            **extra}
    try:
        _root().mkdir(parents=True, exist_ok=True)
        with open(events_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
    except OSError:
        pass


def events_for(request_id: str) -> list[dict]:
    try:
        lines = events_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("id") == request_id:
            out.append(ev)
    return out


class RelayLock:
    """The single-instance relay lock (non-blocking). ``acquire()`` → bool; held until ``release()``
    or process exit. Only the holder ingests and dispatches — approvals from other processes just
    flip records, which the holder picks up on its next tick."""

    def __init__(self) -> None:
        self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        _root().mkdir(parents=True, exist_ok=True)
        handle = open(_root() / ".relay.lock", "a+")   # NOT "w": never truncate a holder's pid
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            try:
                fcntl.flock(self._handle, fcntl.LOCK_UN)
            finally:
                self._handle.close()
                self._handle = None


def relay_holder_pid() -> int | None:
    """The pid recorded by the current relay-lock holder (None if no relay is running)."""
    path = _root() / ".relay.lock"
    try:
        handle = open(path)
    except OSError:
        return None
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            try:
                return int(path.read_text().strip() or 0) or None
            except (OSError, ValueError):
                return None
        fcntl.flock(handle, fcntl.LOCK_UN)
        return None
