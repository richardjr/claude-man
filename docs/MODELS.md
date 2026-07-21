# Local models (Phase 9)

claude-man can run a project's in-container `claude` against a **local model** instead of (or alongside)
the claude.ai subscription. This doc covers both halves: the **dynamic model-management framework** —
installing/updating/listing local models from inside claude-man — and the **hybrid gateway** that routes
the agent's calls to a local model (the `/model`-picker + mid-session switching), now landed and verified
live (`network/gateway.py`; see "Hybrid mode" and "Status" below, plus [`ROADMAP.md`](../ROADMAP.md)
Phase 9 and issue #14).

A project has **one model choice**, picked in one place (the TUI **Model…** screen / `project model
set`), of two kinds — setting either displaces the other:

- a **claude model** (`--claude claude-fable-5`, `--claude opus`, …) — the in-container `claude` is
  simply *launched* with `--model <ref>`. Registry-only, applies at the next `project claude` /
  `c`, **no recreate**, no gateway, works on locked projects. See "Claude-model pin" below.
- a **local (Ollama) model** (`qwen3-coder:30b`, …) — **hybrid mode**: the LiteLLM gateway sidecar
  fronts claude.ai + the local model on one endpoint. Recreate-to-apply. The rest of this doc.

## Claude-model pin — launching claude with `--model`

By default a project adds no `--model` and claude picks its own default. Pinning a claude model:

```
project model set <slug> --claude claude-fable-5   # or an alias: opus / sonnet / haiku
project model show <slug>
project model clear <slug>                         # back to claude's default
```

TUI: Project… menu → **Model…** (`m`) — the curated claude rows sit above the local-model list; a raw
input takes any id/alias (including bracket variants like `claude-sonnet-5[1m]`), disambiguated from
an ollama tag by shape (`models/claude_models.py::is_claude_ref` — a colon always means ollama).

The pin is **launch-time argv only** (`terminals.spawn_claude` appends `--model <ref>`): the container
is untouched (hardened floor byte-identical, invariant 2), nothing changes for a running claude, and
the next launched one picks it up. Entitlement stays claude's concern — pinning a model the account
lacks errors inside claude, not in claude-man. This is also the practical bypass for the model-picker
"usage credits" gate that Claude Code shows under setup-token auth (the token "can only make model
requests", so the picker can't see the seat's plan; actual requests work — verified live with the
premium-seat Fable 5 tier).

## Prerequisite: Ollama on the host

claude-man manages **models**, not the model server. Install and run **[Ollama](https://ollama.com)** on
the host (a long-lived daemon on `:11434`, no auth). For **hybrid mode** (a local model inside Claude
Code) three things must be true, in this order — get any wrong and selecting the local model in `/model`
just **hangs**. (claude-man now pre-flights this on `up` and prints a one-line warning naming the gap;
the claude.ai leg keeps working regardless.)

### 1. Install a GPU build (don't settle for the CPU package)

A capable coding model (the 30B reference) needs a **GPU**. On a 24 GB card (e.g. RTX 3090/4090) the
default `qwen3-coder:30b` fits; CPU-only inference of a 30B model is unusably slow and looks like a hang.

- **Arch / Omarchy:** the plain `ollama` package is **CPU-only**. Install the CUDA (NVIDIA) or ROCm (AMD)
  build instead:
  ```bash
  sudo pacman -S ollama-cuda      # NVIDIA   (or: ollama-rocm for AMD)
  ```
- **Other distros / the official installer** bundle GPU support when the driver + CUDA/ROCm runtime are
  present.
- **Verify the GPU is actually seen** (the common silent failure — a present GPU but a CPU-only daemon):
  ```bash
  journalctl -u ollama -n 40 | grep -iE "vram|cuda|gpu|compute"
  ```
  A working setup reports a non-zero VRAM / a CUDA (or ROCm) runner. `total_vram="0 B"` + `id=cpu` means
  Ollama is on the CPU (wrong package, or a missing driver/runtime) — fix that before pinning a big model.

### 2. Bind it so containers can reach it

The hybrid gateway sidecar reaches the host at `host.docker.internal:11434`. Ollama's **default
`127.0.0.1` bind is not reachable from a container** — the gateway connect times out (the classic
"local model hangs" symptom). Bind all interfaces:

```bash
# Linux (systemd): a drop-in is cleaner than editing the unit
sudo install -d /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0:11434"\nEnvironment="OLLAMA_CONTEXT_LENGTH=65536"\n' \
  | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama && sudo systemctl enable ollama

ss -ltnp | grep 11434     # verify: expect 0.0.0.0:11434, not 127.0.0.1:11434
```

On macOS use `launchctl setenv OLLAMA_HOST 0.0.0.0:11434`. (`OLLAMA_CONTEXT_LENGTH` addresses the
context-truncation trap below — see "Two host-daemon config traps".)

**Host firewall (the easy-to-miss one).** Even bound to `0.0.0.0`, a host firewall like **ufw** blocks
Docker→host traffic by default, so the gateway's connect to `:11434` **times out** (the daemon answers
fine from the host itself — the give-away that it's the firewall). Allow Docker's private ranges to reach
the Ollama port:

```bash
sudo ufw allow from 172.16.0.0/12 to any port 11434 proto tcp   # scoped: only :11434, only Docker subnets
sudo ufw reload
```

### 3. Pull the model you pin

```bash
claudemanctl model add qwen3-coder:30b   # ~19 GB; or `ollama pull qwen3-coder:30b`
```

A model that isn't pulled would otherwise trigger a multi-GB download on the first request (another
apparent hang) — the pre-flight warns when the pinned model is absent.

---

For **host-side `claudemanctl model …`** alone (no containers), the default `127.0.0.1:11434` is enough.
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

A third trap is **handled for you**: at higher reasoning effort Claude Code sends an Anthropic `thinking`
block, which Ollama rejects on non-thinking coder models (`does not support thinking`). The gateway
**force-drops** `thinking`/`reasoning_effort` on the local route (the Claude route keeps it), so a hybrid
project works at any effort with no operator action.

## Hybrid mode — using a local model in Claude Code

Pinning a model to a project turns on **hybrid mode**: a per-project **LiteLLM gateway sidecar** that
fronts *both* the claude.ai subscription and the local model on one endpoint, so **both appear in Claude
Code's `/model` picker and switch mid-session**.

```
project model set <slug> qwen3-coder:30b   # pin a local model → hybrid mode (recreate to apply)
project model show <slug>                  # show the backend
project model clear <slug>                 # back to subscription-direct
```

After `project model set …`, **recreate** the project (`project recreate <slug>`). On `up`, claude-man
brings up the gateway sidecar (fail-closed) and points the agent's `ANTHROPIC_BASE_URL` at it.

**TUI.** The same pin is available from the projects table: Project… menu (`p`) → **Model…** (`m`).
The picker lists the curated claude models (see "Claude-model pin" above), the host-Ollama installed
models, a *default* row to unpin, and a raw input (to pin a tag that isn't pulled yet, or when the
daemon is briefly unreachable). Choosing a local model persists the pin and recreates the project
off-thread (no separate recreate step); choosing a claude model is registry-only (it recreates only
when it displaces a local pin, to tear the gateway down). A **Model** column in the projects table
shows each project's current pin (`-` = default), so a pin is never silent — billing is on the
subscription for the Claude tiers and on-host (free) for the local model.

You **cannot pin a *local* model on a *locked* (strict-egress) project** — locked + hybrid is deferred
(ROADMAP 9c) and refused at `up`, so both the TUI and `project model set` reject it up front (the
registry mutator enforces it too); `project unlock <slug>` first. Unpinning a locked project is always
allowed — and a **claude**-model pin is fine when locked (launch-time argv, no gateway involved).

How the two legs are meant to work:
- **Local leg.** The local model appears in `/model` as `Local: <model>` (added via
  `ANTHROPIC_CUSTOM_MODEL_OPTION`); selecting it sends `claude-local-<model>`, which the gateway routes to
  your host Ollama (`ollama_chat/<model>`).
- **Claude leg — passthrough.** The built-in Claude tiers route through the gateway to Anthropic on your
  **subscription** (the agent's `Authorization`/`anthropic-beta` forwarded; the agent auths to the gateway
  via `x-litellm-api-key`, a per-project master key, state-tier `0600`, never in argv).

Invariants hold: the agent keeps the hard `ANTHROPIC_*` scrub and still gets the OAuth token; the
hardened floor is byte-identical (the hybrid env is additive); the only posture change is that the OAuth
token now transits claude-man's *own* sidecar (never a third party).

## Status (Phase 9 — issue #14)

**Working:** the model-management framework + CLI + TUI; the per-project pin; the gateway sidecar comes up
fail-closed; the local model is **selectable in `/model` and routes to Ollama correctly** (verified live —
the exact `claude-local-*` route beats the wildcard). The 30B reference model cold-loads slowly (~19 GB);
pin a smaller preset (`gpt-oss:20b`, `qwen2.5-coder:7b`) for snappy local inference.

**The Claude passthrough leg now works (the 9a blocker is fixed).** The earlier `404 not_found_error:
model: opus-4-8` was a **model-mapping bug in our own gateway config, not an auth bug** (a 404 not a 401
means the OAuth already reached Anthropic). Claude Code 2.1.193 sends the **full** id `claude-opus-4-8`
(not the short `opus-4-8` — verified first-hand in the binary), and the old `model: anthropic/*` wildcard
captured the suffix after `claude-` and forwarded the invalid bare `opus-4-8`. The fix is a one-line
change to a **prefix-preserving** wildcard, `model: anthropic/claude-*`, which forwards the full valid id
and covers Opus/Sonnet/Haiku(background)/Fable + any future Claude id in a single row (LiteLLM substitutes
the captured `*` into the target `*`). The `?beta=true` query string is **irrelevant** to LiteLLM routing
(FastAPI ignores it). See [`issue-14-hybrid-passthrough-protocol.md`](issue-14-hybrid-passthrough-protocol.md)
for the wire-level write-up.

**Subscription preserved — verified live.** A real Claude request through the gateway returns `200` and is
served on the **subscription** (`anthropic-ratelimit-unified-status: allowed`, counted against the 5h/7d
unified windows; this account's overage is `org_level_disabled`, so a 200 completion can only be the
subscription). The OAuth `Authorization` rides LiteLLM's dedicated anthropic path (not
`forward_client_headers_to_llm_api`), and the agent auths to the proxy via `x-litellm-api-key`. The proxy's
header munging (it replaces `User-Agent` with `litellm/<ver>`) did **not** demote the request.

**Observability caveat:** LiteLLM **strips Anthropic's response headers**, so an operator can't read their
own `anthropic-ratelimit-unified-*` lane through the gateway. That's harmless on overage-disabled accounts
(200 ⇒ subscription) but on accounts where pay-as-you-go overage IS enabled a silent demotion would bill as
overage with no visible signal — an argument for a thin custom passthrough front that forwards response
headers verbatim (issue #14 "Option C"). The shipped gateway image **is pinned by digest**
(`config.GATEWAY_IMAGE_REF`, litellm 1.89.4, verified live) — the official Docker image, never the PyPI
package (`litellm` 1.82.7/1.82.8 on PyPI were briefly malware).

**Other limits:** hybrid requires **open** egress — locked + hybrid (the air-gapped two-sidecar wiring) is
deferred (ROADMAP 9c) and refused with a clear message. `/model` selections save "for new sessions", so
the *current* session keeps its model until you restart `claude`.
