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
- Never copy `.credentials.json` into a container to "fix" this — invariant 1 forbids it, and the
  headless refresh bug makes it self-defeating anyway.

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

## Usage bars stuck on "http 429" / "re-mint" — setup-token cannot mint `user:profile` (June 2026)

**Status: open upstream, SHELVED by the operator (2026-06-11) — `claude setup-token` has no way to
request the usage scope, and we are deliberately not experimenting against the auth flow. Don't
chase the `re-mint` note; re-test only after a claude update (command at the bottom).**

### Symptom

The TUI usage panel / `profile limits` shows `http 429` for tokens minted before 2026-06-05, and
`re-mint` (a clean 403: "OAuth token does not meet scope requirement user:profile") for tokens
re-minted after. Re-minting via `profile renew` does NOT activate the 5h/Week bars.

### Root cause

`claudemanctl profile renew` sets `CLAUDE_CODE_OAUTH_SCOPES="user:profile user:inference"` before
running `claude setup-token` (per `profiles/setup_token.py` / the `usage_api.py` docstring), but
**that env var never shaped the mint**. Captured the OAuth authorize URL `setup-token` generates
with the override exported, on claude 2.1.160 *and* 2.1.173: both request `scope=user:inference`
only. Disassembling the env var's real role in the binary: it annotates which scopes the client
should *assume* an injected `CLAUDE_CODE_OAUTH_TOKEN` carries (`scopes: U87()` in the credential
object) — it is consumption-side, not mint-side. So the "renew to gain the usage scope" claim in
CLAUDE.md / the `usage_api.py` docstring was never achievable; the endpoint's 403 is genuine
server-side scope enforcement. (Why old tokens show 429 instead of 403: scope-less tokens from the
pre-2026-06-05 mints land in an aggressively rate-limited bucket; a fully-scoped host credential
returns 200 — verified.)

### Decision (2026-06-11)

Shelved. The operator chose not to run the hand-edited-URL experiment below — working inference
auth on two accounts is not worth risking against an undocumented grant path. Consequences left
in place on purpose until upstream moves:

- The 5h/Week bars stay inactive (`re-mint` for post-renewal tokens, `http 429` for pre-renewal).
- The `re-mint` note in `usage_api.py` and the CLAUDE.md "renew to gain the usage scope" text are
  **known-misleading but unchanged** — reword them to "scope upstream-locked" if this stays shelved.
- The work profile token was NOT renewed (still the Jun 3 mint; valid for inference until ~Jun 2027).

### What to try / do (if un-shelved)

- **Hand-edited authorize URL** (unverified — the server may clamp the grant): run
  `claude setup-token`, copy the printed authorize URL, change `scope=user%3Ainference` to
  `scope=user%3Ainference%20user%3Aprofile`, approve in the browser, continue the CLI flow, and
  store the token into `~/.local/state/claude-man/profiles/<name>/token` (0600). If the resulting
  token passes `profile limits`, the bars work; if approval fails or it still 403s, the grant is
  server-clamped.
- Otherwise: the bars stay off until Anthropic allows scoped setup-token mints. The `re-mint`
  note in `usage_api.py` (and the CLAUDE.md "renew to gain the usage scope" text) is misleading
  and should be reworded to say the scope is upstream-locked.

### How to re-test (after a claude update)

```bash
BROWSER=false timeout 12 claude setup-token 2>&1 | grep -ao 'scope=[^&]*'
# fixed ⇒ the authorize URL requests user:profile (or setup-token grows a --scopes flag)
```

