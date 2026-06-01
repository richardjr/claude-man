# Architecture

This is the full design for claude-man, synthesized from a verified investigation of how Claude
Code behaves inside a hardened container. The load-bearing facts below were confirmed against
`claude` 2.1.159 on the target host (Arch / Hyprland / Docker 29.5.1, rootful, DNS pinned to the
bridge gateway `172.17.0.1`).

## Verified facts that shape the design

- **`CLAUDE_CONFIG_DIR` fully relocates Claude's config root.** The CLI resolves it as
  `process.env.CLAUDE_CONFIG_DIR ?? path.join(os.homedir(), ".claude")`. Everything — `agents/`,
  `skills/`, `plugins/`, `commands/`, `projects/`, `sessions/`, `shell-snapshots/`, `cache/`
  (statsig), `settings.json`, `history.jsonl`, `.credentials.json` — is computed relative to it.
  When `CLAUDE_CONFIG_DIR` is **set**, `.claude.json` lives *inside* the dir (vs `$HOME/.claude.json`
  when unset), so setting it co-locates identity, state, and credentials in one relocatable dir.
- **On Linux the credential store is the plaintext file store** (no keychain). An OAuth refresh
  reads → mutates → atomically rewrites `$CLAUDE_CONFIG_DIR/.credentials.json` (mode `0600`)
  **in place**. So if a token file were present in a bind-mounted config dir, refreshes would land
  back on the host automatically.
- **But we do not seed a credential file at all.** Copying `.credentials.json` into a headless
  container triggers a known 401/no-refresh failure. Instead we mint a long-lived token with
  `claude setup-token` and inject it as `CLAUDE_CODE_OAUTH_TOKEN`, which removes credential
  sync-back entirely and structurally avoids that bug.
- **The hardened profile needs a real `/etc/passwd` entry.** Under `--read-only --user 1000:1000`
  with no passwd entry, `getpwuid`/`os.userInfo()` fails and `HOME` resolves to `/`. The image
  bakes uid 1000 → `/home/agent` and `HOME`/`CLAUDE_CONFIG_DIR` env.
- **`--cap-drop ALL` forbids in-container `iptables`.** Strict egress therefore lives in a sidecar
  proxy on an `internal: true` network, not inside the agent container.

## The three stores (anti-drift)

| Store | Location | Answers | Mutability |
|---|---|---|---|
| **Definition** | `~/.config/claude-man/{projects,profiles}/*.toml` | *what exists / what it is* | operator-editable, git-versionable, secret-free |
| **State** | `~/.local/state/claude-man/...` | *durable bytes* (checkouts, tokens, config dirs, baselines, backups) | written by claude-man; some secret; never committed |
| **Liveness** | `docker ps` / `docker inspect` | *what state the container is in right now* | never stored; read fresh, never cached |

A project **exists iff** its `projects/<slug>.toml` exists — fully decoupled from whether a
container is alive. Labels (`claude-man.{slug,profile,overlay,egress,repos,version,created}`) make
`docker ps` self-describing, but they are a **projection**: on divergence the registry wins and
claude-man reconciles by recreating the container, never by editing the registry from labels.

## Profile / account model

A **profile** is one account identity (work / home) → one long-lived OAuth token + a scrubbed
config seed.

- `~/.config/claude-man/profiles/<name>.toml` — display name, account email (for the TUI + a
  switch-time mismatch guard), `default` flag, and which host assets seed new projects.
- `~/.local/state/claude-man/profiles/<name>/token` — `0600`, the ~1-year token from
  `claude setup-token` (use `claude auth login --sso` first for a work Teams/Enterprise seat).
- `~/.local/state/claude-man/profiles/<name>/identity.json` — a **scrubbed** `oauthAccount`
  block (`emailAddress`/`displayName`/`organizationName` only — never `accountUuid`/`userID`)
  plus `{hasCompletedOnboarding: true, installMethod: "native"}` to suppress the onboarding prompt.

At launch, `CLAUDE_CODE_OAUTH_TOKEN=$(cat token)` is injected and `ANTHROPIC_API_KEY` /
`ANTHROPIC_AUTH_TOKEN` are scrubbed from the env. A project inherits the `default = true` profile
unless its `projects/<slug>.toml` sets `profile = "..."`. Switching a profile is just choosing
which token + identity to inject, then `project recreate` (same workspace + config dir; no
re-clone). A guard warns if the config dir's existing `oauthAccount.emailAddress` mismatches the
new profile's email, to stop work/home cross-contamination. The TUI surfaces token age/expiry and
warns before the ~1-year cliff (the token cannot self-refresh; a 401 in a work container means
*re-mint*, not a code bug).

## Persistence + container lifecycle

- **Definition:** `projects/<slug>.toml` — slug, profile, overlay (image variant), egress mode,
  `[env]` (or `env_file`), `extra_apt`, and a `[[repos]]` array.
- **State:** `~/.local/state/claude-man/projects/<slug>/` with `workspace/` (the checked-out
  repos → bind `/workspace`) and `claude-config/` (per-project `CLAUDE_CONFIG_DIR` → bind
  `/home/agent/.claude`, `0700`), plus sibling `baseline.json` and `backups/`.
- **Checkout:** on `project create`, repos are cloned **host-side** with the host's `gh` PAT (the
  PAT never enters the container). Because `workspace/` is a bind mount and the container runs
  `--user 1000:1000` (matching the host uid), in-container edits land on the host with correct
  ownership. `project sync-repos` does `git fetch` and reports ahead/behind but never auto-resets.
- **Container:** one long-lived **named** container per project (`claude-man-<slug>`), created with
  `docker create` (never `--rm`), `docker start`/`stop`. Restarts/reboots leave the binds untouched,
  so checkouts, sessions, memory, and agents persist. A baked-claude version bump is an explicit
  `project recreate`. `project delete` is an idempotent transaction: `docker rm -f` + `rm -rf` the
  state dir + `rm` the toml. Stop/restart never deletes — **persistence is the default, deletion is
  explicit.**

## Container image

`debian:trixie-slim` (glibc — `claude` is a glibc native ELF; alpine/musl would need extra libs).
Installs `ca-certificates git ripgrep curl` + node (for project tooling / MCP stdio servers), and
bakes the **pinned native `claude` binary** to a read-only system path (NOT npm-installed at
runtime). Creates user `agent` (uid/gid 1000) with a **real `/etc/passwd` entry** and a baked
`/home/agent` (0755). Baked env: `HOME`, `CLAUDE_CONFIG_DIR`, `XDG_CACHE_HOME`,
`USE_BUILTIN_RIPGREP=0` (use the apt ripgrep so claude never extracts a binary to a writable temp),
`DISABLE_AUTOUPDATER=1` (auto-update can't write a read-only rootfs; claude-man owns version bumps).

**Overlays** (`images/overlays/<name>.Dockerfile`, `FROM` the base) add toolchains: `python` (uv),
`rust` (rustup), `node` (extra node). Project-specific lightweight packages come from
`project.toml`'s `extra_apt = [...]`, baked into a thin per-project layer at create time. Project
**env vars are injected at run time** (`-e KEY=VAL` or `--env-file`), never baked, so secrets never
enter an image layer.

Every image is gated by `claudemanctl image smoke`: `claude doctor` + a one-shot `claude -p` inside
the **fully hardened profile**, watching for `EROFS`/`getpwuid`/ripgrep failures before the image is
trusted.

## Hardening (the exact run profile)

```
docker create --name claude-man-<slug> \
  --label claude-man.slug=<slug> ... (see docker/labels.py) \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --user 1000:1000 \
  --pids-limit 1024 \
  --tmpfs /tmp:rw,exec,nosuid,size=512m \
  --tmpfs /home/agent/.cache:rw,exec,nosuid,size=256m \
  -e HOME=/home/agent -e CLAUDE_CONFIG_DIR=/home/agent/.claude \
  -e XDG_CACHE_HOME=/home/agent/.cache \
  -e USE_BUILTIN_RIPGREP=0 -e DISABLE_AUTOUPDATER=1 \
  -e CLAUDE_CODE_OAUTH_TOKEN=<profile token>  (ANTHROPIC_API_KEY/AUTH_TOKEN omitted) \
  -v <state>/projects/<slug>/claude-config:/home/agent/.claude \
  -v <state>/projects/<slug>/workspace:/workspace \
  -w /workspace \
  claude-man:<overlay> \
  sleep infinity        # long-lived; shell/claude opened via `docker exec` from the TUI
```

**Writable surfaces (everything else is read-only):** `/home/agent/.claude` (persistent bind —
the sync-back surface), `/workspace` (persistent bind), `/tmp` and `/home/agent/.cache` (tmpfs,
`exec`). `--pids-limit` is **1024** (not small): claude forks Bash, ripgrep, MCP servers, hooks — a
low limit silently breaks parallel tool calls. `no-new-privileges` + `--cap-drop ALL` is exactly
why the firewall is a network-layer sidecar.

## Network / egress

**Open by default.** Strict mode is per-project, opt-in, and implemented at the network layer so
`--cap-drop ALL` stays intact:

- A generated two-service compose: a `squid`+`dnsmasq` sidecar on both an `internal: true`
  `proj_internal` net and a normal `proj_egress` bridge, and the agent attached **only** to
  `proj_internal` with `--dns <squid>`.
- `internal: true` removes any direct route, so the proxy env can't be bypassed (a real boundary,
  not advisory). `dnsmasq` in the sidecar forwards allowlisted names to `172.17.0.1` (the host
  resolver), since `internal: true` cuts the agent off from it directly.
- The agent gets `HTTPS_PROXY`/`HTTP_PROXY=http://squid:3128`, `NO_PROXY=localhost,127.0.0.1,squid`,
  and the entrypoint wires `git`, `npm`, and apt proxies explicitly.
- **Allowlist** (squid `dstdomain`, CONNECT tunnel, no MITM): base set =
  `api.anthropic.com`, `.anthropic.com`, **`claude.ai`** (OAuth refresh — critical),
  `statsig.anthropic.com`, `registry.npmjs.org`, GitHub (`.github.com`, `codeload.github.com`,
  `.githubusercontent.com`) + the project's `egress.allowlist[]` extras. `http_access deny all`
  otherwise; denied requests are logged for tuning. In-container `iptables` default-DROP is a
  deferred v2 defence-in-depth layer (its `NET_ADMIN` phase would run as a separate pre-start init).

## Sync-back (review-gated three-way merge)

Engine: a stdlib **three-way manifest reconcile** with the **denylist enforced before any read**.
Git is the audit layer only (accepted changes are committed to a `sync-audit/` repo for free
revert history).

1. **Seed (session start):** walk the allowlisted artifacts and write `baseline.json` (sibling of
   the mount, never inside it) — sha256 for file trees, canonical-JSON-subtree hash for
   `settings.json` keys + the MCP block, link-target string for symlinked skills. Snapshot the
   current **host** value of each target too, to make the diff three-way (detect host drift).
2. **Detect (session close, fired by claude-man on container stop — the in-container `SessionEnd`
   hook is disabled in the image so they don't co-fire):** enforce the denylist first, then re-hash
   each allowlisted artifact and classify vs baseline (unchanged/added/modified/deleted; per-key for
   JSON).
3. **Diff:** `difflib.unified_diff` for files; canonical key-path diff for `settings.json` + MCP. A
   secret-mask pass redacts token/key/secret/password/authorization/Bearer values before they reach
   the diff buffer. A three-way conflict (host changed since seed **and** container changed) is
   flagged and forced to manual.
4. **Gate (TUI `SyncReviewScreen`):** a table of changed artifacts + a masked-diff pane;
   `a`/`r`/`s` per row, `enter` to apply. Defaults: authored text (agents/skills/commands/memory/
   `CLAUDE.md`) **accept**; `settings.json` + MCP + deletions + conflicts **reject**. Nothing is
   written until `enter`.
5. **Merge (accepted only):** back up every host target first → copy file-tree artifacts
   (symlink-preserving, container→host path rewrite, honouring the user-vs-project split and the new
   `commands/` target) → **field-patch** `settings.json` (host hooks + statusLine structurally
   immune) → apply MCP via `claude mcp add/remove --scope` → mirror accepted assets into
   `~/Work/setups/claude-code/` and `git commit` (with a denylist re-assertion at staging time) →
   refresh `baseline.json`. A per-profile merge lock serialises concurrent reconciles.

**Never synced:** credentials / OAuth tokens (env-injected — no file to sync), identity
(`oauthAccount`/`userID`/`accountUuid`), history/sessions/transcripts, statsig/caches/shell-snapshots,
host-absolute paths, and the live `.claude.json` wholesale.

## TUI

Textual app (`tui/app.py`). The **projects screen** is a `DataTable`
(Project · Status · Profile · Egress · Repos · Version) populated by an async worker running
`docker ps -a --filter label=claude-man.slug --format '{{json .}}'`, JOINed with the registry so
DEFINED projects with no container still show. A `set_interval` refresh upgrades to an event-driven
worker tailing `docker events`. Bindings: `n` new · `enter` shell · `c` claude · `l` logs · `s`
start/stop · `y` sync-review · `d` delete · `r` recreate.

- **Logs:** a `RichLog` fed by a worker running `docker logs -f --tail 200 --timestamps`; the
  follower is reaped on container switch and app shutdown.
- **Terminal spawn** (`tui/terminals.py`): a **separate OS window** (not `suspend()`), launched
  detached via `Popen(..., start_new_session=True)`. `ghostty` preferred, `alacritty` fallback, with
  `--class=claude-man-<slug>` so a Hyprland `windowrulev2` can place it:
  `ghostty --class=claude-man-<slug> -e docker exec -it claude-man-<slug> {bash|claude}`.

## Open risks

See [`docs/SECURITY.md`](SECURITY.md) for the threat model. Top risks: plaintext long-lived tokens
that can't self-refresh (mitigate with `0600` storage + expiry warnings + treat 401 as re-mint); the
fully hardened profile surfacing an undocumented write path (mitigate with the `image smoke` gate +
reactive writable mounts); strict-egress DNS under `internal: true` (mitigate with the dnsmasq
sidecar → `172.17.0.1`); and sync-back leaking secrets or clobbering host hooks (mitigate with the
before-read denylist, field-patch, pre-merge backups, and deletions-default-reject).
