# Scaffold review — 2026-06-02

A multi-agent audit of the Phase 0/1 scaffold (security invariants, correctness bugs, wiring
gaps, image/Docker, sync-back, TUI). Every finding was independently re-verified against the
actual code/host before it counted. **39 raw findings → 38 confirmed, 1 false-positive dropped.**
Severities below are the *post-verification* values (many initial "high"s were correctly
downgraded because they sit in honestly-stubbed code that isn't wired yet — real, but not live
regressions).

Status key: `TODO` · `DONE` (fixed this pass) · `PLANNED` (folded into a later ROADMAP phase).

> **Resolution update (2026-06-02):** beyond the rows already marked DONE below, Phase 1-min + Phase
> 2 resolved **WIRE-1/2/3/7** (lifecycle wiring + token loader + config seeding), **BUG-1** (env
> coercion), **TUI-1/3/7** (start-on-DEFINED, `enter`→shell, cursor-by-slug), and **SYNC-2** (seed
> field-patch). **IMG-1**'s fix was finalised as a **native `~/.local` install** (which also clears
> the `claude doctor` warnings). Still open: **TUI-2** (async projects refresh), **TUI-4**, **SEC-3**
> (one-claude guard), **SEC-6** (CLI slug validation), and the Phase 3/4/5 design notes.

## Critical

| ID | Location | Finding | Fix | Status |
|----|----------|---------|-----|--------|
| IMG-1 | `images/base/Dockerfile:32-39` | `cp -a /root/.local/bin/claude` copies the installer's **symlink**, not the ~233 MB ELF. The real binary stays under root-only `/root/.local/share/claude/versions/<v>` (0700), so uid 1000 cannot reach it under `--read-only --user`. Build passes (runs as root); every provisioned container would have a broken `claude`. Breaks invariant 2. | Relocate the real `versions/` tree, re-point a stable symlink, `rm -rf /root/.local`, and verify as the **agent** user in the build. | DONE (0.5.1) |

## High

| ID | Location | Finding | Fix | Status |
|----|----------|---------|-----|--------|
| SEC-2 | `docker/runner.py:85-86` | Inline `[project.env]` keys are scrubbed, but `env_file` is handed straight to `docker --env-file`. An `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` in that file silently outranks the OAuth token → wrong-account billing. Breaks invariant 1. | Parse the env-file host-side, drop scrubbed/OAuth keys, inject survivors as pass-through (`-e KEY`, value via subprocess env). | DONE (0.5.3) |

## Medium

| ID | Location | Finding | Fix | Status |
|----|----------|---------|-----|--------|
| WIRE-1 | `cli.py:82-91` | `project create`/`up`/`stop` are `_todo` stubs although `runner.create/start/stop`, `checkout.clone_all`, `projects.save`, `profiles.resolve_for_project`, `identity.build_identity_stub` all exist. | Wire the orchestration. | TODO (Phase 1-min) |
| IMG-3 / WIRE-5 | `cli.py:144-145` | `image smoke` is a stub — the documented gate that would have caught IMG-1 does nothing. | Implement the real smoke gate. | DONE (0.5.2) |
| WIRE-3 | `config.py:100-102`; `profiles/identity.py`; `tui/screens/create.py` | Nothing seeds the `claude-config` bind dir (no mkdir 0700, identity stub, profile-seed copy). First `claude` would re-onboard. | `profiles.seed_project_config`. | TODO (Phase 1-min) |
| IMG-4 | `images/overlays/node.Dockerfile:7-10` | corepack caches yarn/pnpm under `/home/agent/.cache` (wiped by the runtime tmpfs) → first `yarn`/`pnpm` re-downloads → fails under strict egress; `registry.yarnpkg.com` not in base allowlist. | `COREPACK_HOME` to a read-only system path; add yarn hosts to allowlist; smoke offline. | PLANNED (Phase 4 prep) |
| TUI-1 / WIRE-4 | `tui/app.py:103-114` | `s` (start) on a DEFINED project runs `docker start` on a non-existent container and logs green "started" anyway (returncode discarded). | Branch on `Row.kind`; surface returncode/stderr. | TODO (Phase 1-min) |
| TUI-2 | `tui/app.py:50-63` | 2 s refresh shells out to `docker ps` **synchronously on the UI thread**; docs require an async worker. | `@work(thread=True, exclusive)` ps worker posting rows back. | TODO (Phase 1-min) |
| TUI-3 | `tui/app.py:33,46` | `enter`=open_shell is shadowed by DataTable's own Enter/RowSelected handler — the headline shell action silently does nothing when the table is focused (the default focus). | Rebind to an unambiguous key or add `on_data_table_row_selected`. | TODO (Phase 1-min) |

## Low

| ID | Location | Finding | Fix | Status |
|----|----------|---------|-----|--------|
| SEC-3 | `tui/terminals.py:83-84`; `cli.py:77-79` | Nothing enforces "one claude per container" (invariant 6), yet CLAUDE.md asserts in the present tense that the spawn paths do. Two `claude` race on `.claude.json`/session writes. | Add a `pgrep -x claude` guard before spawn, or soften the CLAUDE.md wording. | PLANNED (Phase 1/2) |
| SEC-5 | `docs/SECURITY.md:26` | Trust-boundary table lists "refreshed token (in-place in the bind)" as an allowed container→host flow — contradicts the env-token/no-credentials-file model and could lure a contributor into re-introducing `.credentials.json`. | Remove/reword the row. | DONE (records) |
| SEC-6 | `tui/terminals.py:24-30` | keep-open path rebuilds `docker exec` via `bash -lc` f-string; CLI `project shell/claude <slug>` passes the slug **unvalidated** (no `_SLUG_RE` at the CLI boundary), so a crafted slug reaches the shell string. | Validate slug at the CLI boundary (registry-membership); keep docker exec a pure argv list + terminal hold flag. | PLANNED (Phase 1) |
| BUG-1 | `registry/projects.py:54`; `runner.py:84` | Non-string TOML env values (`DEBUG = true`) render as Python repr (`DEBUG=True`). | Coerce env values to `str` at parse time (bools → `true`/`false`). | TODO (Phase 1-min) |
| BUG-2 | `docker/status.py:83-90` | `_parse_label_csv` splits the `docker ps` Labels CSV on `,` — truncates any label value containing a comma (latent; current values are comma-free). | Read labels from `docker inspect --format '{{json .Config.Labels}}'`. | PLANNED (Phase 3) |
| BUG-3 | `syncback/denylist.py:49,96` | `*-cache.json` (via fnmatch, `*` matches `/`) wrongly denies nested files like `agents/build-cache.json`. Over-denial, not a leak. | Anchor the `*-cache.json` class to `os.path.basename`. | PLANNED (Phase 5) |
| BUG-4 | `syncback/denylist.py:121-128` | `mask_line` misses `export KEY=secret` and a token under an innocuous key (defense-in-depth; not the primary boundary). | Add a value-shape scan (`sk-ant-…`, JWT runs); tolerate a leading `export`/word. | PLANNED (Phase 5) |
| BUG-5 | `docker/status.py:108-109` | `status.join` shows a drifted container **label** value as authoritative over the registry (mild invariant-4 tension; read-only status only). | Prefer registry values for descriptive fields + surface a drift marker. | PLANNED (Phase 3) |
| WIRE-2 | `runner.py:109-125`; `config.py:118-120` | No code reads the profile token file; no `load_token` helper. | `profiles.load_token(name)`. | TODO (Phase 1-min) |
| WIRE-7 | `cli.py:86-87,178-180`; `runner.py:104-106` | `runner.exists` (the idempotency guard a real `up` needs) is implemented but unused; `up` advertises "create-if-needed" it can't do. | Use `runner.exists` in the `up` wiring. | TODO (Phase 1-min) |
| IMG-2 | `images/base/Dockerfile:42-46`; `runner.py:28-45` | `XDG_STATE_HOME` unset → claude's lock dir resolves to the read-only rootfs. Error is **swallowed** (not fatal): degraded lock/concurrency-guard + log noise. | Bake `XDG_STATE_HOME` onto a writable surface; assert in smoke. | DONE (0.5.1) |
| IMG-5 / SEC-4 | `Dockerfile:15`; `config.py:31` | Pin 2.1.159 trails host 2.1.160; `DISABLE_AUTOUPDATER=1` means it never self-heals. | Bump both in lockstep + re-smoke. | DONE (0.5.1) |
| SYNC-1 | `syncback/artifacts.py:36-48` | `Artifact.scope` promises user/project/repo but only `user` is ever constructed (dead code until Phase 5). | Add project/repo producers + per-scope tests. | PLANNED (Phase 5) |
| SYNC-2 | `registry/profiles.py:13` | Default seed copies `settings.json`+`plugins/` verbatim; the path-based denylist does not field-patch settings.json inbound, so the host `SessionEnd → sync-claude.sh --save` hook + bun statusLine would be seeded into the container (a forbidden host-tool co-fire). | Inbound field-patch to strip `hooks`/`statusLine`; trim `plugins/` (exclude cache/data/blocklist.json). | DONE (2.3 — `seed.capture_profile_seed`/`_patch_settings`) |
| SYNC-3 | `syncback/denylist.py:58-59` | `skills` is `tree-symlink` and defaults to accept; the planned merge preserves symlinks with no target constraint — a container-authored absolute/escaping symlink would be planted on the host. Design note (no merge code yet). | Containment check after rewrite; reject/down-rank absolute/escaping links; fixture test. | PLANNED (Phase 5) |
| SYNC-5 | `syncback/diff.py:27-28` | `json_key_diff` (unimplemented) must drop `is_denied_json_key` keys **before** diffing; the line-mask only catches secret-named keys, so identity keys (`oauthAccount`/`userID`) could render into the diff buffer. | Drop denied keys before diffing; regression test. | PLANNED (Phase 5) |

## Nits

| ID | Location | Finding | Status |
|----|----------|---------|--------|
| BUG-6 | `checkout/repos.py:55-67` | `fetch_all` returns the raw multi-line `git fatal` as `detail` when `origin/<branch>` is absent. | PLANNED (Phase 3) |
| WIRE-6 | `ROADMAP.md:19-23` | Phase 1 `[~]` boxes are "read path done, write/create path absent"; goal prose not yet met. | DONE (records — ROADMAP updated) |
| IMG-6 | `Dockerfile:32-34` | `install.sh` always bootstraps "latest"; the VERSION arg pins only via the forwarded `claude install <target>`. Works, but undocumented coupling — a future installer refactor could silently break the pin. | DONE (0.5.1 — comment + smoke version assert) |
| SYNC-4 | `syncback/denylist.py:19-53` | `settings.local.json` is in neither DENY_PATHS nor SYNC_ARTIFACTS (safe only because reads are allowlist-driven). | DONE (records — added to DENY_PATHS) |
| SYNC-6 | `syncback/denylist.py:19-53` | `debug/` and `todos/` are excluded only by being unlisted, not denied; invariant 5 also names legacy `tasks/` but not the current `todos/`. | DONE (records — added to DENY_PATHS) |
| TUI-4 | `tui/app.py:103-114` | `action_toggle_running` re-queries docker (redundant blocking call + benign TOCTOU). | TODO (Phase 1-min) |
| TUI-5 | `tui/screens/{logs,create,sync_review}.py`; `ROADMAP.md:22` | logs/create/sync screens are pure stubs; ROADMAP line 22 bundles a stubbed logs pane under a `[x]` box. | DONE (records — ROADMAP split) |
| TUI-6 | `tui/app.py:78-101`; `status.py:111-117` | Actions fire on orphan rows (container, no registry entry) with no guard/distinct UX. | PLANNED (Phase 3) |
| TUI-7 | `tui/app.py:65-76` | Cursor restore by integer index snaps to row 0 when the selected row vanishes from a shrinking, slug-sorted table. | TODO (Phase 1-min) |

## Dropped (false positive)

- **MCP host_target user-scope** (`syncback/artifacts.py:45`) — claimed the MCP artifact targets the
  wrong location because servers live under `projects[<path>].mcpServers`. Refuted: that path-keyed
  subtree is the *local* scope; `claude mcp add --scope user` correctly writes the top-level
  `mcpServers` in `~/.claude.json`, which is exactly what the `scope="user"` artifact targets. No defect.

---

*Full per-finding evidence (verifier reasoning, reproduction) is in the workflow transcript that
produced this review.*

# New-project form review — 2026-06-03

A multi-agent adversarial review of the TUI new-project form (`tui/screens/create.py` +
`action_new_project`/`_create_project_worker` in `tui/app.py`), across correctness/Textual idioms,
concurrency/UI-thread safety, and CLAUDE.md-invariant preservation. **4 findings raised → 4
confirmed (0 false positives)**; they collapse to two root issues, both fixed this pass. The form
itself preserves every load-bearing invariant — it funnels through the same `lifecycle.create_project
→ ensure_created → runner.create` chain as the CLI, so the hardened argv, the no-`.credentials.json`/
no-`ANTHROPIC_*` rule, registry-as-source-of-truth, and one-claude-per-container are all untouched.

| ID | Location | Finding | Fix | Status |
|----|----------|---------|-----|--------|
| FORM-1 | `tui/app.py` `_create_project_worker`; `docker/runner.py:_run` | The `@work(thread=True)` create worker caught only `ValidationError`/`RuntimeError`. `OSError` from config-dir seeding **and `FileNotFoundError` from a missing `docker` binary** escaped the worker → Textual's default `exit_on_error=True` tore the whole TUI down (contradicting the worker's own docstring). The synchronous `up`/`recreate` paths shared the missing-`docker` hole. | Root-caused in `runner._run`: a missing binary now maps to a `127` "not found" `CompletedProcess` (benefits CLI + sync UI paths too). Broadened the worker catch to `(OSError, RuntimeError)` + a last-resort `except Exception`, each → a red `Result`. | DONE (2026-06-03) |
| FORM-2 | `registry/projects.py:save`/`list_projects`; `tui/app.py` 2 s poll | Moving create into a background worker created a new cross-thread race: the UI-thread projects poll reads the registry via `tomllib.load` while the worker's **non-atomic** `save()` writes it → a torn read raises `TOMLDecodeError` (uncaught) → UI crash. Same root enables a benign same-slug write race (FORM-3, nit). | `save()` now writes a sibling temp file + `os.replace` (atomic on one FS); `list_projects` also skips `TOMLDecodeError` as defense-in-depth. | DONE (2026-06-03) |

Tests added (now 51 total): `runner._run` maps a missing binary to `127`; `projects.save`
round-trips atomically with no `.tmp` residue; `list_projects` skips malformed TOML. Behavioural
verification was via a headless Textual harness (form validation: empty/invalid/duplicate blocked,
dismiss via button/Enter/Escape; worker robustness: forced `OSError` and `KeyError` both log a red
`Result` with the app still running).

**Not addressed here (unchanged scope):** **SEC-6** remains open — it is the CLI `project
shell`/`claude` exec boundary in `terminals.py`, *distinct* from the create-form slug check this
work added. **SEC-3** (one-claude guard) and **TUI-2** (async projects poll) also remain open; TUI-2
in particular would further reduce the create-worker/poll contention noted in FORM-2.
