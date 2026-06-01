# Roadmap

Phased so an early phase yields a runnable skeleton and each later phase adds one load-bearing
subsystem. Each phase lists its goal and concrete deliverables. Checkboxes track scaffold status.

## Phase 0 — Repo scaffold + image
**Goal:** a buildable repo and a smoke-tested hardened image, no app logic yet.

- [x] `uv` project (`pyproject.toml`) with `textual` + `tomlkit`; `src/claudeman/` package skeleton; `CLAUDE.md` + `README` + docs
- [x] `config.py` XDG path resolution; constants for label prefix + image/container names + baked container paths
- [x] `docker/runner.py` renders the full hardened `docker create` argv (pure, unit-tested)
- [x] `docker/labels.py` label model; `syncback/denylist.py` (unit-tested)
- [x] `images/base/Dockerfile` (debian-slim + node + pinned native claude + non-root uid1000 `/etc/passwd` entry + baked env) and `images/overlays/{python,rust,node}.Dockerfile`
- [ ] `claudemanctl image build` + `image smoke`: `claude doctor` and a one-shot `claude -p` pass inside the fully hardened profile with the verified writable mounts

## Phase 1 — Runnable skeleton (one project, default profile)
**Goal:** the TUI lists and creates a single hardened container under one default profile and opens a shell + claude in it.

- [~] `registry/projects.py` read/write a single `projects/<slug>.toml`; minimal one-repo checkout
- [~] `docker/runner.py` create/start/stop; `docker/status.py` the live `docker ps` JOIN
- [~] `tui/app.py`: `DataTable` populated from `docker ps` (`set_interval` refresh) JOINed with the registry; `n` = create form
- [x] `tui/terminals.py`: detached ghostty/alacritty spawn for `docker exec -it ... bash` and `... claude`; logs pane streaming `docker logs -f`
- [ ] Auth: a single token file injected as `CLAUDE_CODE_OAUTH_TOKEN` (manual `claude setup-token` for now); `ANTHROPIC_*` scrubbed

## Phase 2 — Profiles (work/home) + per-project default
**Goal:** multiple account profiles with a per-project default and safe switching.

- [ ] `profiles/setup_token.py` wrapping `claude setup-token` (+ `--sso` path); `0600` token store + `profiles.lock` with mint time
- [ ] `profiles/identity.py` scrubbed `.claude.json` onboarding stub; `profile.toml` schema + default resolution
- [ ] `claude-config/` seeding from the profile `seed/` through the denylist; create/recreate use the effective profile
- [ ] TUI profile column + token age/expiry warning; switch-time email-mismatch guard; docker-events-driven refresh

## Phase 3 — Persistent multi-repo checkouts + full lifecycle
**Goal:** projects own a set of repos, persist across restarts, and tear down cleanly.

- [ ] `checkout/repos.py`: host-side clone of every `[[repos]]` entry into `workspace/`; `project sync-repos` (fetch, ahead/behind)
- [ ] Idempotent `project delete` (`rm -f` container + `rm -rf` state dir + `rm` toml); start/stop/recreate verbs in TUI + ctl
- [ ] Version-bump-by-recreate flow + running-version status column; `backups/` convention
- [ ] DEFINED/STOPPED/UP JOIN hardened; second `docker inspect` per-label read for robust multi-label parsing

## Phase 4 — Lockable strict egress
**Goal:** per-project opt-in strict egress allowlist that keeps `--cap-drop ALL` intact.

- [ ] `network/allowlist.py` base set (incl. `claude.ai` for OAuth refresh + GitHub + npm) + project extras
- [ ] `network/squid.py`: generate squid+dnsmasq sidecar compose, `internal: true` agent net + egress net, proxy env + apt/npm/git proxy wiring, dnsmasq → `172.17.0.1`
- [ ] `project lock`/`unlock` verbs; denied-request logging surfaced in the TUI for allowlist tuning
- [ ] Smoke: `claude.ai` refresh, github clone, npm install, apt install all succeed under lock; `example.com` blocked

## Phase 5 — Sync-back accept flow
**Goal:** review-gated three-way merge of container config changes to host + setups repo.

- [ ] `syncback/artifacts.py` registry + `denylist.py` enforced before read; `baseline.py` 3-way manifest (sha256 / canonical-JSON / symlink-target)
- [ ] `detect.py` classify; `diff.py` difflib + canonical key-diff + secret-mask; per-profile merge lock
- [ ] `tui/screens/sync_review.py` accept/reject/skip gate with defaults (text accept; settings/MCP/deletions/conflict reject)
- [ ] `merge.py`: backup → field-patch `settings.json` → `claude mcp add/remove --scope` → symlink-preserving tree copy + path rewrite → mirror to `setups/claude-code` + git commit; baseline refresh; `sync --plan` dry-run

---

Legend: `[x]` done in scaffold · `[~]` partially scaffolded (structure + stubs) · `[ ]` not started.
