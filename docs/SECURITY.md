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
| host → container | the **one** profile's OAuth token (env), project env vars, the checked-out repos | `.credentials.json`, the `gh` PAT, `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`, other profiles' tokens |
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

### Credential isolation
- **No `.credentials.json` ever enters a container.** Auth is a long-lived token minted by
  `claude setup-token`, stored `0600` (dir `0700`) under `XDG_STATE`, injected per-launch as
  `CLAUDE_CODE_OAUTH_TOKEN`. `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` are scrubbed from the
  rendered env so they can't silently outrank the OAuth token or bill the wrong account.
- Each profile's token dir is isolated; work and home tokens never share a file. A switch-time
  email-mismatch guard refuses to cross identities into an existing config dir.
- The **`gh` PAT stays on the host** — repos are cloned host-side; the container only sees the
  working tree.

### Network containment
- Open egress by default; per-project **strict** mode routes all egress through a squid sidecar on
  an `internal: true` network (no direct route → the proxy can't be bypassed). Recommended for
  untrusted project code. The allowlist always includes `claude.ai` (token refresh).

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
   strict egress for untrusted code.
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
