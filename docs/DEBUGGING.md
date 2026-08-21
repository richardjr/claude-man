# Debugging findings

Investigations into container behaviour that looked like claude-man bugs. Each entry records the
symptom, the evidence chain, the root cause, and how to re-test — so the next "the container is
broken" report starts from facts instead of re-deriving them.

## Fable 5 missing from `/model` under setup-token auth (June 2026)

**Status: open upstream — re-test on each claude version bump (see "How to re-test" below).**

### Symptom

After the image rebuild to claude 2.1.172 on 2026-06-10 (the on-start update offered by
`up`/`recreate`), claude inside every container:

- shows the banner **"Sonnet 4.6 · Claude API"** instead of "… · Claude Max",
- lists no **Fable 5** row in `/model` (only Sonnet 4.6 default / Sonnet 1M / Opus 4.8 / Haiku),
- defaults to Sonnet instead of the plan default.

The same accounts on the host (normal `claude.ai` login) show "Fable 5 · Claude Max" and the full
picker. Before the rebuild — claude **2.1.160** in the same containers — Fable appeared in the
in-container picker too.

### Root cause

**Anthropic gates Fable 5's picker row server-side per *credential*, and denies it to long-lived
`claude setup-token` credentials** (the `CLAUDE_CODE_OAUTH_TOKEN` model claude-man uses —
invariant 1). The server pushes this into the config dir's `policy-limits.json`:

```json
"allow_cobalt_plinth": { "allowed": false }
```

(`cobalt_plinth` is Fable's gate codename; hand-edits revert — it is server-controlled. Upstream:
[anthropics/claude-code#66827](https://github.com/anthropics/claude-code/issues/66827).) The same
credential distinction leaves `clientDataCache` / `additionalModelOptionsCache` empty (the caches
that feed the host's Fable picker row) and `auth status` without `subscriptionType` — which is why
the banner falls back to "Claude API". A full creds-file login on the *same account* gets no
restrictions at all.

**Why it regressed at the 2.1.160 → 2.1.172 bump:** 2.1.160 surfaced Fable via the **anonymous**
GrowthBook feature-flag fetch (`cachedGrowthBookFeatures` — works fine under token auth; the
container's own `.claude.json` backups from 2026-06-10 17:46 carry the Fable flags and launch tip).
Claude ≥2.1.170 moved Fable gating to the credential-scoped policy/cache machinery above, which
denies setup-tokens. The container's first 2.1.172 flag re-fetch (22:41 backup) dropped the Fable
GrowthBook entries. So the *update* changed the gating mechanism; the container itself was fine.

### What was ruled out (all verified, not assumed)

- **Strict egress / the Phase 4 firewall** — no project was locked; no `HTTP(S)_PROXY` vars in any
  container env; `runner._render_egress` renders nothing for open projects (unit-pinned).
- **Token injection** — `CLAUDE_CODE_OAUTH_TOKEN` present in every container; no `ANTHROPIC_*`
  contamination (the scrub works); no API key hiding in the config binds (`settings.json` env
  blocks, `apiKeyHelper`, `primaryApiKey` all absent).
- **Account entitlement** — both the home and work tokens hit the same gate; the home account
  demonstrably has Fable on the host.
- **The container environment at all** — the gate reproduces on the *host* with the same binary,
  the token env var, and a throwaway `CLAUDE_CONFIG_DIR`. Probed claude 2.1.160 / 2.1.170 /
  2.1.172 / 2.1.173 in isolation: every version gets `allow_cobalt_plinth: false` and an
  `auth status` without subscription fields under token auth.

### What still works

`--model claude-fable-5` **inference is not gated** — verified on 2.1.170/172/173 with both
profile tokens. Only the picker row, the default-model selection, and the "Claude Max" label are
lost. The "Claude API" banner under token auth is cosmetic; requests still bill the subscription.

### Workarounds (operator's choice — Fable burns plan limits ~2× faster than Opus)

- Pin `"model": "claude-fable-5"` in the project's claude-config bind `settings.json`
  (`~/.local/state/claude-man/projects/<slug>/claude-config/settings.json`), or launch with
  `claude --model claude-fable-5` in the container.
- Keep the image on claude ≥2.1.173 (it normalises the `[1m]` model-ID suffix Fable carries).
- Do **not** pin the image back to 2.1.160: its `fable` alias errors outright, and the
  GrowthBook-era gating is being retired server-side, so the old picker behaviour won't return.
- **The supported full fix is per-project login mode** (`project auth <slug> login` — see the
  connectors entry below): a full login credential gets no restrictions, so the picker row and
  "Claude Max" banner return. Never copy a HOST `.credentials.json` into a container to "fix"
  this — invariant 1 forbids it and the headless refresh bug makes it self-defeating; login mode
  is different in kind (the credential is *minted inside* the container's own bind).

### How to re-test (after a claude version bump or a suspected server-side fix)

All on the host — no container needed. Read the token only into the process env, never argv:

```bash
# 1. Does token auth resolve the subscription yet?  Fixed ⇒ subscriptionType appears.
mkdir -p /tmp/fable-probe && CLAUDE_CONFIG_DIR=/tmp/fable-probe \
  CLAUDE_CODE_OAUTH_TOKEN=$(cat ~/.local/state/claude-man/profiles/home/token) \
  claude auth status

# 2. Is the gate still pushed?  Fixed ⇒ allow_cobalt_plinth gone/true (file is written on first run).
cat /tmp/fable-probe/policy-limits.json

# 3. Picker caches populated?  Fixed ⇒ additionalModelOptionsCache contains a Fable entry.
python3 -c "import json; j=json.load(open('/tmp/fable-probe/.claude.json'));
print(j.get('clientDataCache'), j.get('additionalModelOptionsCache'))"

rm -rf /tmp/fable-probe
```

If all three flip, the in-container picker will show Fable again on the next `project up` with a
current image — no claude-man change needed.

### Possible lever (untested for the picker)

The claude binary reads **`CLAUDE_CODE_SUBSCRIPTION_TYPE`** (and `CLAUDE_CODE_RATE_LIMIT_TIER`)
when building the env-token credential — i.e. an operator can *assert* the subscription tier for
token auth. It does not change `auth status` output (verified), and whether it restores the Fable
picker row / "Claude Max" banner in a live session is untested. If the upstream gate doesn't lift,
injecting `CLAUDE_CODE_SUBSCRIPTION_TYPE=max` into containers is the next experiment.

### Evidence trail (2026-06-11)

- Container env audits: `docker inspect <container> --format '{{json .Config.Env}}'` — token
  present, scrub intact, no proxy vars.
- Version timeline from the binds: transcripts under
  `…/projects/<slug>/claude-config/projects/` record `"version"` per line — 2.1.160 through
  Jun 10 17:47, 2.1.172 from 22:54; image labels confirm the rebuild at 22:32.
- `.claude.json` dotfile backups in the binds (`claude-config/backups/.claude.json.backup.<ms>`)
  snapshot the flag caches before/after the bump.
- Isolated per-version probes (downloaded via `claude.ai/install.sh <version>` into a temp HOME):
  auth status + `--model fable` + resulting `policy-limits.json` / cache keys, per version above.

## claude.ai connectors unavailable under setup-token auth (Aug 2026)

**Status: upstream-intentional (not a bug) — resolved in claude-man by the opt-in per-project
login auth mode (`project auth <slug> login`).**

### Symptom

Inside a (token-mode) container, `/mcp` never lists the account's claude.ai **connectors**
(remote MCP servers configured at claude.ai/settings/connectors) — Gmail/Drive/Linear/custom
connectors that work on the host and on claude.ai are simply absent. Locally-configured MCP
servers (`claude mcp add`, `.mcp.json`) work fine.

### Root cause

`claude setup-token` mints an OAuth token with **only the `user:inference` scope** — the docs
state it "can only make model requests". Account connectors are fetched **server-side, only when
the active auth is a full claude.ai subscription login**; the fetch needs scopes the setup-token
never carries (`user:mcp_servers` and friends — the full login scope set is
`user:profile user:inference user:sessions:claude_code user:mcp_servers`). The docs explicitly
list connectors as unavailable when `CLAUDE_CODE_OAUTH_TOKEN` is the active credential. This is
the same credential-scope family as the Fable picker gate above and the removed usage bars
(`user:profile`).

Upstream refs: [#22450](https://github.com/anthropics/claude-code/issues/22450) (`claude usage`
fails — setup-token lacks `user:profile`), [#21328](https://github.com/anthropics/claude-code/issues/21328)
(missing `user:profile` blocks usage data + Max features),
[#62556](https://github.com/anthropics/claude-code/issues/62556) (connectors fail without the
`user:mcp_servers` scope — the smoking-gun debug line), and
[#79597](https://github.com/anthropics/claude-code/issues/79597) (the Fable wall above).

### Resolution: login mode

`project auth <slug> login` + `project recreate <slug>`: no token env is injected; run `/login`
once inside the container (it prints a URL — authorise in the host browser and paste the code
back; no in-container browser needed). The minted credential lands in the project's claude-config
bind, self-refreshes in place, and carries the full login scopes — connectors appear in `/mcp`,
and the Fable picker/banner return. Token mode stays the default (docs/SECURITY.md residual risk
6 records the trade-off).

### Caveats

- **Context cost**: account connectors auto-load into every session with no per-environment
  opt-out ([#50062](https://github.com/anthropics/claude-code/issues/50062) reports ~100K tokens
  of tool definitions for a heavily-connected account). Consider a lean connector set on the
  account you use for containers.
- **Strict egress**: connector endpoints are remote MCP hosts — each needs a `project egress-log`
  → allowlist pass on a locked project (the `/login` flow itself rides the base allowlist's
  `claude.ai` + Anthropic entries).
- Local MCP (`claude mcp add`) never needed any of this — it works under token mode.

## Locked project (strict egress) troubleshooting

Strict egress (invariant 3) is the most failure-prone subsystem: a locked project routes ALL traffic
through a squid sidecar on a no-route `--internal` network. When a locked project misbehaves, the
fault is almost always the allowlist or the sidecar — not claude itself.

### `up` aborts with a sidecar / network error

`project up` is **fail-closed** for a locked project (`lifecycle.up` → `egress.ensure_network` then
`egress.ensure_proxy`): if the per-project network can't be made `--internal`, or the squid sidecar
can't build / start / connect to the bridge, the start **aborts and the agent never runs** — a locked
project must never come up with broken or absent egress enforcement.

- *"egress network … exists but is NOT --internal and can't be recreated (in use?)"* —
  `ensure_network` found a same-named network that isn't internal (a silent route out) and couldn't
  remove it because something is attached. Stop the attached container, then
  `docker network rm claude-man-net-<slug>` and retry `up`.
- *"squid sidecar failed to start"* / *"could not connect sidecar to egress bridge"* — inspect the
  sidecar: `docker logs claude-man-proxy-<slug>`. A bad rendered config or a missing
  `claude-man:proxy` image (auto-built on first lock; rebuild with `image build proxy`) is the usual
  cause. `ensure_proxy` removes a half-wired sidecar on failure, so a retry starts clean.

### A locked project can't reach an allowlisted host

Run the daemon-gated end-to-end check first — it probes an allowlisted host (must reach) and a
non-allowlisted host (must be blocked):

```bash
claudemanctl project egress-smoke <slug>   # PASS ⇒ allowlist enforces; FAIL names which side broke
```

If the smoke FAILs on *"allowlisted host … was NOT reachable"*, the allowlist is too strict. See what
the project actually tried to reach and got BLOCKED:

```bash
claudemanctl project egress-log <slug>     # blocked destinations, paste-able into egress.allowlist
```

Add the missing host in the TUI Egress screen (Project… `p` → `g`; allowlist extras, or promote a
blocked destination straight from the log), then **recreate** — `egress.render_conf` only re-renders
squid.conf on the next recreate, so an allowlist edit doesn't apply to a running sidecar. The base
allowlist always includes `claude.ai` (the OAuth refresh path, invariant 3); if token refresh fails
opaquely under lock, confirm that host is present.

### By design under lock: ssh-git outside the big forges, and direct DNS

Strict egress today covers **proxy-aware traffic plus the SSH-over-443 forges** (`HTTP(S)_PROXY` →
the squid CONNECT allowlist). Two things are NOT reachable from a locked project and that is
expected, not a bug:

- **Direct (non-proxy) DNS / TCP** — the agent is on an `--internal` net with no gateway, so anything
  that bypasses `HTTP(S)_PROXY` (raw sockets, a tool that ignores the proxy env) has no route out. The
  deferred `dnsmasq` direct-DNS layer + in-container default-DROP are ROADMAP Phase 4 defence-in-depth.
- **ssh-based git to hosts outside github / gitlab / bitbucket** — plain ssh does not honour
  `HTTP(S)_PROXY`, so it can't traverse the squid sidecar. For those three forges,
  `git@github.com:…`-style remotes **do work under lock**: `lifecycle._seed_ssh` rewrites them to
  their SSH-over-443 endpoints and `ProxyCommand`s through the sidecar on the same allowlist
  (issue #12; the project needs its `ssh` env-mount). Anything else — a self-hosted forge, Azure
  DevOps (no 443 SSH endpoint) — has no route: use HTTPS remotes there (the `egress-smoke` probe
  exercises both a `git ls-remote https://…` and an ssh-over-443 leg through the proxy), or leave
  the project unlocked while it needs ssh-git.

