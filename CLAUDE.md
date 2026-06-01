# CLAUDE.md

Guidance for Claude Code (and humans) working in the **claude-man** repository.

claude-man is a Python **Textual TUI** + **`claudemanctl`** CLI that provisions and manages
hardened Docker containers, each running Claude Code under a chosen account profile, for a set of
long-lived git-checkout projects. Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full
design and [`ROADMAP.md`](ROADMAP.md) for the phase plan before making non-trivial changes.

## Load-bearing invariants — do not break these

These are security- and correctness-critical. Every change must preserve them.

1. **Never copy `.credentials.json` into a container, and never inject `ANTHROPIC_API_KEY` /
   `ANTHROPIC_AUTH_TOKEN`.** Auth is the env-var long-lived token model: `claude setup-token` once
   per profile on the host → a `0600` token file → injected at launch as `CLAUDE_CODE_OAUTH_TOKEN`.
   Copying `.credentials.json` triggers the known headless 401/no-refresh bug; `ANTHROPIC_*` keys
   silently outrank the OAuth token and can bill the wrong account, so they are scrubbed from the
   rendered container env. Never pass `--bare` to the in-container `claude` (it ignores the token).
2. **The hardened run profile is the floor, not a suggestion.** `--read-only`, `--cap-drop ALL`,
   `--security-opt no-new-privileges`, `--user 1000:1000`, `--pids-limit 1024`, with writable
   surfaces limited to: the persistent `claude-config` bind (`/home/agent/.claude`), the persistent
   `workspace` bind (`/workspace`), and two `tmpfs` mounts (`/tmp`, `/home/agent/.cache`, both
   `exec`). The image bakes a real `/etc/passwd` entry + `HOME` for uid 1000 (without it,
   `getpwuid` fails under `--read-only --user` and `HOME` resolves to `/`). Do not relax these to
   "make something work" — fix the writable-mount set or the image instead, and re-run
   `claudemanctl image smoke`.
3. **The firewall lives at the network layer, never in-container iptables.** `--cap-drop ALL`
   forbids `NET_ADMIN`, so strict egress is a squid+dnsmasq **sidecar** on an `internal: true`
   network, not `iptables` inside the agent container. The base allowlist must always include
   `claude.ai` (the OAuth subscription refresh path) or token refresh fails opaquely.
4. **Registry is the source of truth; docker labels are a projection.** A project exists iff its
   `~/.config/claude-man/projects/<slug>.toml` exists. Live status is read fresh from
   `docker ps`/`inspect` and **never** cached. On any divergence, reconcile *toward* the registry by
   recreating the container (re-stamping labels) — never edit the registry from labels.
5. **Sync-back enforces the denylist before any read, and again at git-staging time.** Never read
   or sync: `.credentials.json`, `.claude.json` (wholesale), `.config.json`, `history.jsonl`,
   `sessions/`, transcripts, `shell-snapshots/`, `statsig`/`cache/`, `file-history/`, `tasks/`,
   `plans/`, `*-cache.json`, `backups/`; and the JSON keys `oauthAccount`, `userID`, `accountUuid`,
   and `last*`/`cached*`/telemetry keys. `settings.json` is **field-patched** (host hooks +
   statusLine are structurally immune), MCP changes are applied via `claude mcp add/remove --scope`,
   and every host target is **backed up before** merge. Deletions and conflicts **default to reject**.
   See `src/claudeman/syncback/denylist.py`.
6. **One `claude` per container.** A second shell is fine; a second `claude` in the same container
   races on `.claude.json`/session writes. The TUI's spawn paths enforce a single claude session per
   project; don't add code paths that launch a second one.

## Project layout

```
src/claudeman/
  config.py            XDG paths + all shared constants (label prefix, container/image names, baked container paths)
  cli.py               claudemanctl argparse surface (profile / project / sync / image verbs)
  __main__.py          `python -m claudeman` -> TUI;  argv dispatch
  registry/            projects.py, profiles.py, schema.py  — TOML definition store (tomllib read, tomlkit write)
  docker/              labels.py (label model), runner.py (hardened `docker create` argv), status.py (live ps JOIN)
  profiles/            setup_token.py (`claude setup-token` wrapper), identity.py (scrubbed .claude.json stub)
  checkout/            repos.py — host-side git clone/fetch into workspace/ (host PAT never enters the container)
  network/             allowlist.py (base egress set), squid.py (strict-egress sidecar generator)
  syncback/            denylist.py, artifacts.py, baseline.py, detect.py, diff.py, merge.py — the review-gated 3-way merge
  tui/                 app.py, terminals.py (detached ghostty/alacritty spawn), screens/
images/                base/Dockerfile + overlays/{python,rust,node}.Dockerfile
templates/             project.toml.example, profile.toml.example, claude-json-stub.json, squid.conf.j2
tests/                 dependency-free unittest suite (argv renderer, denylist, registry)
```

Runtime state lives **outside the repo** under `~/.config/claude-man` (definitions) and
`~/.local/state/claude-man` (workspaces, tokens, config dirs). The `.gitignore` hard-blocks
`*.credentials.json`, `secrets.toml`, `/state/`, `/profiles/` as a belt-and-braces guard.

## Conventions

- **Python ≥ 3.11**, managed by **`uv`** (no pip/poetry). Read TOML with stdlib `tomllib`; write
  with `tomlkit` to preserve operator comments.
- **Tests must stay dependency-free** (`python -m unittest`): pure-stdlib, no docker/network/textual
  needed. Keep `textual` imports inside `tui/` so the CLI and tests import without it installed.
- **Shelling out to docker/git/claude** is done via `subprocess` with explicit argv lists (never
  `shell=True`). The hardened argv is rendered by one pure function (`docker/runner.py::build_create_argv`)
  so it can be unit-tested without a daemon.
- Stubs for unimplemented phases raise `NotImplementedError("phase N: ...")` referencing
  [`ROADMAP.md`](ROADMAP.md) — keep them honest rather than silently no-op.

## Common commands

```bash
uv sync                       # install deps
uv run claudemanctl --help    # CLI
uv run claudeman              # TUI
uv run python -m unittest     # tests (no deps required)
uv run ruff check src tests   # lint
```

## Commit & PR rules

Follow the workspace conventions: short, factual subject lines (what changed, not why); optional
one-paragraph body; **no** "Co-Authored-By" trailer, marketing language, emojis, or generated-by
footer unless explicitly asked. **Never commit, push, or open a PR unless explicitly asked.**
