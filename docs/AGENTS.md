# Multi-agent provider abstraction (Phase 7 design)

Status: **planned / not started.** This is a design — no code exists yet. Tracking:
[`ROADMAP.md`](../ROADMAP.md) Phase 7. The goal is a seam that lets claude-man run a *different*
coding agent (e.g. the OpenAI Codex CLI) inside the same hardened-container model, without forking
the project and without weakening any security invariant.

## Concept

Today claude-man is wired to one agent — Claude Code — in nine areas (auth, image, process spawn,
updates, usage, paths, sync-back, packs, naming). The encouraging part, confirmed by a coupling
audit, is that **the security floor is already agent-agnostic**: the hardened `docker create` argv
(`docker/runner.py::_HARDENING` / `build_create_argv`), the strict-egress squid sidecar
(`network/egress.py` + `runner._render_egress`), the labels (`docker/labels.py`), published ports,
and the env-mount / secret-passthrough machinery are generic Linux-container plumbing — none of it
knows what binary runs inside. The Claude-specific assumptions cluster in eight well-defined places.

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
| 4 | **updates** | HARD | `updates.py` (`RELEASES_BASE_URL`), `lifecycle.check_update`/`resolve_build_version` | the release-pointer URL, channel names, and UA — the semver parse/compare + rebuild-before-start orchestration are already provider-neutral and reused verbatim |
| 5 | **usage** | HARD | `usage.py` (transcript JSONL schema) | the transcript location + schema — or `usage=None` to drop the feature |
| 6 | **paths/config** | HARD | `config.CONTAINER_CLAUDE_CONFIG`/`CLAUDE_CONFIG_DIR`, `runner._BAKED_ENV`, `profiles/seed.py`, `registry/schema.py` (`_MANAGED_MOUNTS`) | the in-container config-dir path + its env-var name, the host state-dir naming, and the identity-seed file name + shape. **The managed-mount-dst denylist becomes keyed on `provider.config_dir`** so the anti-smuggling guard protects the new agent's cred file too |
| 7 | **sync-back** | HARD | `syncback/{denylist,artifacts,baseline,merge}.py` | the denylist paths/keys, the syncable-artifact set + host targets, the settings-file name + structurally-immune keys, the narrow identity-file reader, and the MCP/config apply strategy. **The 3-way merge engine, masking, backup-first, flock, audit-commit are all reused** — only the policy *data* varies |
| 8 | **packs/assets** | SOFT | `packs/materialize.py` (marker block, `@`-import), `assets.py` (`_CLAUDE_SAFE_ENTRIES`), `library/packs/` | the context-file name (`CLAUDE.md` vs `AGENTS.md`), its import syntax (`@path` vs include vs inline concat), and the safe-config-entries allowlist. Marker-block patching, manifest, operator-wins-collision logic are reused; library *content* is agent-flavoured |
| 9 | **naming/egress-base** | SOFT | `config.py` brand prefixes, `docker/labels.py`, `network/allowlist.py` (`BASE_ALLOWLIST`) | only the version-label key + the **required egress hosts**. The `claude-man` brand prefix stays product-wide. `BASE_ALLOWLIST` splits into a neutral toolchain set (npm/pypi/apt/github) + `provider.required_hosts`, so a locked container for *any* agent always includes its own auth-refresh path (invariant 3) |

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
  Key `_MANAGED_MOUNTS` on `provider.config_dir`. Generalize the one-per-container guard's comm.
- **7c — A `codex` provider + image overlay**, validated against the hardened floor
  (`image smoke`). Resolve the auth-kind difference (below). `project create --agent codex`.
- **7d — Codex sync-back policy + pack content** (`AGENTS.md` vs `CLAUDE.md`; its own config
  taxonomy + library content).

## Hard problems to resolve before 7c

1. **Auth model divergence.** The whole profile model assumes `claude setup-token` → a single,
   non-refreshable bearer in one env var (`CLAUDE_CODE_OAUTH_TOKEN`), with the file mtime as token
   age. Codex likely uses a refreshable JSON credential. So `AuthSpec` needs an **auth-kind** enum,
   not just a renamed var — and invariant 1 must still hold (never inject a key that mis-bills, never
   copy a working credentials file into the container). *Needs research on the Codex CLI's actual
   auth/login flow before 7c.*
2. **Sync-back is the deepest coupling.** The engine is reusable; only the policy data (which files
   and JSON keys in the config dir are secret/machine-local) and the MCP-apply strategy are
   agent-specific — `SyncbackPolicy` isolates exactly that. Getting a Codex denylist wrong is a
   credential-leak risk, so it gets the same adversarial review the Claude denylist did.
3. **Usage is optional.** Codex's transcript format differs; `usage=None` drops the feature cleanly.
   (The only usage surface is the transcript token-totals — the per-account subscription-usage bars
   were removed — so this seam is low-stakes.)
4. **Pack library content is Claude-flavoured** (skills/, `CLAUDE.md` fragments). Codex needs its own
   content or a translation layer; the materializer machinery itself carries over unchanged.

## Non-goals

Not a plugin/extension API for arbitrary binaries — this is a curated set of *coding-agent*
providers claude-man ships and security-reviews. Not a per-provider relaxation of the hardened
floor or the auth invariants — those are enforced by the layer, identically, for every provider.
