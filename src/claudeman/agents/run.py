"""The headless-run seam — PURE per-provider argv renderers + event normalisers (Phase 7-run).

The manager tier (V2-PLAN Phases 13–16) drives agents through this, never through a tty: one
non-interactive session per ``RunRequest``, its JSON output stream parsed line-by-line into
provider-neutral ``AgentEvent``s. Real captured streams live in ``tests/fixtures/{claude,codex}/``
and pin these parsers. No IO here — ``lifecycle.run`` does the ``docker exec``.

claude:  ``claude -p --output-format stream-json --verbose [--permission-mode acceptEdits |
         --dangerously-skip-permissions] [--resume ID] [--model REF]`` — records: ``system/init``
         (session_id, model), ``assistant`` (message.content blocks: text | tool_use), ``user``
         (tool_result blocks), ``rate_limit_event`` (ignored), ``result`` (subtype, is_error,
         result text, usage, total_cost_usd, session_id).
codex:   ``codex exec --json --skip-git-repo-check --sandbox danger-full-access [resume ID]``
         (our container IS the sandbox — its bubblewrap can't namespace under cap-drop ALL; the
         2026-09-26 spike) — records: ``thread.started`` (thread_id), ``turn.started``,
         ``item.started/completed`` (item.type: agent_message | command_execution | error | …),
         ``turn.completed`` (usage), ``turn.failed`` (error.message), top-level ``error``.
"""

from __future__ import annotations

from .base import AgentEvent, RunRequest

# ---------------------------------------------------------------------------
# claude
# ---------------------------------------------------------------------------
_CLAUDE_PERMISSION_FLAGS = {
    "default": (),
    "edits": ("--permission-mode", "acceptEdits"),
    "full": ("--dangerously-skip-permissions",),
}


def claude_argv(req: RunRequest) -> tuple[str, ...]:
    argv: list[str] = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    argv += _CLAUDE_PERMISSION_FLAGS[req.permission]
    if req.resume:
        argv += ["--resume", req.resume]
    if req.model:
        argv += ["--model", req.model]
    return tuple(argv)   # the prompt arrives on stdin


def _usage_from_claude(u: dict) -> dict[str, int]:
    return {
        "input": int(u.get("input_tokens") or 0),
        "output": int(u.get("output_tokens") or 0),
        "cache_read": int(u.get("cache_read_input_tokens") or 0),
        "cache_creation": int(u.get("cache_creation_input_tokens") or 0),
    }


def claude_parse(rec: dict) -> tuple[AgentEvent, ...]:
    t = rec.get("type")
    sid = str(rec.get("session_id") or "")
    if t == "system":
        if rec.get("subtype") == "init":
            return (AgentEvent("started", text=str(rec.get("model") or ""), session_id=sid, raw=rec),)
        return ()
    if t in ("assistant", "user"):
        out: list[AgentEvent] = []
        for blk in (rec.get("message") or {}).get("content") or ():
            if not isinstance(blk, dict):
                continue
            bt = blk.get("type")
            if bt == "text" and t == "assistant" and blk.get("text"):
                out.append(AgentEvent("message", text=str(blk["text"]), session_id=sid, raw=rec))
            elif bt == "tool_use":
                out.append(AgentEvent("tool_use", text=_summ(blk.get("input")), session_id=sid,
                                      tool=str(blk.get("name") or ""), raw=rec))
            elif bt == "tool_result":
                content = blk.get("content")
                if isinstance(content, list):   # [{type: text, text: …}, …]
                    content = "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
                out.append(AgentEvent("tool_result", text=str(content or ""), session_id=sid,
                                      ok=not bool(blk.get("is_error")), raw=rec))
        return tuple(out)
    if t == "result":
        usage = _usage_from_claude(rec.get("usage") or {})
        failed = bool(rec.get("is_error")) or rec.get("subtype") not in (None, "success")
        text = str(rec.get("result") or rec.get("error") or rec.get("subtype") or "")
        return (AgentEvent("failed" if failed else "turn_done", text=text, session_id=sid,
                           ok=not failed, usage=usage, raw=rec),)
    return ()   # rate_limit_event and anything new: ignored (never fails a run)


# ---------------------------------------------------------------------------
# codex
# ---------------------------------------------------------------------------
def codex_argv(req: RunRequest) -> tuple[str, ...]:
    argv: list[str] = ["codex", "exec", "--json", "--skip-git-repo-check",
                       "--sandbox", "danger-full-access"]
    if req.model:
        argv += ["--model", req.model]
    if req.resume:
        argv += ["resume", req.resume]
    argv.append("-")   # prompt from stdin
    return tuple(argv)


def _usage_from_codex(u: dict) -> dict[str, int]:
    return {
        "input": int(u.get("input_tokens") or 0),
        "output": int(u.get("output_tokens") or 0),
        "cache_read": int(u.get("cached_input_tokens") or 0),
        "cache_creation": int(u.get("cache_write_input_tokens") or 0),
    }


def codex_parse(rec: dict) -> tuple[AgentEvent, ...]:
    t = rec.get("type")
    if t == "thread.started":
        return (AgentEvent("started", session_id=str(rec.get("thread_id") or ""), raw=rec),)
    if t in ("item.started", "item.completed"):
        item = rec.get("item") or {}
        it = item.get("type")
        if it == "agent_message" and t == "item.completed":
            return (AgentEvent("message", text=str(item.get("text") or ""), raw=rec),)
        if it == "command_execution":
            if t == "item.started":
                return (AgentEvent("tool_use", text=str(item.get("command") or ""), tool="shell", raw=rec),)
            rc = item.get("exit_code")
            return (AgentEvent("tool_result", text=str(item.get("aggregated_output") or ""),
                               tool="shell", ok=(rc in (0, None)), raw=rec),)
        if it == "error":
            return (AgentEvent("notice", text=str(item.get("message") or ""), ok=False, raw=rec),)
        return ()
    if t == "turn.completed":
        return (AgentEvent("turn_done", usage=_usage_from_codex(rec.get("usage") or {}), raw=rec),)
    if t == "turn.failed":
        err = rec.get("error") or {}
        return (AgentEvent("failed", text=str(err.get("message") if isinstance(err, dict) else err),
                           ok=False, raw=rec),)
    if t == "error":
        return (AgentEvent("notice", text=str(rec.get("message") or ""), ok=False, raw=rec),)
    return ()


def _summ(value, limit: int = 160) -> str:
    """One-line summary of a tool input for the human event line."""
    if isinstance(value, dict):
        for key in ("command", "file_path", "path", "pattern", "query", "url"):
            if value.get(key):
                return str(value[key])[:limit]
    s = str(value or "")
    return s[:limit]
