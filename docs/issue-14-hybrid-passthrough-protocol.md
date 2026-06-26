> [!NOTE]
> **Status: research spike output for issue #14 (Phase 9 hybrid gateway, the "9a" Claude-leg blocker).**
> Produced by a multi-agent protocol-research pass (4 dimension finders → adversarial corroborate/refute
> verification → synthesis), grounded in first-hand sources: the `claude` 2.1.193 native binary, LiteLLM
> source, and official Anthropic/Claude-Code docs. Two crux claims independently re-verified by hand:
> (1) CC's wire model id is the full `claude-opus-4-8` (binary registry `iMr.firstParty`, no bare
> `opus-4-8` assignment exists); (2) the bad `opus-4-8` is manufactured by claude-man's own
> `network/gateway.py` wildcard `claude-* → anthropic/*` (suffix-capture). Confidence levels and
> DISPUTED/UNCERTAIN items are called out inline + in the appendix.

> [!IMPORTANT]
> **RESOLVED by live capture (2026-06-26).** The minimal fix is applied: `network/gateway.py` now renders
> `model: anthropic/claude-*` (prefix-preserving wildcard; tests + ruff green). Live result on the
> `modeltest` hybrid project / `home` profile:
> - A real `claude-haiku-4-5-20251001` request **through the LiteLLM gateway** returns **200** with a real
>   completion (was 404) — the model-mapping fix works.
> - The request is served on the **subscription**: a direct read of the upstream response shows
>   `anthropic-ratelimit-unified-status: allowed`, `5h-utilization 0.62`, `representative-claim five_hour`,
>   and `overage-disabled-reason: org_level_disabled`. With overage org-disabled, a 200 completion can only
>   be the subscription lane (no API key is set, so there is no other lane). The "subscription survives a
>   proxy" question (§"Does the subscription survive a proxy?") is therefore **answered YES for this account
>   and stack** — including the transport delta (the real gateway uses httpx) and the header delta (a
>   control variant with LiteLLM's munged `User-Agent` + no billing header gave the identical `allowed`).
> - **Caveat that stands:** LiteLLM **strips Anthropic's response headers**, so an operator can't observe
>   their own lane through the gateway. Harmless when overage is org-disabled (200 ⇒ subscription); on
>   overage-*enabled* accounts a silent demotion would bill as overage invisibly — the strongest remaining
>   argument for Option C (a thin custom passthrough front that forwards response headers verbatim).
> - The predicted discriminator `anthropic-ratelimit-unified-overage-in-use` did **not** appear (moot under
>   org-disabled overage); the operative signal is `unified-status: allowed` + window utilization. Still
>   **TODO**: pin the LiteLLM image by digest; (optional) Option C for header fidelity + removing LiteLLM
>   from the OAuth-token path; the local leg needs host Ollama running (it was down during this capture,
>   which does not affect the Claude leg).

# Subscription-through-a-proxy for claude-man's hybrid gateway: a wire-level decision document

## TL;DR

- The Claude passthrough leg does **not** 404 for the reason the bug report assumes. Claude Code 2.1.193 does **not** send the short alias `opus-4-8`; it sends the **full canonical id `claude-opus-4-8`** on `POST /v1/messages?beta=true` (first-hand binary, D2-c2…c6).
- The `opus-4-8` that Anthropic 404s on is **manufactured by claude-man's own LiteLLM config**: the wildcard row `model_name: claude-*` → `model: anthropic/*` (`network/gateway.py:64-66`) captures the suffix after `claude-` from `claude-opus-4-8`, substitutes it into `anthropic/*` to get `anthropic/opus-4-8`, then `get_llm_provider` strips the prefix and forwards the invalid id `opus-4-8` (litellm `pattern_match_deployments.py:100`, D3-c5).
- It is a **404, not a 401** — which proves **auth already works**: the subscription OAuth token is already reaching `api.anthropic.com` (D3-c6/c8). The 9a blocker is a **model-mapping bug, not an auth bug**.
- The premise that `?beta=true` routes LiteLLM to a verbatim "experimental passthrough" that bypasses the model router is a **misdiagnosis** (adversarial verification refuted it): FastAPI ignores the query string; `/v1/messages` always dispatches `route_type="anthropic_messages"` and the router resolves by **model name**. Removing/ignoring `?beta=true` would fix nothing.
- **Verdict on whether subscription-through-a-proxy is possible: YES, it is on Anthropic's officially documented path** — setting `ANTHROPIC_BASE_URL` *without* a gateway credential variable keeps the claude.ai login as the active billed credential (code.claude.com/docs/en/llm-gateway, D4-c1). The one real residual risk is **post-Jan-2026 client-fingerprinting**: a re-originating proxy changes the TLS fingerprint and (in LiteLLM) drops/replaces some genuine-CC headers, and only a **live capture** can confirm the request still lands in the Max lane.

---

## The protocol, hop by hop

There is **one** `ANTHROPIC_BASE_URL` for the whole agent; the base URL decides *where* requests go, not *which model* answers (D4-c9). Every tier — Opus/Sonnet, the background Haiku tier, and the local model — is POSTed to the same gateway. The split must therefore happen **inside the gateway, where TLS terminates** (a squid/CONNECT layer cannot see the model id in the encrypted body — D4-c9, invariant 3).

### (a) A built-in Claude tier (e.g. Opus selected in `/model`)

**Hop 1 — Claude Code → gateway**

| Field | Value | Evidence |
|---|---|---|
| Endpoint | `POST {ANTHROPIC_BASE_URL}/v1/messages?beta=true` | binary literal `/v1/messages?beta=true` (D1-c4, D2-c8); `?beta=true` is structural to the SDK `.beta.messages` namespace, not disableable |
| Model id (body `model`) | **`claude-opus-4-8`** (full canonical firstParty id) — NOT `opus-4-8` | `/model` option value is the short alias `"opus"` (D2-c1), but `Hs()=qo(aw())` resolves it to `b_()→Zp().opus48 = "claude-opus-4-8"` *before* the request, and the builder only applies `up()` which strips `[1m]/[2m]` and does not strip `claude-` (D2-c2…c5) |
| Proxy auth | `x-litellm-api-key: Bearer <master key>` (via `ANTHROPIC_CUSTOM_HEADERS`) | D1-c11; binary parses `ANTHROPIC_CUSTOM_HEADERS` newline/colon split |
| Subscription auth | `Authorization: Bearer <sk-ant-oat… OAuth token>` (never `x-api-key`) | binary `…"accessToken"in e?{Authorization:Bearer…,"anthropic-beta":Nk}…` (D1-c1) |
| `anthropic-beta` | comma-joined list incl. `claude-code-20250219` (non-haiku) **and** `oauth-2025-04-20` (OAuth+`user:inference` scope), plus `interleaved-thinking-2025-05-14`, `fine-grained-tool-streaming-2025-05-14`, `context-1m-2025-08-07` (if `[1m]`), prompt-caching betas | `T3r`/`DFe`/`sCe`, gated by `Eo()` (D1-c2, D2-c10) |
| Other headers | `anthropic-version: 2023-06-01`, `x-app: cli`, `User-Agent: claude-cli/2.1.193 (external, cli)`, `X-Claude-Code-Session-Id`, `anthropic-client-platform`, `x-anthropic-billing-header: cc_version=…; cc_entrypoint=…` | D1-c3; billing header from D1-c7 refute lens (first-hand) |

**Hop 2 — gateway (LiteLLM) → api.anthropic.com** — *where it breaks today*

- LiteLLM dispatches `route_type="anthropic_messages"` and resolves the body model `claude-opus-4-8` against the model_list. The only matching row is the wildcard `claude-*`, whose pattern compiles to `claude\-(.*)`; the captured group is `opus-4-8`, substituted into `anthropic/*` → `litellm_params.model = anthropic/opus-4-8` → `get_llm_provider` strips `anthropic/` → **forwards `opus-4-8`** (D3-c5).
- Auth IS forwarded correctly on current LiteLLM (the reason it's a 404 not a 401): `clean_headers()` preserves the `Authorization: Bearer sk-ant-oat…` when the proxy was authenticated via `x-litellm-api-key`; `add_provider_specific_headers_to_request()` scopes it to anthropic-family providers; `optionally_handle_anthropic_oauth()` drops `x-api-key` and sets `anthropic-beta: oauth-2025-04-20` (D3-c8/c9). **Note:** this rides a *separate* mechanism, **not** `forward_client_headers_to_llm_api` (that setting forwards only `x-*` + `anthropic-beta`, never `Authorization` — D3-c7).
- **Break point:** `api.anthropic.com` returns `404 not_found_error: "model: opus-4-8"` because `opus-4-8` is not a valid API model id. `claude-opus-4-8` *is* valid (D2-c4, claude-api reference) — so the model the agent actually chose would succeed if the gateway hadn't mangled it.
- Residual transport deltas vs a direct CC→Anthropic call: LiteLLM re-originates TLS via Python `httpx` (not Node undici), **replaces `User-Agent` with `litellm/<version>`**, and forwards only `x-*` (minus `x-stainless`) + `anthropic-beta` + the OAuth `Authorization` (D1-c10 verification). `x-anthropic-billing-header` is `x-`prefixed and **survives**; the `claude-cli` User-Agent and any non-`x-` header **do not**.

### (b) The local model (working leg)

| Field | Value | Evidence |
|---|---|---|
| Endpoint | same `POST {BASE}/v1/messages?beta=true` | D2-c8 |
| Model id | `claude-local-<model>` — the **verbatim** `ANTHROPIC_CUSTOM_MODEL_OPTION` string (not a known alias, so `qo()` returns it unchanged) | D2-c13 |
| Routing | matches an explicit `model_list` row → `ollama_chat/<model>` (host Ollama) | gateway.py |
| OAuth header | present in `Authorization` but **not applied**: `provider_specific_header` is gated to anthropic/bedrock/vertex, so it does NOT fire on the ollama route | D3-c9 |

This leg works precisely because it uses an explicit, non-aliased id and a non-anthropic provider — i.e. it sidesteps both the wildcard-substitution bug and the OAuth-forward path.

---

## Does the subscription survive a proxy?

**CONFIRMED — the configuration is officially sanctioned.** Anthropic's own gateway docs state verbatim: "Setting only `ANTHROPIC_BASE_URL`, without a gateway credential, doesn't replace the subscription… a saved claude.ai login remains the active credential, so its usage limits and billing apply. Gateways that pass this traffic on to Anthropic must forward the OAuth capability in `anthropic-beta`" (D4-c1). The protocol reference adds: forward `anthropic-beta` and `anthropic-version` **verbatim** (stripping `anthropic-beta` fails subscription requests with 401), and "Inference requests post to `/v1/messages?beta=true`, so match on the path, not the full URL" (D4-c2/c3). claude-man maps onto the "no gateway credential variable" case exactly: it authenticates the agent to the sidecar via the **custom** `x-litellm-api-key` header and never sets `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` (a "gateway credential variable" means those env vars, not arbitrary custom headers — D4-c4).

**UNCERTAIN — whether a *re-originated* genuine-CC request still lands in the Max lane post-Jan-2026.** This is the D1 crux and it gates the feature. Three things are now clearer than the original finders stated:

1. **The gate is NOT "valid token + beta headers" alone, and it is NOT system-prompt-independent.** The original "confirmed-negative" (D1-c7: subscription not gated on fingerprint) is **DISPUTED / largely refuted**. Independent post-crackdown sources document an **application-layer** classifier scoring the whole request body: the system array's first text block must equal `"You are Claude Code, Anthropic's official CLI for Claude."` (literal `AVr` in the binary), tool-name combinations above a ~25-tool threshold flag third parties, system-prompt template structure (~26K threshold) is fingerprinted, and the `x-anthropic-billing-header` is read (D1-c7 corroborate/refute lenses; D1-c9 refute lens). Anthropic deployed "strict new technical safeguards against spoofing the Claude Code harness" in Jan 2026 and formalised a ToS clause Feb 19 2026 (D1-c8, partly a clarification of pre-existing terms).
2. **The decisive distinction in claude-man's favour:** every documented subscription-loss case is a **third-party harness fabricating** CC-shaped requests. claude-man runs the **genuine CC binary**, so the system prompt, tool definitions, body, and `x-anthropic-billing-header` are **authentic** (D1-c10). The genuine binary passes every application-layer check by construction.
3. **The two remaining proxy-induced deltas** are therefore the only realistic failure vectors: (a) the **TLS/HTTP-2 transport fingerprint** (httpx, not undici — Anthropic sees the sidecar's fingerprint, not CC's), and (b) **header loss/replacement** by LiteLLM (notably `User-Agent` → `litellm/<version>`). No public source confirms Anthropic keys on the transport layer; the favoured public hypothesis is application-layer telemetry + token-to-client binding (D1-c9 refute lens). Whether either delta demotes the request is **unresolved by research alone** and is the single thing a live capture must settle (next section).

Bottom line: subscription-through-a-proxy is *possible and documented*; whether *this specific stack* (genuine CC behind LiteLLM/httpx) keeps the Max lane is an empirical question with a cheap, decisive test.

---

## Why LiteLLM mis-routes

**The `?beta=true` theory is wrong (finders vs verification disagreement — flag this).** D1-c4 and D4-c5 claimed `?beta=true` routes `/v1/messages` to an experimental passthrough handler that bypasses the model_list router. **Adversarial verification refuted this on first-hand LiteLLM source:**

- `@router.post("/v1/messages")` is registered **path-only**; FastAPI matches on path and **ignores the query string**. `?beta=true` is consulted only for `/v1/messages/count_tokens` and `/v1/skills` (D3-c1/c2). The same handler runs with or without it.
- The native `/v1/messages` route always calls `base_process_llm_request(route_type="anthropic_messages")` → `route_request()`, which decides router-vs-verbatim **purely from the body model name** against `router_model_names`/`model_group_alias`/`pattern_router`/`pass_through_all_models`/`default_deployment` (D3-c1/c10, D1-c4 refute lens).
- `litellm.anthropic_messages` (the file `experimental_pass_through/messages/handler.py`) is the **SDK transport the router always invokes** for anthropic-family models — not a separate bypass route. The URL-prefixed `/anthropic/v1/messages` passthrough is a *different* endpoint that CC never hits (D3-c3).
- For `route_type="anthropic_messages"`, verbatim-forward-on-no-deployment is **disabled** (`passthrough_on_no_deployment` defaults False). So the observed forward means a deployment **did** match — the wildcard (D3-c4).

**Auth precedence (correct in claude-man's design):** `x-litellm-api-key` is checked **before** `Authorization` in `user_api_key_auth` (D3-c11), so the master key authenticates the proxy and the OAuth `Bearer` is left free for the upstream — sidestepping the open inbound-precedence bug #29190. `forward_client_headers_to_llm_api: true` is **largely redundant** for the OAuth (which rides `provider_specific_header`, not that allowlist) but harmlessly forwards `anthropic-beta` and agent `x-*` headers; the gateway.py:73-74 comment claiming it forwards the OAuth is **inaccurate** and should be corrected.

**Can LiteLLM config fix it?** Yes — the fix is **purely model mapping**, not routing or auth. Replace the `claude-* → anthropic/*` wildcard with **explicit `model_list` rows** (or a `model_group_alias`) mapping each exact id CC sends to a valid Anthropic id with **no suffix capture**:

```
claude-opus-4-8            → anthropic/claude-opus-4-8
claude-sonnet-4-6          → anthropic/claude-sonnet-4-6
claude-haiku-4-5-20251001  → anthropic/claude-haiku-4-5-20251001   # background/Haiku tier
```

This is exactly the shape of LiteLLM's official Claude-Code-Max tutorial (explicit rows, no wildcard — D3-c14). The same rows must also serve `/v1/messages/count_tokens`, which CC calls and which resolves through the model_list identically (D3-c15).

---

## Model-id mapping problem

- **What CC sends on the wire:** the **full firstParty canonical id**, because aliases are resolved before the request: Opus → `claude-opus-4-8`, Sonnet → `claude-sonnet-4-6`, default background/Haiku → `claude-haiku-4-5-20251001`, Fable → `claude-fable-5` (D2-c4). The `[1m]` 1M-context variant is signalled by the `context-1m-2025-08-07` beta after `up()` strips the `[1m]` suffix from the id (D2-c5/c10).
- **What the short strings `opus-4-8`/`opus-4-7`/`opus-4-6` actually are:** `.includes()` **matching patterns** for org allowlists, capability/launch-effort lookup, and legacy-id remap — **never** assigned as a wire `model:` value (D2-c6, both verification lenses; grep for `model:"opus-4-8"` = 0 hits). A bare `opus-4-8` is even promoted *up* to `claude-opus-4-8` by an internal normalizer. So the `opus-4-8` Anthropic complained about is unambiguously a gateway artefact.
- **What Anthropic accepts:** `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5` / `claude-haiku-4-5-20251001`, `claude-fable-5` are all valid API model strings (claude-api reference; AWS Bedrock cards corroborate the Bedrock-prefixed forms — D2-c4). Because these are valid, a passthrough that forwards them **verbatim** (no wildcard suffix-capture) returns 200.
- **Background tier caveat:** the Haiku tier is a real second model the gateway must route (`ANTHROPIC_DEFAULT_HAIKU_MODEL` verbatim, else `claude-haiku-4-5-20251001` — D2-c14). Don't map only Opus/Sonnet. Note also `claude-code-20250219` is *not* sent for haiku-tier requests (`if(!r)` gate — D1-c2 refute lens); `oauth-2025-04-20` still is.
- **Discovery interplay:** `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY` GETs `{BASE}/v1/models` and **silently drops any id not matching `/^(claude|anthropic)/i`** (D2-c11/c12). So `claude-local-*` ids would survive discovery, but a model named `opus-4-8` would be dropped — another reason the local leg uses the `claude-local-` prefix. Discovery authenticates with `Bearer <jwt>` from the gateway descriptor, *not* `x-litellm-api-key`, which may explain any discovery failure observed (open question).

---

## Fix options, ranked

### Option A — LiteLLM config-only (explicit model_list rows)
- **Model routing:** explicit rows / `model_group_alias` map `claude-opus-4-8` etc. → `anthropic/<valid id>`; drop the wildcard. Solves the 404.
- **OAuth passthrough:** already works on current LiteLLM (`clean_headers` + `provider_specific_header` + `optionally_handle_anthropic_oauth`, D3-c8) given `x-litellm-api-key` proxy auth.
- **`?beta=true`:** non-issue (ignored for routing).
- **Alias map:** solved by the explicit rows.
- **Cost:** config string change in `network/gateway.py` only; **no new image, no new code path**. Pin the LiteLLM image by **digest** (replace the moving `main-stable` tag) to a known-good ≥1.91.x that postdates both the complete OAuth fix (PR #19453→#19912) and the **March 2026 supply-chain compromise** of PyPI `litellm` 1.82.7/1.82.8 (D3-c12/c13, D4-c14).
- **Invariant impact:** Inv 1 — the long-lived OAuth token now transits the LiteLLM image (a credential-stealer in that image could exfiltrate it; the official Docker image was *not* hit by the PyPI incident, but the dependency surface is large). Inv 2/3 — additive env only, floor unchanged.
- **Residual risk:** (i) LiteLLM **replaces `User-Agent`** and drops non-`x-` headers — if the classifier keys on UA, the subscription could be demoted; (ii) OAuth-forward path is recent and was half-fixed across several releases (version-fragile); (iii) heavy/often-CVE'd dependency.

### Option B — LiteLLM for local + thin custom passthrough front for Claude
- Route the agent at a tiny custom proxy that handles `/v1/messages*` by **path prefix**, forwards Claude traffic to `api.anthropic.com` **verbatim** (preserving `User-Agent`, billing header, full beta list, OAuth `Authorization`), and proxies `claude-local-*` to the LiteLLM sidecar (or Ollama).
- **Model routing/alias:** trivial — Claude ids pass verbatim (no remap needed since CC already sends valid full ids); local ids forwarded by name.
- **OAuth/`?beta=true`:** both trivially correct (pass-through, path-prefix match — exactly the John-Rood/claude-proxy shape, ~150 lines, D4-c7).
- **Cost:** one new small sidecar image + a pure-Python renderer (mirrors `network/squid.py`). Keeps LiteLLM only for the local leg.
- **Invariant impact:** Inv 1 — OAuth token transits the **custom** front (near-zero deps = strongest posture) but the LiteLLM dep is still present for local. Inv 2/3 — additive.
- **Residual risk:** two sidecars to maintain; still carries LiteLLM's dep surface for local.

### Option C — Single custom passthrough proxy doing BOTH legs *(recommended)*
- One ~150–250-line pure-Python/Node proxy: path-prefix match on `/v1/messages*`; if `body.model` starts `claude-local-` → forward to **host Ollama's native Anthropic Messages API** (Ollama speaks it natively since v0.14.0 — D4-c12, no translation layer needed); else forward **verbatim** to `api.anthropic.com` preserving every header.
- **Model routing:** body-`model` inspection (TLS terminates here, so the body is visible — the split squid cannot do).
- **OAuth passthrough:** forward `Authorization` + `anthropic-beta` unchanged (Anthropic's documented requirement, D4-c2) — and critically **preserve `User-Agent: claude-cli/…` and `x-anthropic-billing-header`**, eliminating the LiteLLM header-replacement risk.
- **`?beta=true` + alias map:** both non-issues (verbatim forward; CC already sends valid full ids).
- **Cost:** one new sidecar image (mirrors `images/proxy/` squid pattern) + a pure renderer in `network/gateway.py` style. **Eliminates the entire LiteLLM OAuth blast radius and supply-chain exposure** (D4-c14/c15).
- **Invariant impact:** Inv 1 — best possible (token only ever touches genuine CC + a minimal, audited, on-host proxy; never a remote third party — contrast y-router, D4-c11). Inv 2/3 — additive net + `HTTP(S)_PROXY`-style wiring, floor byte-identical.
- **Residual risk:** claude-man owns the code (small, but yours to maintain); still cannot defeat a **transport-layer** fingerprint if Anthropic deploys one (no proxy that re-originates TLS can — this risk is common to A/B/C and is the live-capture question).

### Option D — split at the squid/network layer
- **Infeasible.** One global `ANTHROPIC_BASE_URL`, model id inside the encrypted body, squid runs CONNECT tunnels with no TLS termination (D4-c9, invariant 3). Listed only to close it out.

### Recommendation

**Sequence, not a single jump:**

1. **First, unblock and measure with Option A's config fix** (lowest effort): replace the wildcard with explicit rows, pin the LiteLLM image by digest, and run the **one live-capture experiment** below. This is consistent with "research first, don't just try stuff" — the config fix is the minimal instrument needed to *observe* the only thing research cannot settle (does the Max lane survive re-origination?).
2. **If the subscription survives** the capture: decide A-vs-C on **security/maintenance grounds**, not function. Given claude-man's stated ethos (pure renderers, minimal dependencies, smallest possible OAuth blast radius, invariant 1 as the crown jewel) and LiteLLM's recent OAuth fragility + March-2026 supply-chain incident, **Option C is the strategic target** — it removes a large untrusted dependency from the path of the long-lived subscription token and gives byte-faithful header forwarding (best fingerprint fidelity).
3. **If the capture shows demotion caused by header loss** (e.g. the dropped `User-Agent`): Option C is **forced** — it is the only one that preserves CC's headers verbatim. If demotion persists even under verbatim forwarding, the cause is transport-layer and **no relay can keep the subscription** — the feature would be local-only + a documented "Claude tier bills as overage" caveat.

---

## What is still uncertain / must be settled by a live capture

These are the claims marked DISPUTED/UNCERTAIN that research alone cannot close. The operator's "research first" instruction means these are the **next** step, each with the single cheapest experiment.

1. **Does genuine CC behind a re-originating proxy keep the Max subscription lane? (the feature-gating unknown — D1-c10, D1 open questions.)**
   *Cheapest experiment:* apply Option A's model-mapping fix, send **one tools-carrying** request through the gateway, and read the response headers. **Decisive discriminator (corrected from D1-c12):** `anthropic-ratelimit-unified-overage-in-use` — **`false` = request stayed in the subscription/Max lane (subscription kept)**; `true` = demoted to the overage/pay-per-token lane. Read the active window from `anthropic-ratelimit-unified-representative-claim` (`five_hour` vs `seven_day`) and confirm its `*-utilization` incremented. **Do NOT** use `overage-status` as the test: `overage-status: rejected` is the *normal* default for a Max account with no pay-as-you-go credits and coexists with a successful in-subscription `status: allowed` (D1-c12 both lenses). On accounts where overage is org-disabled, the simple 200-vs-400 reading is also decisive.

2. **Does LiteLLM's `User-Agent` replacement / header set cause demotion? (D1-c10 verification.)**
   *Cheapest experiment:* point `ANTHROPIC_BASE_URL` at a **logging proxy** (a 20-line mitm/echo that prints exactly what is re-originated to `api.anthropic.com`) for one request; diff the captured headers against a direct CC→Anthropic call. Confirms whether `User-Agent: claude-cli/…`, `x-anthropic-billing-header`, and the full `anthropic-beta` list survive. This also resolves D2/D3 open questions about header order/casing and any injected `Via`/`X-Forwarded-*`.

3. **Exact wire model strings CC sends to the gateway, and discovery auth. (D2/D3 open questions.)**
   *Cheapest experiment:* the same logging-proxy capture records `body.model` for the main loop, the background/Haiku tier, and `count_tokens`, plus which credential the `/v1/models` discovery GET carries (`Bearer <jwt>` vs `x-litellm-api-key`). Pins the explicit `model_list` rows precisely and explains any discovery failure.

4. **Is Anthropic's classifier transport-layer (TLS/JA3-JA4, HTTP-2) or application-layer? (D1-c9, DISPUTED.)**
   *Research cannot settle this* (no public source documents transport fingerprinting; the favoured public hypothesis is application-layer telemetry + token binding). Experiment #1's result is the practical answer: if the genuine-CC-via-proxy request keeps the Max lane, the transport delta does not matter for this account; if it demotes *even under verbatim header forwarding* (Option C / experiment #2 controlled), the gate is transport-level and the feature cannot keep the subscription through any relay.

5. **ToS standing of agentic use of a consumer Max subscription via a self-hosted relay. (D4 open question, not adjudicated.)**
   *Cheapest action:* read the current Claude consumer/usage terms directly before shipping. The Feb 19 2026 clause restricts subscription OAuth to "Claude Code and Claude.ai"; claude-man runs genuine Claude Code, but routing through a self-hosted relay is an operator-risk call to make explicitly.

---

## Claims & confidence appendix

| Claim | Net verdict | Corrected wording (where refute lens changed it) |
|---|---|---|
| D1-c1 OAuth ⇒ `Authorization: Bearer` + `anthropic-beta: oauth-2025-04-20`; `x-api-key` only for API-key auth; mutually exclusive | **CONFIRMED** | `oauth-2025-04-20` rides as one value in a comma-joined `anthropic-beta` list; "mutually exclusive" is a property of CC's credential resolution (apiKey==null under OAuth), not a hard guard. |
| D1-c2 `anthropic-beta` built by `T3r`: `claude-code-20250219` (non-haiku) + `oauth-2025-04-20` (OAuth scope) | **PARTLY-CONFIRMED** | Mechanism confirmed, but **not "always both"**: haiku-tier requests carry `oauth-2025-04-20` **without** `claude-code-20250219` (`if(!r)` gate). `oauth-2025-04-20` also has an alt push path. |
| D1-c3 default headers incl. `x-app`, UA `claude-cli/… (external, cli)`, session-id, client-platform, version; "external" is genuine, not a 3P marker | **PARTLY-CONFIRMED** | UA literal + "external is genuine, not a discriminator" + `anthropic-version: 2023-06-01` independently confirmed; `x-app`/`X-Claude-Code-Session-Id`/`anthropic-client-platform` are first-hand-binary-only. |
| D1-c4 CC posts `/v1/messages?beta=true`; **`?beta=true` causes LiteLLM passthrough routing** | **PARTLY-CONFIRMED** | Endpoint + verbatim-404 outcome confirmed; **causal claim refuted** — FastAPI ignores the query string; routing is by body model name. Removing `?beta=true` fixes nothing. |
| D1-c5 setup-token is inference-only; `user:inference` satisfies `Eo()` so subscription inference works; usage bars need `user:profile` | **CONFIRMED** | — |
| D1-c6 setup-token TTL 1yr; login gets broader scopes; refresh `POST /v1/oauth/token`; static env token can't refresh on 401 | **PARTLY-CONFIRMED** | Binary confirms `LONG_LIVED_OAUTH_TOKEN_TTL_SECONDS=31536000`, the user:profile/"Remote Control" gap, and the 5-min/401 refresh. (One verification lens disputed the scope-gap from external sources; first-hand binary settles it in favour.) |
| D1-c7 subscription **NOT** gated on system prompt / header reproduction (Hermes "confirmed-negative") | **DISPUTED** | Refuted as a general claim: the post-Jan-2026 gate **is** application-layer (system-prompt first block, tool-name combination ~25, template structure ~26K, `x-anthropic-billing-header`). Hermes' tools→400/no-tools→200 is account/org/model-tier dependent (overage config), not a clean fingerprint-independent gate. **Header replay alone is insufficient** (that part holds). |
| D1-c8 Jan-2026 server-side anti-spoof safeguards (Shihipar); Feb-19-2026 ToS clause | **PARTLY-CONFIRMED** | Safeguards + error string + `oc_→mcp_` bypass confirmed first-hand; Feb-19 was a **clarification of pre-existing** Consumer ToS, not a brand-new ban; the section title "Authentication and credential use" is unverified. |
| D1-c9 exact classifier mechanism publicly unresolved; no doc'd TLS/HTTP-2 fingerprinting | **PARTLY-CONFIRMED** | "No documented TLS/HTTP-2 fingerprinting" holds; but the framing omits the **best-documented** mechanism (application-layer telemetry + token-to-client binding + system-prompt-first-block), which is the publicly favoured explanation — so it is materially less "unresolved." |
| D1-c10 genuine CC ⇒ only residual deltas are transport fingerprint + header munging; that's the load-bearing unknown | **PARTLY-CONFIRMED** | Core deltas confirmed and sharpened: LiteLLM **replaces UA with `litellm/<version>`**, forwards only `x-*`+`anthropic-beta`+OAuth `Authorization`; does **not** inject `Via`/`XFF` outbound by default. Also add the **account/credential-scope/ToS** axis the claim omitted. |
| D1-c11 `forward_client_headers_to_llm_api: true` forwards the OAuth; `x-litellm-api-key` for proxy auth | **PARTLY-CONFIRMED** | `x-litellm-api-key` precedence correct; but `forward_client_headers_to_llm_api` does **NOT** forward `Authorization` — the OAuth rides a **separate** dedicated path (`clean_headers`/`provider_specific_header`/`optionally_handle_anthropic_oauth`). The supported recipe pins a **named** model (no wildcard); gateway.py's wildcard is the divergence that causes the bug. |
| D1-c12 lane is observable via response headers; `overage-status: rejected` = demoted | **PARTLY-CONFIRMED** | Observability confirmed; **discriminator corrected**: use `anthropic-ratelimit-unified-overage-in-use` (false=subscription kept) + `representative-claim` window. `overage-status: rejected` is the normal Max default (no PAYG credits) and is **not** a demotion signal. |
| D2-c1 built-in `/model` value is a short alias under firstParty/gateway; full id only for 3P | **CONFIRMED** | Even stronger than stated — base Opus / Haiku-3.5 builders emit `"opus"`/`"haiku"` unconditionally. |
| D2-c2 active model alias-resolved to full canonical id before request (`mainLoopModel:Hs()`) | **PARTLY-CONFIRMED** | `qo()` canonicalizes **tier aliases** (sonnet/opus/haiku/fable/best/opusplan); a non-tier short id like `opus-4-8` falls through `eD/Bge` to `return TC(t)` **verbatim**. The short→canonical mapper (`TCa`) is **telemetry-only**, not the wire path. |
| D2-c3 `qo()` resolves short aliases; non-alias passed verbatim; `[1m]` via `TC()` | **PARTLY-CONFIRMED** | `[1m]` is appended by string concat / `Hq()`, not `TC()` (which is identity). Verbatim passthrough holds **except** legacy full opus ids are remapped to current opus (Nie branch, default-on). |
| D2-c4 `b_()→claude-opus-4-8`; registry firstParty ids (sonnet-4-6, haiku-4-5-20251001, fable-5) | **CONFIRMED** | Corroborated by claude-api skill, Anthropic docs, Bedrock cards, community catalogs. |
| D2-c5 `up()` only strips `[1m]/[2m]`; wire model for Opus = `claude-opus-4-8` | **CONFIRMED** | The `gateway:` registry field also keeps the `claude-` prefix; no `^claude-` strip exists anywhere on the outbound path. |
| D2-c6 short `opus-4-8…` strings are `.includes()` match patterns, never wire ids | **CONFIRMED** | Zero `model:"opus-4-8"` assignments; all occurrences are substring tests / normalizer inputs. |
| D2-c16 (inference) the observed 404 is a LiteLLM/config cause, not a CC-wire cause | **CONFIRMED** | Pinned by D3-c5 to the `claude-*`→`anthropic/*` wildcard suffix-capture. |
| D3-c1…c4 `/v1/messages` always `route_type="anthropic_messages"`; `?beta=true` irrelevant to routing; no verbatim-on-no-deployment for this route | **CONFIRMED** (litellm source) | — |
| D3-c5 ROOT CAUSE: `claude-*`→`anthropic/*` wildcard captures `opus-4-8` → 404 | **CONFIRMED** | The single load-bearing fix target. |
| D3-c6 404-not-401 ⇒ auth already succeeded | **CONFIRMED (inference)** | — |
| D3-c7 `forward_client_headers_to_llm_api` forwards only `x-*`+`anthropic-beta`, never `Authorization` | **CONFIRMED** | — |
| D3-c8/c9 OAuth forwarded via `clean_headers`+`provider_specific_header`+`optionally_handle_anthropic_oauth`, gated to anthropic-family | **CONFIRMED** | Present on current main (≥1.91.x); pre-fix versions fail with x-api-key errors. |
| D3-c10/c14 fix = explicit `model_list` rows / `model_group_alias`, not wildcard (matches official tutorial) | **CONFIRMED** | — |
| D3-c11 `x-litellm-api-key` checked before `Authorization` | **CONFIRMED** | — |
| D3-c12/c13 OAuth fix landed PR #19453→#19912; pin a digest postdating the OAuth fix **and** the March-2026 PyPI compromise (1.82.7/1.82.8) | **CONFIRMED** | Use the official Docker image pinned by digest, not loose PyPI. |
| D4-c1/c2/c3 Anthropic officially supports subscription-through-gateway (no gateway credential var); forward `anthropic-beta`/`anthropic-version` verbatim; match on path | **CONFIRMED** (official docs) | — |
| D4-c5 the 404 is `?beta=true`→experimental passthrough (structural to LiteLLM) | **DISPUTED** | Superseded by D3 verification: the cause is wildcard model substitution under the always-on `anthropic_messages` route, not a `?beta=true` branch. |
| D4-c7/c8 a thin path-prefix front (≈150 lines) handles `?beta=true` + verbatim forward; claude-man adds at most a model normalization (not needed since CC sends valid full ids) | **CONFIRMED** | Model normalization is optional — CC already emits valid `claude-opus-4-8`. |
| D4-c9 cannot split at squid/CONNECT layer | **CONFIRMED** | — |
| D4-c12 Ollama speaks native Anthropic Messages API since v0.14.0 (enables a single custom proxy) | **PARTLY-CONFIRMED (community)** | Verify the beta/body field set Ollama accepts under a hardened-container test before relying on it for Option C's local leg. |
| D4-c14 OAuth token now transits the gateway; LiteLLM PyPI supply-chain risk; custom minimal proxy = strongest invariant-1 posture | **CONFIRMED** | Decisive security argument favouring Option C. |
| D4-c15 recommend single custom proxy (C) or LiteLLM-local + custom-Claude-front (B) | **PARTLY-CONFIRMED (inference)** | Endorsed, sequenced behind the live-capture gate (measure subscription survival first). |