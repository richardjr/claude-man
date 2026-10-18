"""The relay's wire format — the JSON files that cross the container boundary (docs/RELAY.md). PURE.

Two files, both in the requester's ``/workspace/.relay`` mailbox:

* **request** — ``out/<id>.json``, written by the in-container helper (or anything else running in the
  container: it is UNTRUSTED input). ``parse_request`` is the ONE place it is decoded; it never trusts
  a sender field — there is none. The sender is whoever's bind the file came from.
* **status** — ``in/<id>.json``, written by the host relay on every state change; the helper's
  ``wait`` polls it.

Param VALUES are identifier-shaped (branches, names, URLs — no whitespace), because the target's
prompt template interpolates them inline; free prose belongs in the ``note``, which is fenced as
another agent's words when the prompt is rendered (``policy.render_prompt``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

VERSION = 1
# A request id: chosen by the requester's helper (``<yymmddHHMMSS>-<hex>``), unique per host — the
# store refuses a collision. Also the mailbox file stem, so it must be filename-safe.
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{7,63}")
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_PARAM_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
# Identifier-shaped values only (all patterns here are applied with ``fullmatch`` — ``$`` alone
# would accept a trailing newline): letters, digits and ``. _ / @ : + = , % -``; no whitespace,
# quotes, backticks, brackets, braces or shell metacharacters (``& ; | ! # ? ~ ^ $``), and no leading
# ``-`` (an option-injection shape like ``--upload-pack=…`` in a ``git checkout {branch}`` template).
# So a value can't carry a sentence of instructions into the template or break out of a command.
PARAM_VALUE_RE = re.compile(r"(?!-)[A-Za-z0-9._/@:+=,%-]{1,200}")
# C0 controls (except \t \n), DEL and C1 — stripped from agent text so a note or reply can't carry
# terminal escape sequences into the operator's `relay show` / review screens.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
NOTE_MAX = 8000
REQUEST_MAX_BYTES = 32 * 1024          # a request file larger than this is refused unread
REPLY_MAX = 32 * 1024                  # the reply text carried back into the requester's inbox

# Request lifecycle. awaiting_approval → queued → running → done | failed; rejected (policy or the
# operator), cancelled (the operator, before it runs).
AWAITING = "awaiting_approval"
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
REJECTED = "rejected"
CANCELLED = "cancelled"
STATUSES = (AWAITING, QUEUED, RUNNING, DONE, FAILED, REJECTED, CANCELLED)
TERMINAL = frozenset({DONE, FAILED, REJECTED, CANCELLED})
OPEN = frozenset({AWAITING, QUEUED, RUNNING})


class ProtocolError(ValueError):
    """A request file that isn't a well-formed v1 request."""


def _short(value: object) -> str:
    """A bounded repr of a hostile value for an error message."""
    r = repr(value)
    return r if len(r) <= 60 else r[:57] + "…"


@dataclass(frozen=True)
class Request:
    id: str
    to: str
    task: str
    params: dict[str, str] = field(default_factory=dict)
    note: str = ""


def request_filename(request_id: str) -> str:
    return f"{request_id}.json"


def id_from_filename(name: str) -> str | None:
    """The request id for an outbox entry name, or None when the name isn't a request file (the
    helper's in-flight ``.<id>.tmp`` files, junk)."""
    if not name.endswith(".json"):
        return None
    stem = name[:-5]
    return stem if ID_RE.fullmatch(stem) else None


def parse_request(data: bytes, *, expected_id: str) -> Request:
    """Decode + validate one request file's bytes. ``expected_id`` is the filename stem — the body
    must agree, so a file can't masquerade as another request. Raises ``ProtocolError``."""
    if len(data) > REQUEST_MAX_BYTES:
        raise ProtocolError(f"request is larger than {REQUEST_MAX_BYTES} bytes")
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - hostile input: ANY decode failure (incl. RecursionError
        #                        from a deeply nested document) is a malformed request, never a crash
        raise ProtocolError(f"not valid UTF-8 JSON ({type(exc).__name__})") from None
    if not isinstance(obj, dict):
        raise ProtocolError("request must be a JSON object")
    if obj.get("v") != VERSION:
        raise ProtocolError(f"unsupported request version {_short(obj.get('v'))} (expected {VERSION})")
    rid, to, task = obj.get("id"), obj.get("to"), obj.get("task")
    if rid != expected_id:
        raise ProtocolError(f"request id {_short(rid)} does not match its file name ({expected_id})")
    if not isinstance(to, str) or not _SLUG_RE.fullmatch(to):
        raise ProtocolError(f"invalid target project {_short(to)}")
    if not isinstance(task, str) or not _SLUG_RE.fullmatch(task):
        raise ProtocolError(f"invalid task name {_short(task)}")
    params = obj.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ProtocolError("params must be an object of name → value strings")
    clean: dict[str, str] = {}
    for k, v in params.items():
        if not isinstance(k, str) or not _PARAM_NAME_RE.fullmatch(k):
            raise ProtocolError(f"invalid param name {_short(k)}")
        if not isinstance(v, str) or not PARAM_VALUE_RE.fullmatch(v):
            raise ProtocolError(
                f"param {k}={_short(v)} must be identifier-shaped (letters, digits, . _ / @ : + = , % -; "
                f"no leading -; ≤200 chars) — put prose in the note")
        clean[k] = v
    note = obj.get("note", "")
    if note is None:
        note = ""
    if not isinstance(note, str):
        raise ProtocolError("note must be a string")
    if len(note) > NOTE_MAX:
        raise ProtocolError(f"note is longer than {NOTE_MAX} characters")
    return Request(id=rid, to=to, task=task, params=clean, note=strip_controls(note))


def strip_controls(text: str) -> str:
    """Drop terminal-control characters (keeps newlines and tabs)."""
    return _CONTROL_RE.sub("", text)


def render_status(*, request_id: str, to: str, task: str, status: str, detail: str = "",
                  reply: str = "", updated: str = "") -> bytes:
    """The ``in/<id>.json`` status file the helper polls (canonical, newline-terminated)."""
    return (json.dumps({"v": VERSION, "id": request_id, "to": to, "task": task, "status": status,
                        "detail": detail, "reply": reply, "updated": updated},
                       indent=2, sort_keys=True) + "\n").encode("utf-8")
