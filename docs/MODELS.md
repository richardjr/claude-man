# Local models (Phase 9)

claude-man can run a project's in-container `claude` against a **local model** instead of (or alongside)
the claude.ai subscription. This doc covers the **dynamic model-management framework** that's landed
first — installing/updating/listing local models from inside claude-man. The hybrid gateway that routes
the agent's calls to a local model (the `/model`-picker + mid-session switching) is the follow-on; see
[`ROADMAP.md`](../ROADMAP.md) Phase 9 and issue #14.

## Prerequisite: Ollama on the host

claude-man manages **models**, not the model server. Install and run **[Ollama](https://ollama.com)** on
the host (it's a long-lived daemon on `127.0.0.1:11434`, no auth):

- **For host-side `claudemanctl model …`** the default `127.0.0.1:11434` is all you need.
- **For containers to reach it** (the Phase-9 gateway), the daemon must bind a reachable interface —
  set `OLLAMA_HOST=0.0.0.0:11434` in the systemd unit (`systemctl edit ollama`) / `launchctl setenv` on
  macOS. On Linux a hardened host firewall may also need to allow the Docker bridge subnet.

Override the URL claude-man talks to with `CLAUDE_MAN_OLLAMA_URL` (secret-free — Ollama has no token).
Every `model` command **fails open**: with no daemon it prints an actionable hint and exits non-zero,
never hangs.

## `claudemanctl model …`

```
model list [--check]      # installed models; --check annotates update-available (token-less registry probe)
model add <key|tag>       # install a preset (see `model presets`) or any raw ollama tag, with progress
model update [<ref>|--all]# re-pull to the latest build (incremental; an up-to-date model is a no-op)
model rm <ref>            # uninstall (idempotent)
model show <ref>          # context length, capabilities (incl. `tools`), quant, family
model presets             # the curated recommended coding-model table
```

Updates are detected by a **manifest-digest compare** against the Ollama registry (no multi-GB pull) and
applied by re-pulling the tag (Ollama skips blobs already present). There is no `ollama upgrade` —
re-pulling **is** the update.

## Curated coding-model presets

A starter list (`src/claudeman/models/presets.py`) — `model add <key>` resolves to a concrete tag; raw
tags work too. The reference/default is **Qwen3-Coder-30B-A3B** (`qwen3-coder:30b`), the only genuinely
capable agentic coder that fits a single **24 GB** GPU.

| Key | Tag | VRAM | Notes |
|---|---|---|---|
| `qwen3-coder` ★ | `qwen3-coder:30b` | 24 GB | reference / default |
| `devstral` | `devstral:24b` | 24 GB | tool-use-focused second pick |
| `gpt-oss-20b` | `gpt-oss:20b` | 16 GB | most reliable low-VRAM tool-caller |
| `qwen2.5-coder-7b` | `qwen2.5-coder:7b` | 8 GB | smallest useful coder |
| `qwen2.5-coder-32b` | `qwen2.5-coder:32b` | 24 GB | proven dense 32B |

### Two host-daemon config traps

Agentic **tool-use fidelity** is the make-or-break for Claude Code, and it's genuinely shaky on local
models. Two operator-side host-daemon settings matter:

1. **Context length** — Ollama's default `num_ctx` is VRAM-gated low and silently truncates agentic
   context. Set a large `OLLAMA_CONTEXT_LENGTH` (e.g. 64K–131K) on the host daemon.
2. **Tool-call template** — the stock Qwen3-Coder template drops the opening `<tool_call>` tag. Pin a
   known-good template/build for tool-heavy agent work.

`model show <ref>` reports whether a model advertises the `tools` capability.
