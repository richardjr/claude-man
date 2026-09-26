# agentry — the v2 upgrade plan (multi-agent sandboxes, a control plane, manager agents, a web UI)

Status: **planning, 2026-09-26.** claude-man does what it set out to do. This document plans the
next major line of work: run *any* coding agent (Codex first) in the same hardened-container model,
then put a **manager tier** above the sandboxes — agents that run other agents against tasks and
talk to each other on per-task message threads — driven from a **web UI**, hosted eventually on a
remote machine with login. The work runs over many PRs. **Phase 7 (providers) ships first**; the
control-plane / task / manager phases follow and are refined as we go (the operator has a backlog
of ideas for the task layer that gets folded in after Phase 7).

Companion docs: [`ROADMAP.md`](../ROADMAP.md) (phase checklists), [`AGENTS.md`](AGENTS.md) (the
provider seam design, amended by this plan), [`SECURITY.md`](SECURITY.md) (the invariants this plan
extends, never relaxes).

---

## 1. Decisions taken (2026-09-26)

| Decision | Choice |
|---|---|
| Product name | **agentry** (see §2 for the collision check) |
| Order | **Phase 7 first** (providers, Codex), then the agent/manager work |
| Per-provider auth | **Both** methods supported for every provider; chosen per project at create (`token` \| `login`, the existing `Project.auth` generalised) |
| Web stack | **FastAPI + WebSockets** backend (Python, reuses `lifecycle`), **React + Vite + Tailwind** frontend |
| Access model | Localhost first; designed for a **hosted machine with login from anywhere** |
| Manager agents | Run in **hardened containers** like every other agent; **provider-agnostic** |
| Agent-to-agent comms | A **message board**: email-like threads attached to tasks |
| TUI | **Frozen**, kept working (people use it, the operator included) until the agent stack is solid |
| Repo shape | **One repo**, many PRs |
| Jev | **Not a coding agent** — a decision model; becomes the manager tier's decision backend (§4) |

## 2. Naming: `agentry`

"Agentry" = the office or work of an agent. Collision check (2026-09-26):

| Where | State | Impact |
|---|---|---|
| github.com/richardjr/agentry | **free** | rename the repo (GitHub redirects the old URL) |
| crates.io `agentry` | free | none |
| PyPI `agentry` | taken — a 0.0.1 placeholder "A library for creating AI agents" | the wheel is not published today; if it ever is, publish as `agentry-cli` |
| npm `agentry` | taken — 0.1.0 "compose AI agents like React components" | none (the web UI is not an npm package) |
| github.com/amtp-protocol/agentry | 19★, active Sept 2026, "orchestration and memory for multi-agent systems" | closest neighbour in the same space; different product, no trademark. Accepted |
| github.com/marcodenic/agentry | Go, 2★, dormant since Dec 2025 | none |
| agentry.io | an AI virtual-receptionist business | none; do not buy that domain |

**Names in code** (applied in the rename phase, §6 Phase 11):

| Today | After |
|---|---|
| `claude-man` (repo, `pyproject` name, `APP_NAME`) | `agentry` |
| `claudeman` (python package, TUI launcher) | `agentry` |
| `claudemanctl` | `agentryctl` |
| — | `agentryd` (the control-plane daemon, Phase 12) |
| — | `agentry-web` (the React UI, served by `agentryd`) |
| `claude-man.<key>` docker labels | `agentry.<key>` (a read-side shim recognises the old prefix for one release) |
| `claude-man:<overlay>` images, `claude-man-<slug>` containers, `claude-man-proxy-`/`-gw-`/`-net-` | `agentry:<overlay>`, `agentry-<slug>`, `agentry-proxy-`/… |
| `~/.config/claude-man`, `~/.local/state/claude-man`, `CLAUDE_MAN_*` env | `~/.config/agentry`, `~/.local/state/agentry`, `AGENTRY_*` (one-shot `agentryctl migrate`) |
| `CLAUDE_MAN_PROJECT[_TINT]`, `CLAUDE_MAN_BAR_*` in-container env | `AGENTRY_PROJECT…` (image + runner + bash/tmux files move together) |

The brand appears ~1,300 times across `src`/`tests`/`images`/`docs`; it is a mechanical but
whole-repo change and needs the label/state shims so running projects survive it. It is deliberately
scheduled AFTER Phase 7 so the provider refactor (already a large diff) does not land on top of a
rename, and so the daemon and web UI are born under the new name.

## 3. Layers

```
                 ┌──────────────────────────────────────────────────────────────┐
   operator ───► │  agentry-web  (React/Vite/Tailwind; WebSocket streams;       │
   (browser,     │  xterm.js terminals; approvals; login when hosted)           │
    CLI, TUI)    └───────────────▲──────────────────────────────────────────────┘
                                 │ HTTP/WS (unix socket locally; TLS + login when hosted)
                 ┌───────────────┴──────────────────────────────────────────────┐
                 │  agentryd — control plane (FastAPI)                          │
                 │  · projects/containers (wraps lifecycle.py — the SAME code   │
                 │    the CLI/TUI call in-process today; registry flock shared) │
                 │  · tasks + runs (headless agent sessions, worktrees)         │
                 │  · threads (the message board) + event log (sqlite, state)   │
                 │  · decisions (Jev / LLM fallback: route, score, gate)        │
                 │  · the agent-facing MCP server (per-caller policy)           │
                 └───▲───────────────────────────▲──────────────────────────────┘
                     │ MCP (tools, policy-scoped)  │ docker exec (headless run seam)
        ┌────────────┴───────────┐     ┌──────────┴─────────────────────────────┐
        │ manager agent(s)       │     │ worker agents                          │
        │ hardened container,    │     │ hardened containers (today's model),   │
        │ any provider, NO docker│     │ any provider, one agent per container, │
        │ socket — API only      │     │ a task runs on a git worktree          │
        └────────────────────────┘     └────────────────────────────────────────┘
```

Two properties keep the TUI freeze cheap: the daemon is **additive** (it imports `lifecycle` exactly
as the CLI does; the per-slug registry `flock` already serialises concurrent callers), so the CLI and
TUI keep working unchanged as direct callers while the web UI and the agents go through `agentryd`.
And the **security floor is agent-agnostic** (the `docs/AGENTS.md` coupling audit), so nothing in
the manager tier needs a new mount, capability, or socket inside any container.

### New invariants (to be added to CLAUDE.md when the phases land)

7. **No agent ever holds the docker socket or the registry.** Manager and worker agents act only
   through the control-plane API/MCP, which authenticates the caller (operator / manager / worker)
   and enforces policy per caller: a worker can read and post on its own task's thread; a manager can
   create tasks, start runs, read results, and post; only the operator (web/CLI/TUI) can change a
   project's security posture (egress, auth mode, mounts, ports, tools, profiles).
8. **Everything agents say and do is persisted and attributable.** Every thread message and run
   event carries its author (agent id, task, container) and lands in the state-tier event log. There
   are no private agent channels; the operator can read all of it from the web UI.
9. **Provider credentials never cross providers.** The layer injects exactly ONE credential — the
   chosen profile's, for the chosen provider, in the chosen auth mode — and scrubs EVERY provider's
   credential env names from operator-supplied env (`env`, `env_file`, env-mounts). Invariant 1's
   `ANTHROPIC_*` scrub becomes the Claude row of a per-provider table (§4).

## 4. Phase 7 as amended: providers, dual auth, the headless seam, and Jev

`docs/AGENTS.md` stands; this section amends it with what the 2026-09 decisions add.

### Auth: both modes for every provider

`Project.auth` (`token` | `login`) already models the two shapes; it generalises per provider.
Profiles become **provider-scoped** (`Profile.agent`, default `claude`; a project's profile must match
its agent — the existing account-mismatch guard extends to agent-mismatch).

| Provider | `token` mode (long-lived credential held `0600` in the profile, injected as env at launch) | `login` mode (NO env; the agent mints a self-refreshing credential in its own config bind, in-container) | Credential file (denylisted from sync-back + mount-dst) | Context file |
|---|---|---|---|---|
| **claude** (today) | `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` | `/login` code-paste → `~/.claude/.credentials.json` | `.credentials.json` | `CLAUDE.md` (`@path` imports) |
| **codex** ✅ *spike-verified 2026-09-26* | an OpenAI API key → `OPENAI_API_KEY` (billed at API rates; `CODEX_API_KEY` also honoured by `codex exec`) | `codex login --device-auth` (device-code flow, headless-friendly; the ChatGPT plan pays) → `$CODEX_HOME/auth.json` `0600`, auto-refreshed, survives recreate; `cli_auth_credentials_store = "file"` seeded since there is no keyring in-container | `auth.json` | `AGENTS.md` |
| **gemini** (candidate third) | `GEMINI_API_KEY` | Google OAuth device/URL flow → `~/.gemini/oauth_creds.json` | `oauth_creds.json` | `GEMINI.md` |
| **opencode** (candidate third) | provider keys via `opencode auth login` → `auth.json` | Anthropic/OpenAI OAuth logins stored in the same `auth.json` | `auth.json` | `AGENTS.md` |

Invariant 1 is unchanged in doctrine: never copy a HOST credential file in (Codex's own docs suggest
`docker cp auth.json` — we refuse that exactly as we refuse it for Claude), never inject a key that
bills a different account. What becomes provider *data*: the token env name(s), the login command,
the credential file name, the identity probe, and the `forbidden_env` scrub list. The
`schema.EnvMount` dst-denylist is keyed on `provider.config_dir` so the anti-smuggling guard covers
`auth.json` too.

**Codex risks — RESOLVED by the 2026-09-26 spike (`docs/AGENTS.md` § Codex):** its bundled bubblewrap
sandbox cannot create namespaces under `--cap-drop ALL`, so the provider seeds `sandbox_mode =
"danger-full-access"` into the config bind (our container IS the sandbox) and the `RunSpec` passes the
matching flag — never a floor change. The install artefact must be the full `codex-package` tarball
(the shell tool needs the sibling `codex-code-mode-host` binary), installed as a registry-style
`bundle` under `/opt/codex`. Login mode, the headless JSONL stream and credential survival across
recreate are all verified; fixtures in `tests/fixtures/codex/`.

### The headless-run seam (new — the manager tier's primitive)

Every provider gains a `RunSpec`: how to run ONE non-interactive session inside the container and
turn its output into a normalised event stream.

| Provider | Headless command | Stream | Resume |
|---|---|---|---|
| claude | `claude -p --output-format stream-json --verbose [--permission-mode …] [--output-format json + schema]` | JSONL (`system`/`assistant`/`user`/`result` + usage) | `--resume <id>` |
| codex | `codex exec --json [--output-schema f] [--skip-git-repo-check]` | JSONL (`thread.started`, `turn.started`, `item.completed`, `turn.failed`) | `codex exec resume <id>` |
| gemini | `gemini -p … --output-format stream-json` | JSONL (`init`/`message`/`tool_use`/`tool_result`/`result`); exit 53 = turn limit | session flags |
| opencode | `opencode run --format json` / `opencode serve` HTTP / `opencode acp` | JSON events / ACP nd-JSON | `--session/--continue` |

Normalised `AgentEvent`: `started(session_id)`, `message(role, text)`, `tool_use`, `tool_result`,
`turn_done(usage)`, `failed(reason)`, plus a provider-neutral exit-code map. **Evaluate ACP (the Agent
Client Protocol, nd-JSON over stdio — OpenCode ships it, Gemini CLI has it, Claude Code and Codex have
adapters)** as the single transport before writing four JSONL parsers; fall back to per-provider JSONL
where ACP is missing or lags. The seam ships in Phase 7 as a CLI verb — `agentryctl project run <slug>
"<prompt>"` streams a one-shot headless session in the project's container — so it is exercised and
tested long before tasks exist. The one-agent-per-container guard applies to headless runs too.

### Jev: a decision backend, not a provider

Jev (TypeSafe AI, out of stealth 2026-09-15) takes a *state* and typed *questions* and returns
`choice` (with per-option probabilities), `score` (against a rubric), or `noul` (0–1 truth). It writes
no text, so it cannot drive a sandbox. It is the right tool for the judgement calls the manager tier
makes constantly and cheaply (~100 ms, $0.042/M input tokens, output free):

- **routing** — which provider/model/worker should take this task; does this message need the operator;
- **scoring** — rate a worker's result/PR against a task rubric; rank candidate diffs;
- **gating** — is this run's tool call within policy; is this thread waiting on a human;
- **triage** — classify incoming thread messages and repo state.

Design: a `decisions/` package with a Protocol shaped like `models/base.py` — `DecisionBackend`
with `choose/score/check` — Jev as the first backend (`typesafe-sdk`, `TYPESAFE_API_KEY` held `0600`
in the state tier, used by `agentryd` ONLY, never injected into worker containers), an LLM-judge
backend as fallback, and a fake for the dependency-free tests. Lands with the manager tier (Phase 16),
not Phase 7.

## 5. Phase 7 PR breakdown (the first stage)

Each PR ships green: unit tests, ruff, `image smoke`; the hardened argv stays byte-identical
(unit-pinned) through every one of them. TUI: frozen, but the provider IS a core project field, so
the create modal gets an Agent select and the projects table an AGENT column (minimal parity, same
PR as the CLI verb — the CLI-first-then-TUI convention still holds for core fields).

| PR | Scope | Notes |
|---|---|---|
| 7a-1 | `agents/` package: `base.py` (`AgentProvider` + sub-specs), `claude.py`, `resolve()`; route spawn binary/comm, config-dir path+env, version label/build-arg, release URL/UA, required egress hosts through it | pure refactor, zero behaviour change; the argv pin test |
| 7a-2 | Sync-back policy data (`SyncbackPolicy`) + context spec (`CLAUDE.md`, `@`-import) + packs safe-entries through the provider | pure refactor |
| 7b | `Project.agent` + `Profile.agent` (schema, TOML, `set_agent`), `agentry.agent` label, status AGENT column, `project create --agent`, `BASE_ALLOWLIST` split (neutral toolchain + `provider.required_hosts`), mount-dst denylist keyed on `provider.config_dir`, comm probe generalised, agent/profile mismatch guard | TUI: Agent select in create + column |
| 7-auth | `AuthSpec` with both modes as data; `forbidden_env` per provider (invariant 9); `profile add --agent codex` mint path (API-key paste, hidden, `0600`); `login` mode's identity verify/backfill made provider-generic | |
| 7-run | The headless seam: `RunSpec` + `AgentEvent` normalisation for claude; `project run <slug> "<prompt>"` (streams; `--json` raw); ACP evaluation note | the manager tier's primitive, testable now |
| 7c | `codex` provider + image fragment (pinned release, sha256), `image smoke` probes incl. the sandbox-flag check, `project claude` → `project agent` (alias kept), docs | first second-agent code |
| 7d | Codex sync-back denylist (adversarial review, like the Claude one) + `AGENTS.md` packs content + `.codex/config.toml` safe-entries | |
| 7e | Third provider (gemini or opencode — operator to pick) to prove the seam is not a two-case special | |
| 7-docs | `docs/AGENTS.md` → implemented; `README`/`CLI.md`/`TUI-GUIDE.md`; CLAUDE.md invariants 1 + 9 wording | |

## 6. Phases after 7 (to be detailed as we go)

- **Phase 11 — Rename to agentry.** §2 tables; label read-shim; `agentryctl migrate` for the XDG
  dirs; repo rename; splash/motd wordmark; `pyproject`; CI. Before the daemon so it is born named.
- **Phase 12 — Control plane (`agentryd`).** FastAPI over a unix socket (localhost), WebSocket event
  streams, a state-tier sqlite event log, the project/container endpoints wrapping `lifecycle`, an
  xterm.js-backed `docker exec` terminal endpoint (replaces terminal spawning for the web UI), a
  per-caller identity model (operator / manager / worker tokens, invariant 7). CLI/TUI untouched
  (direct callers). `agentryctl daemon start|status`.
- **Phase 13 — Tasks + runs.** `Task` (goal, project, agent, branch/worktree, budget, rubric,
  status, artefacts) and `Run` (one headless session via the Phase-7 seam; events persisted;
  usage/cost accounted). Git worktrees under `/workspace` so several tasks run per project without
  breaking one-agent-per-container (one container per concurrent run, cloned from the project's
  definition). Outputs: a branch/PR + a report. `agentryctl task create|run|watch|list|show`.
- **Phase 14 — Threads (the message board).** Email-shaped messages on a task: from/to/cc, subject,
  body, attachments (paths in the run's artefacts), `in-reply-to`, read state. Participants: the
  operator, manager agents, worker agents. Delivered to agents as MCP tools (`inbox`, `read`, `send`,
  `reply`) and to the operator in the web UI + `agentryctl thread …`. Invariant 8.
- **Phase 15 — Web UI v1.** React/Vite/Tailwind under `web/`, served by `agentryd`: projects table
  (parity with the TUI's columns), container actions, live logs, terminals, tasks board, threads,
  approvals. Localhost, no login yet.
- **Phase 16 — Manager tier.** A manager is a project whose agent gets the control-plane MCP server
  and a manager pack (how to decompose, delegate, review, escalate); it runs in a hardened container
  with no docker socket (invariant 7); provider-agnostic. Delegation = create task → run worker →
  read thread/result → score (Jev, §4) → accept / iterate / escalate to the operator via an
  approval on the thread. `decisions/` lands here.
- **Phase 17 — Hosted access.** TLS, login (single-operator password/passkey first; OIDC later),
  remote-host deployment notes, the TUI's future decided once the web UI carries the agent stack.

## 7. Open questions (next round)

1. Third provider for 7e: **Gemini CLI** (a third auth family, big-vendor) or **OpenCode** (ACP,
   `serve` API, multi-provider incl. Claude/OpenAI OAuth — overlaps the hybrid gateway)?
2. Codex `login` mode requires enabling device-code auth in the ChatGPT account's security settings —
   acceptable as a documented prerequisite (like Ollama is for Phase 9)?
3. Manager agents: one long-lived manager per "team", or spawned per task? (Affects Phase 13's task
   ownership model and how the inbox is scoped.)
4. Task budgets: token/cost caps per run, wall-clock caps, or both? Where does the operator set them
   (task, project, global)?
5. Threads: should the operator be able to email INTO a thread from a real mailbox (e.g. a watched
   IMAP folder), or is "email-shaped" purely the in-app protocol for now?
6. ACP as the single headless transport: worth a spike inside 7-run before per-provider parsers?
