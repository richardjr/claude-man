# Codex headless-run fixtures (captured 2026-09-26, codex-cli 0.157.1)

Real `codex exec --json` JSONL captured inside a hardened claude-man container (read-only,
cap-drop ALL, uid 1000) during the Phase 7c login-mode spike — the shapes the 7-run event
normaliser is tested against. No credentials: the stream carries thread ids, messages, command
items and usage only.

| file | run | what it shows |
|---|---|---|
| `exec-unauth.jsonl` | no credential | `thread.started` → `error` items → `turn.failed` with the 401 message (the fail-clean path) |
| `exec-auth.jsonl` | ChatGPT login, single-binary image | `item.completed error` = the missing `codex-code-mode-host` (the shell tool's host binary — ships in the `codex-package` tarball, NOT the single-binary one) then `agent_message` + `turn.completed` with `usage` |
| `exec-shell.jsonl` | ChatGPT login, full package tree, `--sandbox danger-full-access` | `command_execution` item.started/completed with `aggregated_output` + `exit_code`, then the message + usage |
