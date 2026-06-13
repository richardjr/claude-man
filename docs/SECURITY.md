# Security model

claude-man runs an autonomous agent (Claude Code) against real code and a real account token. The
design assumes the **project code and the agent are sandboxed but not fully trusted**, and that the
**operator's host config + credentials must not leak**. This document records the threat model, the
controls, and the residual risks.

## Trust boundaries

```
        host (trusted)                         container (sandboxed, semi-trusted)
  ┌───────────────────────────┐          ┌──────────────────────────────────────┐
  │ ~/.config/claude-man  TOML │          │  claude (uid 1000, no caps, ro rootfs)│
  │ ~/.local/state/.../token   │── env ──▶│  CLAUDE_CODE_OAUTH_TOKEN (this proj)  │
  │ gh PAT (clone host-side)   │  0600    │  /workspace      (bind, rw)           │
  │ ~/.claude (sync-back tgt)  │◀─ gate ──│  /home/agent/.claude (bind, rw)       │
  └───────────────────────────┘  review  └──────────────────────────────────────┘
                                            egress: open  | strict (squid sidecar)
```

What crosses each way, and what must never cross:

| Direction | Allowed | Forbidden |
|---|---|---|
| host → container | the **one** profile's OAuth token (env), an opt-in `GH_TOKEN` when the operator configures one (`config gh-token`, injected pass-through), the non-secret git author identity (name/email), project env vars, the checked-out repos | `.credentials.json`, the host `gh` PAT / `~/.config/gh`, `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`, other profiles' tokens |
| container → host | review-gated config artifacts (agents, skills, commands, `settings.json` keys, MCP, memory, `CLAUDE.md`) | identity (`oauthAccount`/`userID`/`accountUuid`), **any credential/token** (auth is env-injected — no token file exists in the bind to refresh back), history/sessions/transcripts, statsig/caches, host-absolute paths, wholesale `.claude.json` |

## Controls

### Container hardening (defence against a compromised agent/project)
- `--read-only` rootfs; only `/workspace`, `/home/agent/.claude` (persistent binds), and
  `/tmp` + `/home/agent/.cache` (tmpfs) are writable.
- `--cap-drop ALL` + `--security-opt no-new-privileges` — no capability is ever held, none can be
  regained.
- `--user 1000:1000` (non-root, matches host uid so workspace edits keep correct ownership), real
  baked `/etc/passwd` entry.
- `--pids-limit 1024` bounds fork bombs while still allowing claude's tool subprocesses.
- `tmpfs` mounts are `nosuid`; `exec` is required (claude/MCP execute helpers from temp).
- The `/home/agent/.cache` tmpfs is pinned **agent-owned** (`uid=1000,gid=1000,mode=0700`). A bare
  tmpfs defaults to `root:root` mode `0755`, which the uid-1000 agent cannot write — so node/corepack
  (`mkdir ~/.cache/node`), claude's `XDG_STATE_HOME` (`~/.cache/state`), and the redirected
  git/`gh` config (below) all failed `EACCES`. This is a **writability fix, not a floor relaxation**:
  the surface is one already-declared-writable tmpfs (invariant 2's writable set is unchanged), it
  keeps `nosuid`/`exec`/size, and grants **no new capability** — it just makes the agent the owner of
  its own scratch mount. (`/tmp` needed no fix: Docker special-cases it to sticky `1777`.) The smoke
  gate probes the `.cache` write; `test_docker_argv` pins `uid=1000` on it. Applied on **recreate**
  (tmpfs options are fixed at `docker create`; no image rebuild).

### Credential isolation
- **No `.credentials.json` ever enters a container.** Auth is a long-lived token minted by
  `claude setup-token`, stored `0600` (dir `0700`) under `XDG_STATE`, injected per-launch as
  `CLAUDE_CODE_OAUTH_TOKEN`. `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` are scrubbed from the
  rendered env so they can't silently outrank the OAuth token or bill the wrong account — including
  any `env_file`, which is parsed + scrubbed host-side (not handed to `docker --env-file`) and its
  survivors injected as pass-through so secret values never appear in argv (`ps aux`).
- Each profile's token dir is isolated; work and home tokens never share a file. A switch-time
  email-mismatch guard refuses to cross identities into an existing config dir.
- The **`gh` PAT stays on the host** — repos are cloned host-side; the container only sees the
  working tree.
- **Git author identity is non-secret and injected without a writable file.** The container needs a
  `user.name`/`user.email` for `git commit` (the read-only rootfs blocks `git config --global`
  writing `~/.gitconfig` → *Author identity unknown*). claude-man renders the identity as git's
  **ENV-config** (`GIT_CONFIG_COUNT` + `GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n`, equivalent to
  `git -c user.name=…`) — needing no writable file — and, because name/email are not secrets, passes
  them as plain `-e KEY=value` (`gitconfig.env_for`). The identity is claude-man's `config.toml`
  `[git]` override, else inherited from the host operator's own `git config --global user.{name,email}`
  (`gitconfig.resolve_identity`); a blank/unset identity emits nothing. `GIT_CONFIG_GLOBAL` and
  `GH_CONFIG_DIR` are baked to point at the writable `.cache` tmpfs so any further `git config
  --global` / `gh` writes land somewhere writable instead of hitting `EROFS`.
- **`gh` ships as a binary; no token is injected by default.** The base image bakes a pinned GitHub
  CLI (`config.DEFAULT_GH_VERSION`). With nothing configured, claude-man injects no `gh` credential —
  the operator can authenticate in-container via `gh auth login` (writing the agent-writable
  `GH_CONFIG_DIR`). An operator may instead **opt in** to a managed token via `config gh-token`
  (TUI: Settings → `t`), which stores it `0600` in the **state tier** (`gh_token.py` →
  `config.gh_token_path()`; never in the secret-free `config.toml`, never synced) and injects it
  **pass-through** as `-e GH_TOKEN` (the name in argv, the value via the child env — `runner.py`
  `inject_gh_token`), only into containers, recreate-to-apply. This is safe under invariant 1
  because `GH_TOKEN` cannot outrank Claude auth or mis-bill an account. `config show` reports only
  set/none, never the value. No host `gh` PAT or host `~/.config/gh` is mounted, and `GH_TOKEN` is a
  `FORBIDDEN_ENV_NAME` — it can never be sourced from a `[[project.env_mount]]`, `project.env`, or an
  `env_file` (only the dedicated state-tier token is its source).
- **A managed ssh-agent socket forward is ownership-guarded.** Only a socket owned by the host
  operator is adopted into the container, so a hostile world-writable socket left in the agent's
  reach can't be smuggled in as the forwarded agent.

### Network containment (Phase 4 — implemented)
- Open egress by default; per-project **strict** mode (`project lock <slug>`) routes all egress
  through a squid sidecar on a per-project `--internal` network (no gateway → no direct route, so the
  proxy can't be bypassed). `ensure_network` verifies the `--internal` flag itself (not mere
  existence) and removes + recreates a same-named-but-non-internal network — failing **closed** if it
  can't (e.g. a container is attached) — so a leaky reused network can't silently grant a route out
  while the project still reports `Egress=strict`. The agent attaches to that network only; the sidecar is also on the
  default bridge (its sole egress path) and enforces a `dstdomain` allowlist over CONNECT tunnels (no
  MITM). `up` is **fail-closed** — a locked project never starts if the sidecar can't come up. The
  allowlist always includes `claude.ai` (token refresh) + the Anthropic API, GitHub, and the package
  registries; allowlist extras are validated **fail-closed** (`network/allowlist.is_valid_dstdomain`
  drops over-broad/malformed entries — a bare TLD, `.`, ports or paths — so an extra can never widen
  egress) and managed via `lifecycle.add_allow`/`remove_allow` or the TUI Egress screen. Enforcement
  is verifiable end-to-end and observable: `project egress-smoke` is a daemon-gated check that an
  allowlisted host reaches **and** a non-allowlisted host is blocked (`egress.smoke`/`smoke_verdict` —
  a proxy that lets everything, or nothing, through fails), and per-project blocked/allowed
  distinct-destination counts are surfaced from the squid access log via `project egress-log` (denied
  only) and the always-on TUI Network panel (`egress.parse_access`/`summarize_access`, with
  whole-container traffic from `docker/stats.container_net_io`). Recommended for untrusted project
  code — this is the primary control against a compromised dependency exfiltrating the OAuth/`GH`
  token or any env-mount secret, or opening a reverse shell. The agent's strict flags are additive in
  `runner._render_egress`, so the hardened floor is byte-identical to an open project (invariant 2).
  (Deferred defence-in-depth: `dnsmasq` direct-DNS forwarding + an in-container `iptables` default-DROP
  layer — today's lock covers proxy-aware traffic only.)

### Subscription-usage query (read-only, host-side)
- claude-man queries `GET https://api.anthropic.com/api/oauth/usage` host-side, per profile, with that
  profile's stored OAuth token, to show how close each **account** is to its 5-hour and weekly
  subscription limits (`usage_api.py`). The fetch is **read-only and consumes no quota**; it never runs
  inside a container.
- **The token carries a broadened `user:profile` scope.** `claude setup-token` defaults to
  `user:inference` only (which `403`s on the usage endpoint), so `setup_token.py` mints with
  `CLAUDE_CODE_OAUTH_SCOPES="user:profile user:inference"` (`config.OAUTH_USAGE_SCOPES`). `user:profile`
  is a **read-only profile/usage scope** — it adds no billing, write, or admin power; the trade-off is
  that the same token, when present in a container, can also read the account's profile/usage. Existing
  `user:inference`-only tokens keep working for inference but read `re-mint` on the bars until
  re-minted (`claudemanctl profile renew <name>`).
- **The OAuth bearer can't leak via a redirect.** The query uses a no-redirect urllib opener
  (`usage_api._NoRedirect`): urllib's default redirect handler copies `Authorization` onto a redirect
  target without stripping it on a cross-host hop, so a `30x` could exfiltrate the account bearer to an
  attacker host. The opener turns any redirect into an `HTTPError` instead — the token is never sent
  twice. (Default TLS verification is kept; the host is pinned in `config.OAUTH_USAGE_URL`.) The
  `User-Agent` must be `claude-code/<ver>` (`config.CLAUDE_CODE_USER_AGENT`) or the endpoint
  rate-limits hard. Every failure folds into a short note (`re-mint`/`auth`/`offline`/`http NNN`) —
  no token or response detail is surfaced.

### Curated packs (host-side materialization — no new container surfaces)
- Pack delivery **adds no mount, env var, or docker flag**: materialization writes into the
  per-project asset source (`~/.config/claude-man/assets/<slug>/`, config tier, secret-free), and
  the existing asset sync carries it into the already-declared binds with all its guards — the
  claude-side **default-deny allowlist** (only `skills/agents/commands` may land in
  `~/.claude`), denylisted-name filtering, escape/denylist-targeting **symlink refusal**, and
  backup-before-overwrite. The one direct bind write is the **deselection-removal pass** (sync
  merges and never propagates deletions, so a deselected skill would otherwise linger active);
  it is manifest-bounded and backed up first. The hardened floor is byte-identical with or
  without packs (invariant 2).
- A state-tier **manifest** bounds the files packs materialize and remove: deselection removes
  only manifested paths, and an operator file at a pack's target path always wins the collision
  (skipped with a note) — pack materialization cannot clobber operator-authored config. (The
  one pack-managed surface outside the manifest is the fenced `@`-import block inside the
  workspace `CLAUDE.md`; operator content outside the block is never touched.)
- The library is **repo content, public by design** — packs carry house rules and generic
  conventions, never secrets or client-specific material (that stays in the per-project asset
  source). Pack/tier/skill names are slug-validated (they become directory names in the
  container), pack names must be library-unique (lint-tested), and skill trees are copied
  per-file with symlinks refused — a malicious library entry can't path-escape, but the real
  control is that the library ships in this reviewed, versioned repo.
- The drift probe (`pack_states`) is **read-only**; the start-time refresh writes to the
  container side only to remove manifested files the selection no longer wants (backed up
  first). An agent that edits an injected file only triggers curated-wins re-stamping (with a
  backup) — in-container edits cannot propagate into the library or other projects.

### Sync-back safety (defence against exfiltration into host config / the setups git repo)
- The **denylist is enforced before any read** and **re-asserted at git-staging time** — it refuses
  to stage if `.credentials.json`/`oauthAccount`/`userID` ever slipped through.
- Diffs are **secret-masked** (token/key/secret/password/authorization/Bearer + MCP env/args) before
  display.
- `settings.json` is **field-patched** — host hook wiring and `statusLine` are structurally immune,
  never clobbered. MCP changes go through `claude mcp add/remove --scope`, not wholesale file copy.
- Every host target is **backed up before** merge. **Deletions and three-way conflicts default to
  reject**, in a separate confirmed class. Nothing is written without explicit `enter`.

## Residual risks (accepted, with mitigations)

1. **Long-lived plaintext tokens that can't self-refresh.** An org admin can revoke a work seat,
   401-ing every project on that profile. → Store `0600`, never in an image layer or git
   (`.gitignore` blocks it); surface token age/expiry; treat a work-container 401 as
   `profile renew`, not a bug.
2. **The token is readable from inside its own container** (`docker inspect`, process env). This is
   inherent to env-var injection and the same caveat Anthropic flags for sandboxed agents. →
   Per-project isolation + strict egress (no arbitrary exfil path) is the real mitigation; recommend
   strict egress for untrusted code. The token also carries the `user:profile` scope (for usage bars),
   so a token read from inside its container can additionally read that account's profile/usage — but
   it grants no billing/write power beyond the inference the token already authorises, and the same
   isolation + strict-egress mitigation applies.
3. **The hardened profile is stricter than Anthropic's reference devcontainer**; an undocumented
   write path may only surface at runtime (`EROFS`/`getpwuid`). → `image smoke` gate; add writable
   mounts reactively; re-verify per claude version bump.
4. **Disabled auto-update means a stale baked claude won't self-heal.** → claude-man owns version
   bumps by rebuild + recreate; running version is a status column; resolver/onboarding behaviour is
   re-verified on each bump (pinned per image).
5. **Two-store coherence (TOML vs labels).** A manual `docker run` or partial delete can desync. →
   Registry always wins; reconcile by recreate; delete is an idempotent transaction.

## Reporting

This is a personal tool; there is no formal disclosure process. If a control here is wrong or
bypassable, fix it and note the change against the relevant invariant in `CLAUDE.md`.
