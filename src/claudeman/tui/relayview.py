"""PURE view model for the relay screens (Requests ``r`` + the Project menu's Helpdesk… ``h``) — no
textual imports, unit-tested. The screens only lay these rows/strings out and call the lifecycle /
``relay.engine`` verbs the CLI uses (docs/RELAY.md)."""

from __future__ import annotations

import time

from ..registry.schema import Helpdesk, HelpTask
from ..relay import protocol, store

REQUEST_COLUMNS = ("ID", "From → To", "Task", "Status", "Age", "Detail")
SENDER_COLUMNS = ("Accepts requests from",)
TASK_COLUMNS = ("Task", "Params", "Permission", "Approval", "Timeout", "Summary")

_STATUS_STYLE = {
    protocol.AWAITING: "bold yellow",
    protocol.QUEUED: "cyan",
    protocol.RUNNING: "bold cyan",
    protocol.DONE: "green",
    protocol.FAILED: "red",
    protocol.REJECTED: "red",
    protocol.CANCELLED: "dim",
}


def status_style(status: str) -> str:
    return _STATUS_STYLE.get(status, "")


def age(iso: str, *, now: float | None = None) -> str:
    secs = max(0, int((time.time() if now is None else now) - store.parse_iso(iso)))
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n:
            return f"{secs // n}{unit}"
    return f"{secs}s"


def request_rows(records: list[store.Record], *, show_all: bool,
                 now: float | None = None) -> list[tuple[str, tuple[str, ...]]]:
    """``(key, cells)`` rows, newest first: awaiting-approval on top, then the other open ones, then
    (with ``show_all``) the finished ones."""
    order = {protocol.AWAITING: 0, protocol.RUNNING: 1, protocol.QUEUED: 2}
    picked = [r for r in records if show_all or r.status in protocol.OPEN]
    picked.sort(key=lambda r: (order.get(r.status, 3), -store.parse_iso(r.created), r.id))
    return [(r.id, (r.id, f"{r.sender} → {r.target}", r.task, r.status,
                    age(r.created, now=now), r.detail.splitlines()[0][:80] if r.detail else ""))
            for r in picked]


def counts(records: list[store.Record]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        out[r.status] = out.get(r.status, 0) + 1
    return out


def relay_status(*, here: bool, holder_pid: int | None) -> str:
    if here:
        return "relay: running in this TUI"
    if holder_pid:
        return f"relay: running elsewhere (pid {holder_pid})"
    return "relay: NOT running — requests wait until a relay runs (this TUI retries every few seconds)"


def detail_lines(rec: store.Record, events: list[dict]) -> list[str]:
    """The selected request's full detail (plain text; the screen escapes markup)."""
    lines = [f"{rec.id}  ·  {rec.sender} → {rec.target}  ·  task {rec.task}  ·  {rec.status}"]
    if rec.approval:
        lines.append(f"approval: {rec.approval}"
                     + (f" — {rec.approval_reason}" if rec.approval_reason else "")
                     + (f" (approved by {rec.approved_by})" if rec.approved_by else ""))
    if rec.parent:
        lines.append(f"raised while serving {rec.parent} (chain: {' ← '.join(rec.chain)})")
    if rec.params:
        lines.append("params: " + ", ".join(f"{k}={v}" for k, v in rec.params.items()))
    if rec.detail:
        lines.append(f"detail: {rec.detail}")
    if rec.note:
        lines += ["", "note (from the requesting agent):", *[f"  {x}" for x in rec.note.splitlines()]]
    if rec.reply:
        lines += ["", "reply:", *[f"  {x}" for x in rec.reply.splitlines()]]
    if rec.session_id:
        lines += ["", f"session {rec.session_id} — resume it in {rec.target}: claude --resume {rec.session_id}"]
    if events:
        lines += ["", "events:"]
        lines += [f"  {e.get('ts', '')}  {e.get('event', '')} by {e.get('by', '')}"
                  + (f" — {e['detail']}" if e.get("detail") else "") for e in events]
    return lines


def can_act(status: str) -> dict[str, bool]:
    """Which operator verbs apply to a request in ``status``."""
    return {
        "approve": status == protocol.AWAITING,
        "deny": status in (protocol.AWAITING, protocol.QUEUED),
        "cancel": status in (protocol.AWAITING, protocol.QUEUED, protocol.RUNNING),
    }


# -- helpdesk screen ---------------------------------------------------------------
def helpdesk_status(h: Helpdesk) -> str:
    if h.error:
        return f"INVALID [project.helpdesk] — {h.error} (fix it in the project's TOML)"
    state = "on" if h.enabled else "off — needs at least one sender and one task"
    return (f"helpdesk {state} · default approval {h.approve} (o) · "
            f"autostart {'on' if h.autostart else 'off'} (s)")


def task_rows(h: Helpdesk) -> list[tuple[str, tuple[str, ...]]]:
    return [(t.name, (t.name, " ".join(n for n, _ in t.params) or "—", t.permission,
                      h.approval_for(t) + ("" if t.approve else " (default)"),
                      timeout_text(t.timeout),
                      t.summary or ("free-form" if not t.prompt else "")))
            for t in h.tasks]


def params_text(task: HelpTask | None) -> str:
    """The task modal's params box: one ``name=description`` per line."""
    return "\n".join(f"{n}={d}" for n, d in task.params) if task else ""


def parse_params(text: str) -> tuple[tuple[str, str], ...]:
    """Parse the params box (``name=description`` per line; blank lines ignored). Validation of the
    names is ``HelpTask``'s job."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, desc = line.partition("=")
        out.append((name.strip(), desc.strip()))
    return tuple(out)


def timeout_text(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"
