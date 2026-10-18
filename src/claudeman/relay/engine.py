"""The relay loop + the operator verbs (docs/RELAY.md).

``Relay.tick()`` is called every couple of seconds by whichever process holds the single-instance relay
lock — the TUI (a background worker) or ``claudemanctl relay run``:

1. **ingest** — drain every project's ``.relay/out/`` (``fsio``, no-follow, a per-sender budget). The
   SENDER is the project whose bind the file was in; the request is parsed (``protocol``), checked
   against the target's helpdesk + the loop/rate guards (``policy``), recorded (``store``) as rejected,
   awaiting the operator, or queued, and a status file is written back into the sender's
   ``.relay/in/``. One hostile file can't take the others down with it: each is isolated.
2. **schedule** — expire stale requests; for each queued request whose target has no dispatch in
   flight, start a dispatch thread. Policy is RE-checked first (task removed, sender dropped, approval
   tightened, the task definition changed since it was approved).
3. **dispatch** (thread) — start the target if it's stopped and its helpdesk allows ``autostart``,
   wait while an agent is already live in it (invariant 6 — re-probed every ``RETRY_S``), re-check
   policy once more, then run ONE headless session (``lifecycle.run``) with the target's own task
   template, permission and timeout, and deliver the masked reply. Cancelling a RUNNING request stops
   the run (and the in-container agent).

``approve``/``deny``/``cancel`` are plain compare-and-set record transitions, safe from any process
(the TUI, another shell); the lock holder acts on the new state at its next tick.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable

from .. import agents, lifecycle
from ..registry import projects as projects_registry
from ..registry.schema import HelpTask, Project
from . import fsio, policy, protocol, store
from .protocol import AWAITING, CANCELLED, DONE, FAILED, QUEUED, REJECTED, RUNNING

TICK_S = 2.0          # how often a relay host calls tick()
RETRY_S = 10.0        # re-probe a busy / stopped target this often
PROGRESS_S = 5.0      # refresh a running request's status file at most this often
INGEST_PER_MIN = 20   # requests ingested per sender per minute; the excess waits in its outbox
REJECT_PER_HOUR = 30  # rejected requests RECORDED per sender per hour; beyond, status file only
DETAIL_MAX = 2000     # a failure detail carried back (after control-stripping, before masking)

LogFn = Callable[..., None]   # (message, *, attention=False)


class Backend:
    """The docker/lifecycle side of a dispatch — swapped for a fake in the dependency-free tests.
    The TUI overrides ``reserved`` so the relay never autostarts/runs into a project the operator is
    mid-way through changing (its per-project busy guard)."""

    def is_running(self, project: Project) -> bool:
        from ..docker import runner
        return runner.is_running(project.slug)

    def agent_busy(self, project: Project) -> bool:
        from ..tui import terminals   # textual-free; the invariant-6 /proc probe lives there
        return terminals.claude_already_running(project.slug, provider=project.provider)

    def reserved(self, project: Project) -> bool:
        return False

    def up(self, project: Project) -> lifecycle.Result:
        return lifecycle.up(project)

    def run(self, project: Project, request: agents.RunRequest, *, on_event, timeout: float,
            cancel: threading.Event):
        return lifecycle.run(project, request, on_event=on_event, timeout=timeout, cancel=cancel)


def publish(rec: store.Record) -> None:
    """Write ``rec``'s status file into the SENDER's ``.relay/in/`` (the reply fenced as the other
    agent's words). Called under the store lock (``store.transition(then=…)``) so status files land in
    commit order. Best-effort."""
    if not projects_registry.exists(rec.sender):
        return
    reply = (policy.render_reply_for_requester(target=rec.target, text=rec.reply, nonce=rec.nonce)
             if rec.reply.strip() else "")
    fsio.write_status(rec.sender, rec.id, protocol.render_status(
        request_id=rec.id, to=rec.target, task=rec.task, status=rec.status, detail=rec.detail,
        reply=reply, updated=rec.updated))


def _usage_tail(usage: dict) -> str:
    if not usage:
        return ""
    return f" · {usage.get('input', 0)} in / {usage.get('output', 0)} out tokens"


def _recent_auto(records: list[store.Record], sender: str, target: str, now: float) -> int:
    return sum(1 for r in records
               if r.sender == sender and r.target == target and r.approved_by == "auto"
               and now - store.parse_iso(r.created) < 86400)


class Relay:
    def __init__(self, *, on_log: LogFn | None = None, backend: Backend | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.lock = store.RelayLock()
        self.backend = backend or Backend()
        self._log_fn = on_log
        self._clock = clock
        self._active: dict[str, threading.Thread] = {}
        self._cancels: dict[str, threading.Event] = {}   # running request id -> stop signal
        self._next_try: dict[str, float] = {}
        self._ingested: dict[str, list[float]] = {}      # sender -> recent ingest times (budget)
        self._muted: dict[str, float] = {}               # sender -> until (rejection-flood log mute)
        self._mutex = threading.Lock()
        self._tick_lock = threading.Lock()   # one tick at a time, whoever calls (TUI workers overlap)
        self._recovered = False

    # -- plumbing ------------------------------------------------------------
    def _log(self, msg: str, *, attention: bool = False) -> None:
        if self._log_fn is not None:
            try:
                self._log_fn(msg, attention=attention)
            except Exception:  # noqa: BLE001 - a log sink must never break the relay
                pass

    @staticmethod
    def _set(rec_id: str, allowed, **changes) -> store.Record | None:
        return store.transition(rec_id, allowed_from=allowed, then=publish, **changes)

    def active(self) -> list[str]:
        with self._mutex:
            return sorted(t for t, th in self._active.items() if th.is_alive())

    def wait_idle(self, timeout: float = 30.0) -> None:
        """Join every in-flight dispatch (tests; ``relay run --once``)."""
        deadline = time.monotonic() + timeout
        while True:
            with self._mutex:
                threads = [t for t in self._active.values() if t.is_alive()]
            if not threads or time.monotonic() > deadline:
                return
            for t in threads:
                t.join(max(0.0, deadline - time.monotonic()))

    def close(self) -> None:
        self.lock.release()

    def _budget(self, slug: str) -> int:
        """How many more requests ``slug`` may have ingested this minute (a flooding container's
        excess stays in its outbox — it can't grow the store or slow the loop)."""
        cutoff = self._clock() - 60.0
        recent = [t for t in self._ingested.get(slug, []) if t > cutoff]
        self._ingested[slug] = recent
        return max(0, INGEST_PER_MIN - len(recent))

    # -- the loop --------------------------------------------------------------
    def tick(self) -> bool:
        """One relay pass. → True if this process is (now) the relay; False when another holds it."""
        if not self._tick_lock.acquire(blocking=False):
            return self.lock.held          # a previous tick is still running — skip this one
        try:
            if not self.lock.acquire():
                return False
            if not self._recovered:
                self._recover()
                self._recovered = True
            projects = {p.slug: p for p in projects_registry.list_projects()}
            records = store.list_records()
            for slug in projects:
                if not fsio.has_layout(slug):
                    continue
                for item in fsio.read_outbox(slug, limit=self._budget(slug)):
                    self._ingested.setdefault(slug, []).append(self._clock())
                    try:
                        self._ingest(projects[slug], item, projects, records)
                    except Exception as exc:  # noqa: BLE001 - one bad request never stops the rest
                        self._log(f"relay: could not ingest {item.request_id} from {slug}: {exc!r}")
                        fsio.write_status(slug, item.request_id, protocol.render_status(
                            request_id=item.request_id, to="?", task="?", status=REJECTED,
                            detail="the relay could not process this request", updated=store.now_iso()))
            self._signal_cancels()
            self._schedule(projects)
            return True
        finally:
            self._tick_lock.release()

    def _recover(self) -> None:
        """A RUNNING record at startup belongs to a relay that died mid-dispatch (the lock guarantees
        no live one) — fail it so the requester isn't left waiting forever. Also prunes finished
        records past retention (the append-only event log keeps the history)."""
        pruned = store.prune(self._clock() - store.RETENTION_S)
        for rec in pruned:
            fsio.remove_status(rec.sender, rec.id)
        if pruned:
            self._log(f"relay: pruned {len(pruned)} finished request(s)")
        for rec in store.list_records():
            if rec.status == RUNNING:
                done = self._set(rec.id, {RUNNING}, status=FAILED, finished=store.now_iso(),
                                 detail="the relay stopped while this was running — ask again")
                if done:
                    store.append_event(rec.id, "failed", by="relay", detail=done.detail)

    def _signal_cancels(self) -> None:
        """A RUNNING request the operator cancelled (from any process) → stop its run."""
        with self._mutex:
            pending = list(self._cancels.items())
        for rid, ev in pending:
            rec = store.load(rid)
            if rec is None or rec.status != RUNNING:
                ev.set()

    # -- ingest -----------------------------------------------------------------
    def _ingest(self, sender: Project, item: fsio.OutboxItem, projects: dict[str, Project],
                records: list[store.Record]) -> None:
        rid = item.request_id
        now = self._clock()
        req: protocol.Request | None = None
        err = item.error
        if not err:
            try:
                req = protocol.parse_request(item.data, expected_id=rid)
            except protocol.ProtocolError as exc:
                err = f"malformed request: {exc}"
        rec = store.Record(id=rid, sender=sender.slug, target=req.to if req else "?",
                           task=req.task if req else "?", status=REJECTED,
                           params=dict(req.params) if req else {},
                           note=store.clean(req.note, protocol.NOTE_MAX) if req else "",
                           nonce=secrets.token_hex(8))
        task: HelpTask | None = None
        if req is not None:
            task, err = policy.check_request(req, sender, projects.get(req.to))
        if not err:
            parent = next((r for r in records if r.status == RUNNING and r.target == sender.slug), None)
            if parent is not None:
                rec.parent = parent.id
                rec.chain = [parent.sender, *parent.chain]
            err = policy.check_chain(rec.target, rec.chain)
        if not err:
            open_n = sum(1 for r in records if r.sender == sender.slug and r.open)
            if open_n >= policy.MAX_OPEN_PER_SENDER:
                err = (f"{sender.slug} already has {open_n} open requests "
                       f"(max {policy.MAX_OPEN_PER_SENDER}) — wait for some to finish")
        if err:
            rec.detail = err
            recent_rejects = sum(1 for r in records if r.sender == sender.slug and r.status == REJECTED
                                 and now - store.parse_iso(r.created) < 3600)
            if recent_rejects >= REJECT_PER_HOUR:
                # A flood of bad requests: answer in the sender's own inbox, record nothing.
                fsio.write_status(sender.slug, rid, protocol.render_status(
                    request_id=rid, to=rec.target, task=rec.task, status=REJECTED, detail=err,
                    updated=store.now_iso()))
                if now >= self._muted.get(sender.slug, 0.0):
                    self._muted[sender.slug] = now + 3600
                    self._log(f"relay: {sender.slug} is sending many bad requests — rejecting them "
                              f"without recording for the next hour", attention=True)
                return
        else:
            rec.task_digest = policy.task_digest(task)
            needs, reason = policy.needs_operator(
                sender, projects[rec.target], task,
                recent_auto=_recent_auto(records, sender.slug, rec.target, now))
            rec.approval = "operator" if needs else "auto"
            rec.approval_reason = reason
            if needs:
                rec.status, rec.detail = AWAITING, f"waiting for the operator's approval ({reason})"
            else:
                rec.status, rec.approved_by, rec.detail = QUEUED, "auto", "queued"
                rec.queued_at = store.now_iso()
        if not store.create(rec):
            # An id already in use: answer in the sender's own inbox without touching the other record.
            fsio.write_status(sender.slug, rid, protocol.render_status(
                request_id=rid, to=rec.target, task=rec.task, status=REJECTED,
                detail="request id already used — send it again", updated=store.now_iso()))
            return
        rec = store.load(rid) or rec
        records.append(rec)
        store.append_event(rid, "received", by=sender.slug, detail=f"{rec.target}/{rec.task}",
                           target=rec.target, task=rec.task, params=rec.params)
        store.append_event(rid, rec.status, by="relay", detail=rec.detail)
        publish(rec)
        what = f"{sender.slug} → {rec.target} {rec.task} ({rid})"
        if rec.status == REJECTED:
            self._log(f"relay: rejected {what}: {err}")
        elif rec.status == AWAITING:
            self._log(f"relay: {what} needs approval — {rec.approval_reason}", attention=True)
        else:
            self._log(f"relay: queued {what}")

    # -- policy re-check (schedule + just before running) -----------------------------------
    def _recheck(self, rec: store.Record, projects: dict[str, Project],
                 records: list[store.Record]) -> tuple[HelpTask | None, Project | None]:
        """Re-validate a QUEUED request against the CURRENT registry. Rejects it (no longer allowed) or
        sends it back to the operator (approval now required / the task changed since it was checked)
        and returns ``(None, None)``; else ``(task, target)``."""
        sender, target = projects.get(rec.sender), projects.get(rec.target)
        req = protocol.Request(id=rec.id, to=rec.target, task=rec.task, params=rec.params,
                               note=rec.note)
        task, err = (policy.check_request(req, sender, target) if sender is not None
                     else (None, f"project {rec.sender!r} no longer exists"))
        if err:
            if self._set(rec.id, {QUEUED}, status=REJECTED, finished=store.now_iso(),
                         detail=f"no longer allowed: {err}"):
                store.append_event(rec.id, "rejected", by="relay", detail=err)
            return None, None
        needs, reason = policy.needs_operator(sender, target, task)
        digest = policy.task_digest(task)
        changed = bool(rec.task_digest) and rec.task_digest != digest
        if changed or (needs and rec.approved_by != "operator"):
            why = (f"{target.slug}'s task {task.name!r} changed since it was "
                   f"{'approved' if rec.approved_by == 'operator' else 'queued'}") if changed else reason
            if self._set(rec.id, {QUEUED}, status=AWAITING, approval="operator", approval_reason=why,
                         approved_by="", task_digest=digest,
                         detail=f"waiting for the operator's approval ({why})"):
                store.append_event(rec.id, AWAITING, by="relay", detail=why)
                self._log(f"relay: {rec.sender} → {rec.target} {rec.task} ({rec.id}) now needs "
                          f"approval — {why}", attention=True)
            return None, None
        return task, target

    # -- schedule ------------------------------------------------------------------
    def _schedule(self, projects: dict[str, Project]) -> None:
        now = self._clock()
        records = store.list_records()
        for rec in records:
            if rec.status == AWAITING and now - store.parse_iso(rec.updated) > policy.AWAITING_TTL_S:
                if self._set(rec.id, {AWAITING}, status=REJECTED, finished=store.now_iso(),
                             detail=f"not approved within {policy.AWAITING_TTL_S // 3600}h"):
                    store.append_event(rec.id, "expired", by="relay")
                continue
            if rec.status != QUEUED:
                continue
            if now - store.parse_iso(rec.queued_at or rec.created) > policy.QUEUE_TTL_S:
                if self._set(rec.id, {QUEUED}, status=FAILED, finished=store.now_iso(),
                             detail=f"expired — not started within {policy.QUEUE_TTL_S // 3600}h "
                                    f"of being queued"):
                    store.append_event(rec.id, "expired", by="relay")
                continue
            # Policy first, every tick (cheap, no docker): a config change applies to waiting requests
            # at once, not only when the target frees up.
            task, target = self._recheck(rec, projects, records)
            if task is None:
                continue
            with self._mutex:
                busy = rec.target in self._active and self._active[rec.target].is_alive()
                if busy or now < self._next_try.get(rec.target, 0.0):
                    continue
                th = threading.Thread(target=self._dispatch, args=(rec.id, target),
                                      name=f"relay-{rec.target}", daemon=True)
                self._active[rec.target] = th
            th.start()

    # -- dispatch (thread) ---------------------------------------------------------------
    def _defer(self, rec: store.Record, detail: str) -> None:
        with self._mutex:
            self._next_try[rec.target] = self._clock() + RETRY_S
        if rec.detail != detail:
            self._set(rec.id, {QUEUED}, detail=detail)

    def _dispatch(self, rec_id: str, target: Project) -> None:
        try:
            self._dispatch_inner(rec_id, target)
        except Exception as exc:  # noqa: BLE001 - a dispatch thread must never die silently
            if self._set(rec_id, {QUEUED, RUNNING}, status=FAILED, finished=store.now_iso(),
                         detail=f"relay error: {exc!r}"[:DETAIL_MAX]):
                store.append_event(rec_id, "failed", by="relay", detail=repr(exc))
        finally:
            with self._mutex:
                self._active.pop(target.slug, None)
                self._cancels.pop(rec_id, None)

    def _dispatch_inner(self, rec_id: str, target: Project) -> None:
        rec = store.load(rec_id)
        if rec is None or rec.status != QUEUED:
            return
        if self.backend.reserved(target):
            self._defer(rec, f"waiting — the operator is changing {target.slug}")
            return
        if not self.backend.is_running(target):
            if not target.helpdesk.autostart:
                self._defer(rec, f"waiting for {target.slug} to be started (its helpdesk has no autostart)")
                return
            self._set(rec_id, {QUEUED}, detail=f"starting {target.slug} …")
            self._log(f"relay: starting {target.slug} to serve {rec_id}")
            res = self.backend.up(target)
            if not res.ok:
                if self._set(rec_id, {QUEUED}, status=FAILED, finished=store.now_iso(),
                             detail=f"could not start {target.slug}: {res.detail}"[:DETAIL_MAX]):
                    store.append_event(rec_id, "failed", by="relay", detail=res.detail)
                return
        if self.backend.agent_busy(target):
            self._defer(rec, f"waiting — an agent is already running in {target.slug} (one per container)")
            return
        # Re-check against the registry as it is NOW (an autostart can take minutes; the operator may
        # have disallowed the sender, removed or changed the task in the meantime).
        rec = store.load(rec_id)
        if rec is None or rec.status != QUEUED:
            return
        projects = {p.slug: p for p in projects_registry.list_projects()}
        task, target = self._recheck(rec, projects, store.list_records())
        if task is None:
            return
        cancel = threading.Event()
        with self._mutex:
            self._cancels[rec_id] = cancel
        rec = self._set(rec_id, {QUEUED}, status=RUNNING, started=store.now_iso(),
                        detail=f"running in {target.slug}")
        if rec is None:
            return   # cancelled / denied between schedule and now
        store.append_event(rec_id, "started", by="relay", detail=f"permission {task.permission}")
        self._log(f"relay: running {rec.sender} → {target.slug} {task.name} ({rec_id})")
        prompt = policy.render_prompt(request_id=rec.id, sender=rec.sender, target=target.slug,
                                      task=task, params=rec.params, note=rec.note, nonce=rec.nonce)
        request = agents.RunRequest(
            prompt=prompt, permission=task.permission,
            model=target.claude_model if target.agent == agents.DEFAULT_ID else "")
        state = {"n": 0, "tool": "", "at": 0.0}

        def on_event(ev: agents.AgentEvent) -> None:
            state["n"] += 1
            if ev.kind == "tool_use" and ev.tool:
                state["tool"] = protocol.strip_controls(ev.tool)[:60]
            now = time.monotonic()
            if now - state["at"] >= PROGRESS_S:
                state["at"] = now
                last = f" · last tool {state['tool']}" if state["tool"] else ""
                self._set(rec_id, {RUNNING}, detail=f"running in {target.slug} · {state['n']} events{last}")

        out = self.backend.run(target, request, on_event=on_event, timeout=float(task.timeout),
                               cancel=cancel)
        reply = store.clean(out.text or "", protocol.REPLY_MAX)
        status = DONE if out.ok else FAILED
        detail = (("done" + _usage_tail(out.usage)) if out.ok
                  else store.clean(out.detail or "run failed", DETAIL_MAX))
        final = self._set(rec_id, {RUNNING}, status=status, reply=reply, finished=store.now_iso(),
                          detail=detail, session_id=out.session_id or "", usage=dict(out.usage or {}))
        if final:
            store.append_event(rec_id, status, by=target.slug, detail=detail,
                               session_id=out.session_id or "")
            self._log(f"relay: {status} {rec.sender} → {target.slug} {task.name} ({rec_id})"
                      + ("" if out.ok else f": {detail}"))
        else:
            self._log(f"relay: stopped {rec.sender} → {target.slug} {task.name} ({rec_id})")


# ---------------------------------------------------------------------------
# Operator verbs — compare-and-set transitions, safe from any process.
# ---------------------------------------------------------------------------
def _operator_set(request_id: str, allowed: set[str], verb: str, event: str,
                  **changes) -> lifecycle.Result:
    rid, err = store.resolve_id(request_id)
    if rid is None:
        return lifecycle.Result(False, err)
    before = store.load(rid)
    rec = store.transition(rid, allowed_from=allowed, then=publish, **changes)
    if rec is None:
        state = before.status if before else "missing"
        return lifecycle.Result(False, f"{rid} is {state} — can't {verb} it")
    store.append_event(rid, event, by="operator", detail=changes.get("detail", ""))
    return lifecycle.Result(True, f"{event}: {rid} ({rec.sender} → {rec.target} {rec.task})")


def approve(request_id: str) -> lifecycle.Result:
    """Operator approval: awaiting → queued (the relay runs it at its next tick)."""
    return _operator_set(request_id, {AWAITING}, "approve", "approved", status=QUEUED, approved_by="operator",
                         queued_at=store.now_iso(), detail="approved by the operator — queued")


def deny(request_id: str, reason: str = "") -> lifecycle.Result:
    """Operator denial of a request that hasn't started (awaiting or queued) → rejected."""
    reason = protocol.strip_controls(reason).strip()
    detail = "denied by the operator" + (f": {reason[:300]}" if reason else "")
    return _operator_set(request_id, {AWAITING, QUEUED}, "deny", "denied", status=REJECTED,
                         finished=store.now_iso(), detail=detail)


def cancel(request_id: str) -> lifecycle.Result:
    """Withdraw a request → cancelled. A RUNNING one is stopped by the relay at its next tick (the run
    and the in-container agent)."""
    return _operator_set(request_id, {AWAITING, QUEUED, RUNNING}, "cancel", "cancelled", status=CANCELLED,
                         finished=store.now_iso(), detail="cancelled by the operator")


def abandon_project(slug: str) -> int:
    """A project was deleted: reject its not-yet-running requests (as sender or target). A RUNNING one
    fails on its own when the container goes. → how many were closed."""
    n = 0
    for rec in store.list_records():
        if rec.status in (AWAITING, QUEUED) and slug in (rec.sender, rec.target):
            if store.transition(rec.id, allowed_from={AWAITING, QUEUED}, then=publish,
                                status=REJECTED, finished=store.now_iso(),
                                detail=f"project {slug} was deleted"):
                store.append_event(rec.id, "rejected", by="relay", detail=f"{slug} deleted")
                n += 1
    return n
