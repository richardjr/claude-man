# Claude Code headless-run fixtures (captured 2026-09-26, claude 2.1.282)

Real `claude -p --output-format stream-json --verbose` JSONL, the shapes `agents/run.py::claude_parse`
is tested against. Paths sanitised to `/workspace`; no credentials (the stream carries session ids,
messages, tool blocks, usage and cost only).

| file | run | what it shows |
|---|---|---|
| `stream-ok.jsonl` | "reply with the single word ok" | `system/init` → `assistant` (text) → `rate_limit_event` (ignored) → `result/success` with `usage` + `total_cost_usd` |
| `stream-tool.jsonl` | a Bash tool call (`--allowedTools=Bash`) | `assistant` text + `tool_use` block → `user` `tool_result` block → `assistant` text → `result` (2 turns) |
