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

The Definition and State roots resolve from `$XDG_CONFIG_HOME` / `$XDG_STATE_HOME` (falling back to
`~/.config` / `~/.local/state`), and can be relocated wholesale with the `CLAUDE_MAN_CONFIG_HOME` /
`CLAUDE_MAN_STATE_HOME` env overrides — the unit suite sets these to a tmpdir so it exercises the
real path logic without touching operator state. See `config.config_home()` / `config.state_home()`.

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

**Implemented account verbs.** `profile add <name>` runs `claude auth login` (optionally `--sso`/
`--console`/`--email`) then `claude setup-token`, stores the `0600` token, and records the account
email from `claude auth status --json`; `profile renew` re-mints in place. **`profile verify`**
re-checks a token against `auth status` in an isolated config dir (mirroring the container) — but an
OAuth token only reports `authMethod: oauth_token`, *not* the account email, so verify confirms
*validity* while the **identity is the mint-time record** (the email captured when the host was
logged into that account). Token age is the token file's mtime (`profiles.token_age_days`).
**Switching** a project's account is `project recreate <slug> --profile <X>`: it tears down the
container (keeping the workspace + config binds), re-seeds the identity for the new account, and
swaps the injected token — gated by the **email-mismatch guard** (`lifecycle.account_mismatch`),
which refuses unless `--force` when the config dir already belongs to a different account.
**`profile seed`** captures the host's allowlisted `~/.claude` assets into the profile's `seed/`
(field-patching `settings.json` to strip host `hooks`/`statusLine`, excluding machine-local cruft)
so new projects on that profile inherit them.

## Token usage (per-account)

`usage.py` parses each project's container transcripts (`claude-config/projects/**/*.jsonl`), summing
only the per-message token-count fields (`input`/`output`/`cache_creation`/`cache_read`), never
message content. Usage is attributed to each project's current profile and aggregated per account —
the metric to watch against subscription limits. It is a **read of claude-man's own state** (host-side,
separate from sync-back — nothing crosses the denylist boundary) and counts only usage produced
*inside* claude-man containers, not the operator's host `~/.claude`. Surfaced via
`claudemanctl profile usage` and a worker-refreshed TUI panel (`u` to refresh).

## Subscription usage (per-account 5-hour + weekly limits)

Transcript token-totals (above) measure what claude-man *itself* spent; they cannot reveal how close
an **account** is to the rolling-window subscription caps Claude Code's `/usage` shows — that figure
spans *all* usage on the account, including the operator's own host sessions. `usage_api.py` reads it
straight from the source: `GET https://api.anthropic.com/api/oauth/usage` (`config.OAUTH_USAGE_URL`,
beta header `oauth-2025-04-20`) with a profile's stored OAuth bearer token, returning the `five_hour`
and `seven_day` *utilization* percentages (0–100) plus reset timestamps (the endpoint also carries
`seven_day_opus`/`seven_day_sonnet`, parsed into `Utilization` for a future per-model view but not yet
surfaced). The fetch is **read-only and does not consume quota** — it's a status read, safe to poll.

- **The token-scope split (why minting now requests both).** `claude setup-token` mints a token with
  the `user:inference` scope only — enough to *run* Claude in the container, but a **403** on
  `/api/oauth/usage`, which needs `user:profile`. Rather than carry a second token, `profiles/setup_token.py`
  mints with `CLAUDE_CODE_OAUTH_SCOPES="user:profile user:inference"` (`config.OAUTH_USAGE_SCOPES`) so the
  **same** token both runs inference *and* reads usage. Tokens minted before this change lack the scope;
  the fetch folds their 403 into the note `re-mint`, and the operator re-mints in place with
  `claudemanctl profile renew <name>` to gain it.
- **The no-redirect opener (a credential-leak fix).** The fetch uses a module-level
  `urllib.request.build_opener(_NoRedirect())` whose `redirect_request` returns `None`, so a `30x` is
  turned into an `HTTPError` instead of being followed. urllib's default redirect handler copies the
  `Authorization` bearer onto the redirect target *without* stripping it on a cross-host hop — for this
  fixed JSON endpoint a redirect is anomalous, and following one would re-send the account credential to
  another host (invariant 1). The token is therefore never sent twice. Standard TLS verification is kept.
- **User-Agent matters.** The endpoint rate-limits a generic UA aggressively, so requests send
  `User-Agent: claude-code/<ver>` (`config.CLAUDE_CODE_USER_AGENT`).
- **Pure/network split + failure handling.** `parse_utilization` / `render_bar` / `level` are pure
  (unit-tested without sockets); `fetch_utilization` / `fetch_for_profile` do the I/O and **never raise** —
  every failure folds into a short `UsageResult.note` (`no token`, `re-mint` for the 403, `auth` for a
  401, `offline`, `http NNN`, `bad resp`). `_window` rejects `bool`/`NaN`/`inf` so a junk utilization can't
  read as a colour band in `level()`.
- **Worker + surfaces.** The TUI runs a **separate 60 s `refresh_utilization` worker** off the UI thread,
  caching results in `self._util`; the usage panel reads that cache and renders 5h + Week columns as
  coloured mini bars (green `<70%` < yellow `<90%` < red, via `level`), with a panel title noting the
  figures are account-wide subscription limits, not claude-man's slice. `u` refreshes both the transcript
  totals and the utilization cache. The CLI surface is `claudemanctl profile limits [name]`.

## Persistence + container lifecycle

- **Definition:** `projects/<slug>.toml` — slug, profile, overlay (image variant), egress mode,
  `[env]` (or `env_file`), `extra_apt`, and a `[[repos]]` array.
- **State:** `~/.local/state/claude-man/projects/<slug>/` with `workspace/` (the checked-out
  repos → bind `/workspace`) and `claude-config/` (per-project `CLAUDE_CONFIG_DIR` → bind
  `/home/agent/.claude`, `0700`), plus sibling `baseline.json` and `backups/`.
- **Checkout:** on `project create`, repos are cloned **host-side** with the host's `gh` PAT (the
  PAT never enters the container). Because `workspace/` is a bind mount and the container runs
  `--user 1000:1000` (matching the host uid), in-container edits land on the host with correct
  ownership. `project repo add` clones one repo live into the existing `/workspace` bind (visible in a
  running container at once — no recreate; the immutable `claude-man.repos` count label is allowed to
  drift, registry wins). `project sync-repos` clones any missing repo then `git fetch`es, never
  auto-resets. Live per-repo state — branch, clean/dirty, ahead/behind (parsed from a single
  `git status --porcelain=v2 --branch` header against the actual `@{upstream}`), and branch-vs-config
  drift — is read host-side by `checkout/gitstate.py` (a pure parser + thin subprocess shell), surfaced
  by `project repo list`. A repo `dir` is containment-checked so it can never escape `workspace/`, and
  any credential in a remote URL is masked before it reaches a surfaced string.
- **Container:** one long-lived **named** container per project (`claude-man-<slug>`), created with
  `docker create` (never `--rm`), `docker start`/`stop`. Restarts/reboots leave the binds untouched,
  so checkouts, sessions, memory, and agents persist. A baked-claude version bump is an explicit
  `project recreate`. `project delete` is an idempotent transaction: `docker rm -f` + `rm -rf` the
  state dir + `rm` the toml. Stop/restart never deletes — **persistence is the default, deletion is
  explicit.**

## Container image

`debian:trixie-slim` (glibc — `claude` is a glibc native ELF; alpine/musl would need extra libs).
Installs `ca-certificates git ripgrep curl` + node (for project tooling / MCP stdio servers), the
**pinned GitHub CLI `gh`** (from the upstream `.deb` — see "In-container git identity + GitHub CLI"),
and installs the **pinned native `claude`** into the agent's own `~/.local` by running the official
installer **as uid 1000** — the exact location `installMethod: native` and `claude doctor` expect,
so the runtime is doctor-clean and the binary is reachable under `--read-only --user` (auto-update
is disabled, so a read-only `~/.local` is fine; NOT npm-installed at runtime). Creates user `agent`
(uid/gid 1000) with a **real `/etc/passwd` entry** and a baked `/home/agent` (0755). Baked env:
`HOME`, `CLAUDE_CONFIG_DIR`, `XDG_CACHE_HOME`, `XDG_STATE_HOME` (under the writable `.cache` tmpfs so
claude's version-lock dir doesn't hit the read-only rootfs), `GIT_CONFIG_GLOBAL`/`GH_CONFIG_DIR` (also
under `.cache` so `git config --global`/`gh auth` don't hit the read-only rootfs — see "In-container git
identity"), `PATH` (prepends `~/.local/bin`), `USE_BUILTIN_RIPGREP=0` (use the apt ripgrep so claude never
extracts a binary to a writable temp), `DISABLE_AUTOUPDATER=1` (auto-update can't write a read-only
rootfs; claude-man owns version bumps).

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
  --tmpfs /home/agent/.cache:rw,exec,nosuid,size=256m,uid=1000,gid=1000,mode=0700 \
  -e HOME=/home/agent -e CLAUDE_CONFIG_DIR=/home/agent/.claude \
  -e XDG_CACHE_HOME=/home/agent/.cache -e XDG_STATE_HOME=/home/agent/.cache/state \
  -e GIT_CONFIG_GLOBAL=/home/agent/.cache/gitconfig -e GH_CONFIG_DIR=/home/agent/.cache/gh \
  -e USE_BUILTIN_RIPGREP=0 -e DISABLE_AUTOUPDATER=1 \
  -e CLAUDE_CODE_OAUTH_TOKEN=<profile token>  (ANTHROPIC_API_KEY/AUTH_TOKEN omitted) \
  -e GIT_CONFIG_COUNT=2 -e GIT_CONFIG_KEY_0=user.name -e GIT_CONFIG_VALUE_0=<name> \
  -e GIT_CONFIG_KEY_1=user.email -e GIT_CONFIG_VALUE_1=<email>   (when a git identity resolves) \
  -v <state>/projects/<slug>/claude-config:/home/agent/.claude \
  -v <state>/projects/<slug>/workspace:/workspace \
  -w /workspace \
  claude-man:<overlay> \
  sleep infinity        # long-lived; shell/claude opened via `docker exec` from the TUI
```

**Writable surfaces (everything else is read-only):** `/home/agent/.claude` (persistent bind —
the sync-back surface), `/workspace` (persistent bind), `/tmp` and `/home/agent/.cache` (tmpfs,
`exec`), and — **only when a project declares an `ssh` env-mount** — a `0700` `/home/agent/.ssh`
tmpfs. `--pids-limit` is **1024** (not small): claude forks Bash, ripgrep, MCP servers, hooks — a
low limit silently breaks parallel tool calls. `no-new-privileges` + `--cap-drop ALL` is exactly
why the firewall is a network-layer sidecar.

**The `.cache` tmpfs must be agent-owned.** Docker special-cases `/tmp` to sticky world-writable
(`1777`), so it's writable for free — but a *named* tmpfs like `/home/agent/.cache` defaults to
`root:root mode=755`, which uid 1000 cannot write. Left bare, `node`/`corepack` (`mkdir ~/.cache/node`),
claude's `XDG_STATE_HOME=~/.cache/state`, and the git/gh config redirect (below) all fail `EACCES`/`EROFS`
under `--read-only --user 1000`. So `_HARDENING` pins the `.cache` tmpfs `uid=1000,gid=1000,mode=0700`
(keeping `nosuid,exec,size=256m`). This is **not** a floor relaxation — it makes a surface invariant 2
already calls writable *actually* writable by the agent. tmpfs options are fixed at `docker create`, so
the fix lands on **`recreate`** with no image rebuild, and `image smoke` now probes the `.cache` write.

## In-container git identity + GitHub CLI

`git commit` and `gh` both want to write config into the home dir, but the rootfs is `--read-only`:
`git commit` fails *Author identity unknown* and `git config --global` / `gh auth` fail with
*could not lock … Read-only file system*. claude-man fixes both without relaxing the floor.

- **Identity via git ENV-config, not a file.** `gitconfig.py::env_for` renders the author identity as
  git's environment config — `GIT_CONFIG_COUNT` + `GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n` (equivalent to
  `git -c user.name=… -c user.email=…`) — which needs **no writable `~/.gitconfig`**, so `git commit`
  works under the read-only rootfs. The env is injected at `docker create` via
  `runner.build_create_argv(git_env=…)` ← `lifecycle.ensure_created` ← `gitconfig.container_env()`. Name
  and email are **non-secret**, so they're rendered as plain `-e KEY=value` (unlike the OAuth token's
  pass-through form). An empty identity emits `{}` — nothing is forced.
- **Settings-override-else-inherit-host precedence.** `resolve_identity()` takes the claude-man global
  settings (`config.toml` `[git]` `user_name`/`user_email`) when set, else inherits the operator's own
  host `git config --global user.{name,email}`, else `""`. So a fresh install "just works" with the
  operator's host identity, and a profile/workspace that should commit under a different name overrides it
  in settings. Identity is fixed at create time, so a change needs a **`recreate`** to apply.
- **Writable git/gh config redirected to the `.cache` tmpfs.** For everything *other* than identity —
  `git config --global` writes, `gh auth login` state — `GIT_CONFIG_GLOBAL=/home/agent/.cache/gitconfig`
  and `GH_CONFIG_DIR=/home/agent/.cache/gh` (`config.CONTAINER_GITCONFIG`/`CONTAINER_GH_CONFIG`, baked in
  both `runner._BAKED_ENV` and the Dockerfile `ENV`) point those at the writable `.cache` tmpfs instead of
  the read-only `~/.gitconfig` / `~/.config/gh`, dodging `EROFS`.
- **`gh` baked into the image, auth left to the operator.** The GitHub CLI isn't in Debian's repos, so
  `images/base/Dockerfile` installs the **pinned upstream `.deb`** (arch-aware; `ARG GH_VERSION`,
  `config.DEFAULT_GH_VERSION = 2.93.0`). **No token is injected** — `gh auth` is the operator's job:
  `gh auth login` works in-container (writing the writable `GH_CONFIG_DIR`), or a `GH_TOKEN` can be supplied
  via an env-mount. Because this is an image change, picking up `gh` needs an **image rebuild**
  (`image build base`, then the overlay e.g. `image build node`) followed by a `recreate`. `image smoke`
  probes `gh` presence and the `git config --global` writability.

## Global settings (`config.toml`)

The "general features" config tier lives at `~/.config/claude-man/config.toml` — a third store beside
the per-project and per-profile TOMLs, holding cross-cutting operator preferences rather than anything
project-scoped. It is **secret-free** (ssh key *paths* and a git name/email only), so like the other
definitions it round-trips through git. `registry/settings.py` reads it with stdlib `tomllib` and writes
it comment-preserving with `tomlkit`; a missing file is not an error (it resolves to a default
`Settings()`). Today it carries `[ssh] keys`/`auto_load` (host keys to forward) and `[git]
user_name`/`user_email` (the identity override above; `set_git_identity` clears it with empty strings).
The TUI opens a Settings screen with `,` (showing the resolved git identity, edited via a `GitIdentityScreen`
where a blank field means *inherit host*); the CLI surface is `claudemanctl config git [--name … --email … |
--clear]` and `config show` (which now also prints the resolved git identity).

## Environment mounts (ssh + files)

A project can declare `[[project.env_mount]]` entries to make host material available **inside** the
container for the agent's own runtime/git ops (the host-side clone uses the host's ssh; this is for
in-container `git push`/`pull`, `.netrc`, certs, etc.). Two kinds, both rendered **additively** by
`docker/runner.py::_render_env_mounts` — they emit only `-v`/`--tmpfs`/`-e`, never a `_HARDENING`
flag, so the floor is byte-identical with or without them (a unit test pins this; the design was
verified empirically against the exact hardened profile, including a real GitHub ssh round-trip):

- **`file`** → a read-only (`:ro`, default; `rw` opt-in) bind of a host `src` at an absolute container
  `dst`. A bind overlays any path even on the read-only rootfs, with host perms (e.g. `0600`)
  preserved. `src` must resolve to an **absolute** host path (a relative one becomes a docker named
  volume, not a bind); a **trailing-slash `dst`** appends the src basename (`cp`-style). `dst` is
  **containment-checked** (`schema.EnvMount`, with a leading-`//` collapse so the kernel's
  normalization can't be used to slip the check): it may not be relative, contain `..`, or target a
  claude-man-managed mount — **never** `/home/agent/.claude/…` (a bind there smuggles a working
  `.credentials.json` — a verified attack), the `.claude.json` sibling, `/home/agent/.ssh` (no binding
  a private key in), `/home/agent/.local/` (the baked claude launcher runs with the OAuth token),
  `/tmp`, or `/home/agent/.cache`. **`/workspace/<path>` IS allowed** (a workspace-root `CLAUDE.md`
  above the per-repo ones is a primary use case) — gitstate reads the registry not the filesystem, so
  it isn't polluted, and `lifecycle._ensure_workspace_mountpoints` pre-creates the nested mountpoint
  operator-owned so Docker doesn't root-create it.
- **`ssh`** → **agent-forwarding**: a read-only bind of the host `$SSH_AUTH_SOCK` at `/ssh-agent` +
  `SSH_AUTH_SOCK` pointing at it, plus the `0700` `~/.ssh` tmpfs. **Private keys never enter the
  container** — the host agent signs. Post-start, the host's `~/.ssh/{config,known_hosts}` (non-secret)
  are seeded into the tmpfs via `docker exec -i … 'cat > …'` (a `docker cp` into a `--read-only`
  container fails — verified).

**Mounts are fixed at `docker create`**, so adding/removing an env-mount needs a `recreate` to take
effect (surfaced in the `add`/`remove` Result, the same honesty as the repos count-label drift). The
host source must exist operator-owned before start — a missing `file` source is **refused, never
auto-created** (Docker would create a missing bind source as `root` at start). `project resync`
re-validates sources and re-seeds the ssh tmpfs into a running container (no recreate). Verbs:
`project env add ssh|file`, `project env rm`, `project env list`, `project resync`. The base image
ships `openssh-client` for the in-container `ssh`. **Validation is strict at the add boundary but lenient
at load** (`EnvMount.lenient`): a mount valid when saved but invalidated by a later-tightened rule (e.g.
the case-typo guard) loads **flagged** (`error` set) — visible/removable in the env screen, round-tripped
on save, and skipped by the render/lifecycle — rather than crashing `projects.load` (and the TUI).

## Network / egress (Phase 4 — implemented)

**Open by default.** Strict mode is per-project, opt-in (`project lock <slug>` / create
`--egress strict`), and implemented at the network layer so `--cap-drop ALL` stays intact
(invariant 3 — the firewall is never in-container `iptables`):

- **One per-project `--internal` docker network** (`claude-man-net-<slug>`). `--internal` removes
  the network's gateway, so a container on it has **no route to the internet at all** — a real
  boundary, not advisory.
- **The agent attaches to that network only** (rendered additively in
  `docker/runner.py::_render_egress`, so the hardened floor is byte-identical to an open project —
  a unit test pins this), with `HTTP(S)_PROXY` (upper + lower case) pointing at the squid sidecar by
  its in-network DNS name and `NO_PROXY=localhost,127.0.0.1,::1,<proxy>`.
- **The squid sidecar** (`claude-man-proxy-<slug>`, image `claude-man:proxy`) sits on that internal
  network **and** the default bridge (attached via `docker network connect bridge` after run), so it
  is the **only** path out. squid enforces the `dstdomain` allowlist over **CONNECT tunnels — no
  MITM, no CA install** (HTTPS stays end-to-end); `http_access deny all` otherwise, and denials are
  logged to stdout so `project egress-log` / the TUI Project-menu **Egress log** can surface them for
  allowlist tuning.
- **Allowlist** (`network/allowlist.py`, rendered to squid.conf by the pure `network/squid.py`):
  `api.anthropic.com`, `.anthropic.com`, **`claude.ai`** (OAuth refresh — critical),
  `statsig.anthropic.com`, `sentry.io`, the registries (`registry.npmjs.org`, PyPI,
  `registry.yarnpkg.com`), Debian apt mirrors, GitHub (`.github.com`, `codeload.github.com`,
  `.githubusercontent.com`) + the project's `egress.allowlist[]` extras.
- **Orchestration** lives in `network/egress.py` (explicit `docker network`/`docker run` argv — no
  compose, so the agent stays on the single unit-tested `build_create_argv` renderer). The sidecar is
  trusted infra (our fixed image + rendered config, no agent code, sees only CONNECT hostnames) so it
  is not under the agent's hardened floor, but runs `--security-opt no-new-privileges`.
- **Fail-closed:** `up` aborts if the sidecar can't start, so a locked project never runs with broken
  egress. `lifecycle.set_egress` (lock/unlock) recreates to apply (egress is fixed at `docker create`,
  like ports/mounts); unlock tears the sidecar + network down. `image smoke proxy` builds the sidecar;
  `project egress-smoke <slug>` validates a locked project end-to-end (allowlisted host reaches,
  non-allowlisted host blocked).

**Deferred refinements (not blockers):** the agent reaches external hosts only through the proxy
(HTTP CONNECT), which covers every proxy-aware tool (claude, `git` over HTTPS, npm/pip/apt). Tools
that bypass the proxy and resolve DNS directly (e.g. `ssh`-based git) are not reachable under lock by
design; a `dnsmasq` forwarder for direct-DNS support and an in-container `iptables` default-DROP
defence-in-depth layer (its `NET_ADMIN` phase would run as a separate pre-start init) remain future
work.

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
worker tailing `docker events`. The **Repos column** is the live git-state summary (`3 ✓`, `2 ✓ client:~↑1`,
`1 uncloned`) from a separate **8 s fetch-less gitstate worker** (`checkout/gitstate.py`, off the UI
thread, cached between scans — host-FS state, distinct from the never-cached container liveness); a
**repo-detail panel** below the table lists the cursor project's repos (Dir · Branch · State · ↑/↓ ·
Last commit), repainted on cursor-move from the same cache. Bindings: `n` new · `a` add-repo ·
`R` remove-repo · `e` env-mounts · `enter` shell · `c` claude · `l` logs · `s` start/stop · `u` usage ·
`g` refresh-git (fetch-ful) · `y` sync-review · `d` delete · `r` recreate · `,` settings (ssh keys +
git identity; `g` there opens the git-identity edit modal). Add/Remove-repo are modal
screens (`tui/screens/{add_repo,remove_repo}.py`) whose clone/registry work runs off the UI thread via
`lifecycle.add_repo`/`remove_repo` (the `_busy` reserve + per-slug `flock` guard concurrent edits).
`e` opens an **env-mounts manager** (`tui/screens/env_mounts.py` + `add_mount.py`): a modal listing the
project's ssh/file mounts with in-screen add (validated against the dest-denylist) / remove / resync —
the fast registry mutations run inline, `resync` (docker exec) on a thread worker.

- **Logs:** a `RichLog` fed by a worker running `docker logs -f --tail 200 --timestamps`; the
  follower is reaped on container switch and app shutdown.
- **Terminal spawn** (`tui/terminals.py`): a **separate OS window** (not `suspend()`), launched
  detached via `Popen(..., start_new_session=True)`. `ghostty` preferred, `alacritty` fallback, with
  `--class=claude-man-<slug>` so a Hyprland `windowrulev2` can place it:
  `ghostty --class=claude-man-<slug> -e docker exec -it -w <launch_workdir> claude-man-<slug> {bash|claude}`.
  `claude`/shell open in the project's **`launch_workdir`** (`Project.launch_workdir`): an explicit
  `[project] workdir`, else a **lone repo's checkout dir** (so a single-repo project drops you straight
  into it), else `/workspace`.

## Open risks

See [`docs/SECURITY.md`](SECURITY.md) for the threat model. Top risks: plaintext long-lived tokens
that can't self-refresh (mitigate with `0600` storage + expiry warnings + treat 401 as re-mint); the
fully hardened profile surfacing an undocumented write path (mitigate with the `image smoke` gate +
reactive writable mounts); strict-egress DNS under `internal: true` (mitigate with the dnsmasq
sidecar → `172.17.0.1`); and sync-back leaking secrets or clobbering host hooks (mitigate with the
before-read denylist, field-patch, pre-merge backups, and deletions-default-reject).
