# Multi-agent provider abstraction (Phase 7 design)

Status: **7a-1 landed 2026-09-26** (branch `feat/7a-agent-provider-seam`): `src/claudeman/agents/`
exists — `base.py` (the `AgentProvider` + `AuthSpec`/`ImageSpec`/`UpdateSpec` value objects below,
validated), `claude.py` (the reference provider), `resolve()`. Routed through it, byte-identical
(`tests/test_agents.py` pins pre-seam goldens of the create argv, build argv, probe argv, base
allowlist, image label and exec strings): seams 2 (build-arg + label key), 3 (binary + comm), 4
(release URL/UA/channels), 6 (config dir + env pointer; the mount-dst denylist and the tool
reserved-env now cover EVERY provider's config dir), 9 (`required_hosts` + the neutral `TOOLCHAIN`
split — `allowlist.base_allowlist(provider)`), and the token env / scrub set of seam 1 (runner +
the host verify). Still to route: seam 7 (sync-back policy data), 8 (context file + packs), 5
(usage), the smoke probes, the host mint flow (7-auth), and the headless `RunSpec` (7-run). The
interface below is the design; the shipped sub-specs are the subset 7a-1 needed (the others land
with their seams). **7b landed the same day:** `Project.agent` / `Profile.agent` (schema-validated
against the registry, terse-default TOML), `Project.provider`, the `claude-man.agent` label, the
AGENT column in both surfaces, `project create --agent` + the create modal's Agent select, the
`agent_mismatch` profile guard, and the provider threaded through lifecycle → runner / images /
updates / egress (squid allowlist) / terminals (`provider_for`). Tracking:
[`ROADMAP.md`](../ROADMAP.md) Phase 7. **Amended 2026-09-26 by [`V2-PLAN.md`](V2-PLAN.md) §4–5:** both auth
modes per provider (`AuthSpec` as data, provider-scoped profiles, a per-provider `forbidden_env` scrub),
a new **headless-run seam** (`RunSpec` → normalised `AgentEvent` stream; `project run`), a third
provider, and the PR breakdown. Phase 7 is the first stage of the v2 (agentry) line. The goal is a seam that lets claude-man run a *different*
coding agent (e.g. the OpenAI Codex CLI) inside the same hardened-container model, without forking
the project and without weakening any security invariant.

## Concept

Today claude-man is wired to one agent — Claude Code — in nine areas (auth, image, process spawn,
updates, usage, paths, sync-back, packs, naming). The encouraging part, confirmed by a coupling
audit, is that **the security floor is already agent-agnostic**: the hardened `docker create` argv
(`docker/runner.py::_HARDENING` / `build_create_argv`), the strict-egress squid sidecar
(`network/egress.py` + `runner._render_egress`), the labels (`docker/labels.py`), published ports,
and the env-mount / secret-passthrough machinery are generic Linux-container plumbing — none of it
knows what binary runs inside. The Claude-specific assumptions cluster in nine well-defined places.

The fix follows the existing `hostplatform.py` discipline ("all platform branches go through here"):
introduce an **`agents/` package** with a single `AgentProvider` value object that owns every
agent-specific decision, resolved through one module so nothing else hard-codes `"claude"`.

**Load-bearing principle:** a provider parameterizes *behaviour*, never *security*. Invariants 1–3
are enforced **by** the abstraction for every provider — they are not per-provider knobs. A new
agent can change *which* config dir is bound, but not *that* `.credentials.json` is never copied,
`ANTHROPIC_*`/the misbilling keys are never injected, the hardened floor stays byte-identical, and a
locked container has no route out except its allowlist proxy.

## The interface

```python
# agents/base.py  (pure stdlib — importable by CLI/lifecycle without textual, like the rest of core)
@dataclass(frozen=True)
class AgentProvider:
    id: str                          # "claude" | "codex"
    display_name: str

    # --- in-container identity (process-spawn seam) ---
    binary: str                      # the CLI program name to exec ("claude" / "codex")
    proc_comm: str                   # /proc comm to probe for one-per-container (MAY differ from binary)

    # --- auth (auth/token seam) ---
    auth: AuthSpec                   # token env var(s), mint cmd, login cmd, identity probe, auth-KIND

    # --- image (image-bake seam) ---
    image: ImageSpec                 # install Dockerfile fragment + version build-arg + version-label key

    # --- config dir + identity seed (paths seam) ---
    config_dir: str                  # in-container config path ("/home/agent/.claude")
    config_dir_env: str              # env var that points the agent at it ("CLAUDE_CONFIG_DIR")
    identity_seed: SeedSpec          # onboarding-suppression file name + shape

    # --- optional features (None => feature absent for this provider) ---
    updates: UpdateSpec | None       # release-pointer URL + channels + UA
    usage: UsageSpec | None          # transcript location/schema + subscription-usage endpoint

    # --- sync-back policy (sync-back seam) ---
    syncback: SyncbackPolicy         # denylist paths/keys, syncable set, settings file + immune keys, MCP apply

    # --- context files / packs (packs seam) ---
    context: ContextSpec             # context-file name ("CLAUDE.md"), import syntax ("@path"), safe-config entries

    # --- egress (naming/egress-base seam) ---
    required_hosts: tuple[str, ...]  # auth-refresh + inference + release domains a LOCKED container must allow
```

The sub-specs (`AuthSpec`, `ImageSpec`, …) are small frozen dataclasses holding the policy data each
seam needs. Provider selection is a new explicit `Project.agent` field (default `"claude"`), resolved
once (`agents.resolve(project)`), mirroring how the profile/overlay are resolved today.

## Seam map (what each provider owns)

| # | Seam | Coupling | Files today | What the provider owns |
|---|------|----------|-------------|------------------------|
| 1 | **auth/token** | HARD | `profiles/setup_token.py`, `config.OAUTH_TOKEN_ENV`, `profiles/identity.py`, `registry/schema.py` (`Profile`) | mint command + scopes, auth env-var name(s), login/SSO command, account-identity probe (`status` cmd + email extractor), and an **auth-kind** (single bearer vs refreshable JSON cred) |
| 2 | **image-bake** | HARD | `images/base/Dockerfile`, `docker/images.py` (`image_claude_version`, `build_argv`), `docker/labels.py` (`IMAGE_VERSION`), `config.DEFAULT_CLAUDE_VERSION` | the agent-install Dockerfile fragment, the version build-arg name, and the version-label key (the hardened base + non-root passwd + neovim + node/gh stay shared) |
| 3 | **process-spawn** | HARD | `tui/terminals.py` (`spawn_claude`, the `/proc`-comm probe, `build_claude_probe_argv`) | the in-container exec program **and** the `/proc` comm to probe (they can differ); the one-per-container invariant generalizes to one-*agent*-per-container |
| 4 | **updates** | HARD | `updates.py` (`config.RELEASES_BASE_URL`), `lifecycle.check_update`/`resolve_build_version` | the release-pointer URL, channel names, and UA — the semver parse/compare + rebuild-before-start orchestration are already provider-neutral and reused verbatim |
| 5 | **usage** | HARD | `usage.py` (transcript JSONL schema) | the transcript location + schema — or `usage=None` to drop the feature |
| 6 | **paths/config** | HARD | `config.CONTAINER_CLAUDE_CONFIG`/`CLAUDE_CONFIG_DIR`, `runner._BAKED_ENV`, `profiles/seed.py`, `registry/schema.py` (`_MOUNT_FORBIDDEN_DST_EXACT`/`_MOUNT_FORBIDDEN_DST_PREFIXES`) | the in-container config-dir path + its env-var name, the host state-dir naming, and the identity-seed file name + shape. **The managed-mount-dst denylist becomes keyed on `provider.config_dir`** so the anti-smuggling guard protects the new agent's cred file too |
| 7 | **sync-back** | HARD | `syncback/{denylist,artifacts,baseline,merge}.py` | the denylist paths/keys, the syncable-artifact set + host targets, the settings-file name + structurally-immune keys, the narrow identity-file reader, and the MCP/config apply strategy. **The 3-way merge engine, masking, backup-first, flock, audit-commit are all reused** — only the policy *data* varies |
| 8 | **packs/assets** | SOFT | `packs/materialize.py` (marker block, `@`-import), `assets.py` (`_CLAUDE_SAFE_ENTRIES`), `library/packs/` | the context-file name (`CLAUDE.md` vs `AGENTS.md`), its import syntax (`@path` vs include vs inline concat), and the safe-config-entries allowlist. Marker-block patching, manifest, operator-wins-collision logic are reused; library *content* is agent-flavoured |
| 9 | **naming/egress-base** | SOFT | `config.py` brand prefixes, `docker/labels.py`, `network/allowlist.py` (`BASE_ALLOWLIST`) | only the version-label key + the **required egress hosts**. The `claude-man` brand prefix stays product-wide. `BASE_ALLOWLIST` splits into a neutral toolchain set (npm/pypi/apt/github) + `provider.required_hosts`, so a locked container for *any* agent always includes its own auth-refresh path (invariant 3) |

> **Post-Phase-9 addendum:** the hybrid local-model gateway added a further Claude-coupled seam
> after this map was drawn — the `claude-* → anthropic/claude-*` route and the
> `claude-local-<model>` prefix in `network/gateway.py` (+ `config.GATEWAY_LOCAL_PREFIX`), plus the
> agent-env wiring (`ANTHROPIC_BASE_URL` + `ANTHROPIC_CUSTOM_MODEL_OPTION`) in `docker/runner.py`.
> A future non-Claude provider needs a gateway/model seam too.

## Phasing

Each phase ships green (tests + `image smoke`); the order keeps risk front-loaded into a pure
refactor before any second-agent code exists.

- **7a — Extract the seam (pure refactor, zero behaviour change).** Introduce `agents/` + a `claude`
  provider that reproduces today's behaviour byte-for-byte. Route the soft seams through it: spawn
  binary/comm, config-dir path + env, version-label key + build-arg, release URL/UA, required egress
  hosts, context-file name + import syntax, and the sync-back policy *data*. All existing tests stay
  green; a unit test pins that the rendered hardened argv is byte-identical (invariant 2). This is
  the high-value, low-risk precondition — do it first.
- **7b — Thread the provider through.** Add `Project.agent` (default `"claude"`); pass the resolved
  provider through `lifecycle`/`runner`/`terminals`/`images`. Split `BASE_ALLOWLIST` per the table.
  Key the mount-dst denylist (`_MOUNT_FORBIDDEN_DST_*`) on `provider.config_dir`. Generalize the
  one-per-container guard's comm.
- **7c — A `codex` provider + image overlay**, validated against the hardened floor
  (`image smoke`). Resolve the auth-kind difference (below). `project create --agent codex`.
- **7d — Codex sync-back policy + pack content** (`AGENTS.md` vs `CLAUDE.md`; its own config
  taxonomy + library content).

## Hard problems to resolve before 7c

1. **Auth model divergence — RESOLVED by the 2026-09-26 spike (below).** Codex has both shapes:
   an API key (`OPENAI_API_KEY` / `CODEX_API_KEY` env = our `token` mode) and a ChatGPT login that
   mints a refreshable JSON credential (`$CODEX_HOME/auth.json` = our `login` mode, the same
   in-container-minted doctrine as claude's `/login`). No new auth-kind enum is needed: `Project.auth`
   `token`|`login` already models it; `AuthSpec` carries the env name(s) + the credential file name.
2. **Sync-back is the deepest coupling.** The engine is reusable; only the policy data (which files
   and JSON keys in the config dir are secret/machine-local) and the MCP-apply strategy are
   agent-specific — `SyncbackPolicy` isolates exactly that. Getting a Codex denylist wrong is a
   credential-leak risk, so it gets the same adversarial review the Claude denylist did.
3. **Usage is optional.** Codex's transcript format differs; `usage=None` drops the feature cleanly.
   (The only usage surface is the transcript token-totals — the per-account subscription-usage bars
   were removed — so this seam is low-stakes.)
4. **Pack library content is Claude-flavoured** (skills/, `CLAUDE.md` fragments). Codex needs its own
   content or a translation layer; the materializer machinery itself carries over unchanged.

## Codex — verified by the login-mode spike (2026-09-26, codex-cli 0.157.1)

Run by hand in a container created with the EXACT hardened floor (`build_create_argv`'s flags:
read-only, cap-drop ALL, no-new-privileges, uid 1000, pids 1024, the two tmpfs, 16g cap) plus a
`codex-config` bind at `/home/agent/.codex` and `CODEX_HOME` pointing at it. Findings, each one a
design input for 7c:

| Question | Verified answer |
|---|---|
| Install artefact | The **`codex-package-<arch>-unknown-linux-musl.tar.gz`** tarball (147 MB, static musl, the only asset with a `codex-package_SHA256SUMS` entry + a sigstore bundle), NOT the single `codex-<arch>` binary. Layout (`codex-package.json`): `bin/codex` + `bin/codex-code-mode-host`, `codex-resources/{bwrap,zsh,voice}`, `codex-path/rg`. Installed whole to `/opt/codex` with `bin/*` symlinked into `/usr/local/bin` — exactly the tool registry's `install = "bundle"` shape (the AWS CLI v2 precedent), so the provider's image fragment IS a registry-style entry |
| Why the whole package | Codex 0.157's shell tool runs through a **code-mode host** (`codex-code-mode-host`, looked up next to the `codex` executable). With the single binary every command fails `Code Mode is unavailable … host executable was not found` and the model answers from imagination. With the package tree `command_execution` items run as uid 1000 with `aggregated_output` + `exit_code` |
| Codex's own sandbox | Bundled **bubblewrap**; under `--cap-drop ALL` it fails `No permissions to create a new namespace` (docker's seccomp blocks user namespaces). Our container IS the sandbox, so the provider seeds **`sandbox_mode = "danger-full-access"`** into `$CODEX_HOME/config.toml` (verified: a plain `codex exec` then runs commands with no flag) and the headless `RunSpec` also passes `--sandbox danger-full-access`. Never a floor relaxation |
| Login mode | `codex login --device-auth` prints a URL + one-time code (15-min window, polls; needs device-code auth enabled in the ChatGPT account's security settings) and, on success, mints **`auth.json` `0600`** in the bind: `{auth_mode: "chatgpt", last_refresh, tokens: {access_token, refresh_token, id_token, account_id}, OPENAI_API_KEY: null}`. Self-refreshing (`last_refresh`). **Survived a container recreate** (it lives in the bind). `codex login status` → `Logged in using ChatGPT`. `cli_auth_credentials_store = "file"` seeded so it never tries a keyring |
| Token mode | `OPENAI_API_KEY` env (API-billed) — also `printenv OPENAI_API_KEY \| codex login --with-api-key` writes it INTO `auth.json`; `codex exec` additionally honours `CODEX_API_KEY`. Both names go in the provider's scrub set (invariant 9) and only the chosen profile's key is injected pass-through |
| Headless run | `codex exec --json [--ephemeral] [--skip-git-repo-check] "<prompt>"` → JSONL: `thread.started{thread_id}` → `turn.started` → `item.started/completed{item:{type: agent_message\|command_execution\|error, …}}` → `turn.completed{usage}` \| `turn.failed{error}`. Final message alone on stdout without `--json`. Refuses outside a git repo unless `--skip-git-repo-check` (a repo under /workspace passes). Fixtures: `tests/fixtures/codex/` |
| Config dir contents (sync-back denylist input) | `auth.json` (SECRET), `config.toml`, `installation_id`, `models_cache.json`, `*.sqlite` + `-shm`/`-wal` (goals/logs/memories/queue/state — machine-local state, some of it conversation content), `log/`, `tmp/`, `.tmp/`, `cache/`, `plugins/`, `shell_snapshots/`, `.sandbox_migration`, `skills/` (the one syncable artefact tree, like claude's) |
| Required egress hosts | `auth.openai.com` (device auth + refresh), `api.openai.com` (`/v1/responses`), `chatgpt.com` — to confirm under lock at 7c via `egress-log` |
| Subscription lane | The exec runs billed to the ChatGPT plan (`auth_mode: chatgpt`, no API key) — the same posture as claude's login mode |

Interactive (tty) use and `codex logout`'s exact file removal are not yet exercised — both are 7c
smoke items. The spike's bind (with the minted credential) is in the session scratchpad only.

## Non-goals

Not a plugin/extension API for arbitrary binaries — this is a curated set of *coding-agent*
providers claude-man ships and security-reviews. Not a per-provider relaxation of the hardened
floor or the auth invariants — those are enforced by the layer, identically, for every provider.
