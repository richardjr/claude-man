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

## Persistence + container lifecycle

- **Definition:** `projects/<slug>.toml` — slug, profile, overlay (image variant), egress mode,
  `language` + `packs` (the curated-pack selection — see *Curated packs* below), `[env]` (or
  `env_file`), `extra_apt`, and a `[[repos]]` array.
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
`rust` (rustup), `node` (extra node), `python-node` (a polyglot combo — python+uv *and* the node
package managers in one image, for a node project that also needs python/pip; project python deps go
in a `.venv` under `/workspace`, not the read-only rootfs), and `terraform` (the infra
toolchain — pinned `terraform` + `packer` + the AWS CLI v2 for the `infrastructure/` repo; working
state writes ride the `/workspace` bind and `CHECKPOINT_DISABLE`/`PACKER_*`-dir/`AWS_CONFIG_FILE` +
`AWS_SHARED_CREDENTIALS_FILE` redirects keep tool writes off the read-only HOME — the AWS config/creds
files land on the ephemeral `.cache` tmpfs, off the `/workspace` git checkout, so a creds file never
reaches a repo; the preferred way to pass AWS creds is env vars via a `kind="env"` env-mount. A
*locked* terraform project must allowlist `registry.terraform.io` + `releases.hashicorp.com`
plus its own cloud targets, e.g. `.amazonaws.com`). Project-specific lightweight packages come from
`project.toml`'s `extra_apt = [...]`, baked into a thin per-project layer at create time. Project
**env vars are injected at run time** (declared `project.env` as `-e KEY=VAL`; any `env_file` is
parsed and `ANTHROPIC_*`-scrubbed host-side, then injected pass-through as `-e KEY` name-only with
the value supplied via the subprocess env — docker is never given `--env-file`, which would bypass
the scrub, per review SEC-2 / invariant 1), never baked, so secrets never enter an image layer.

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
  --memory 16g --memory-swap 16g   (Settings.container_memory — always present; `config memory`) \
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
  sleep infinity        # long-lived; shell/claude/nvim opened via `docker exec` from the TUI
```

**Writable surfaces (everything else is read-only):** `/home/agent/.claude` (persistent bind —
the sync-back surface), `/workspace` (persistent bind), `/tmp` and `/home/agent/.cache` (tmpfs,
`exec`), and — **only when a project declares an `ssh` env-mount** — a `0700` `/home/agent/.ssh`
tmpfs. `--pids-limit` is **1024** (not small): claude forks Bash, ripgrep, MCP servers, hooks — a
low limit silently breaks parallel tool calls. `no-new-privileges` + `--cap-drop ALL` is exactly
why the firewall is a network-layer sidecar.

**Memory cap (issue #29).** `--memory X --memory-swap X` is **always rendered** beside the fixed
hardening flags — the only part of the floor whose *value* is operator-chosen (`[container] memory`,
default `16g`, minimum `1g`; `runner._render_memory`, validated by the pure
`config.normalise_memory_limit`). Equal `--memory-swap` means **no swap** for the container — a true
ceiling, so a runaway can neither starve the host of RAM nor thrash its swap/zram. The kernel then
OOM-kills *inside the container's cgroup* (the biggest process there — the runaway) and the host never
sees pressure. Before this, a 30 GB in-container `node` put the host under `global_oom` and Chrome
died first. Fixed at create → recreate to apply.

**The scratch / data-transfer dir (`/workspace/scratch`).** A known drop-zone for copying files in
and out of a container. It is a **subdir of the existing `/workspace` bind — not a new mount**, so
the hardened floor is byte-identical (invariant 2 holds; `scratch.py` touches no `docker create`
argv). The lifecycle **wipes it on every container start and stop** (`scratch.clear`, containment-
checked against the workspace bind, best-effort — a clear fault never blocks a start/stop), so it
never persists across sessions: stage inputs there while the container runs, collect outputs before
stop. `scratch.ensure_note` stamps a small claude-man-owned managed block into `/workspace/CLAUDE.md`
on start (via the shared `claudemd.patch_block`, in place — it coexists with the packs block and
preserves operator content) telling the agent to look there for "provided files". A repo whose dir
would land under `scratch/` is refused at `project repo add` (it would be wiped).

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
`Settings()`). It currently carries `[ssh] keys`/`auto_load` (host keys to forward), `[git]
user_name`/`user_email` (the identity override above; `set_git_identity` clears it with empty strings),
`[image] image_update_check`/`claude_channel`/`claude_version_pin` (the on-start "newer claude?" check
+ the tracked release channel/pin), `[terminal] terminal_program`/`terminal_command` (the emulator that
opens detached shell/claude/nvim windows), `[opener] opener_command` (the file-manager command for
Browse), and `[ui] ui_splash` (the boot splash toggle). All stay non-secret — note the GitHub token
surfaced via `config gh-token` is deliberately **not** a `config.toml` section: it lives `0600` in the
state tier (never synced; invariant 1). The TUI opens a Settings screen with `,` (managing ssh keys plus
`g` git identity, `t` GH token, and `e` terminal launcher; the git identity is edited via a
`GitIdentityScreen` where a blank field means *inherit host*); the CLI surface is `claudemanctl config
show` plus the `config git`, `gh-token`, `terminal`, `opener`, `splash`, `image`, and `ssh` verbs.

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

## Curated packs (skills + CLAUDE.md injectors — Phase 6, implemented)

Full design: [`PACKS.md`](PACKS.md). The short version of the delivery rail it rides on: every
project has an **asset source** at `~/.config/claude-man/assets/<slug>/` (config tier, secret-free,
git-versionable) holding its `CLAUDE.md` + skills/agents/commands. `assets.sync_in` copies it into
the live binds on every start (asset wins, backup-before-overwrite, claude-side default-deny
allowlist, symlink containment) and `sync_out` carries bind-side edits back on stop. Packs are a
**producer into that source** — container delivery needs no new mount, so the hardened floor is
byte-identical (invariant 2).

- **The library lives in this repo** (`library/packs/<tier>/<pack>/`): tiers are `common/` plus
  per-language dirs **discovered from the layout** (adding a `typescript/` tier is just a
  directory). A **pack is a bundle** — `pack.toml` (description, `default` flag) plus any mix of
  `claude-md/*.md` fragments and `skills/<name>/` dirs that travel together; selection operates at
  pack granularity. Pack names are library-unique (a lint test imports the real tree), so a
  project's stored selection stays a flat list. Freshness identity is a **content hash** —
  curation is "edit the file, commit", no version bumps. `packs/library.py` is the pure read side.
- **Selection is explicit registry state**: `Project.language` (an explicit field, never inferred
  from the overlay) and `Project.packs`. Defaults — every `default = true` pack in `common/` +
  `<language>/` — are resolved **once at create** and written into the TOML, so a new library
  default never creeps into existing projects; `project packs defaults` re-applies on demand.
- **Materialization** (`packs/materialize.py::refresh`, run before `sync_in` on every `up` of a
  project with packs selected, and immediately by `lifecycle.set_packs` on any selection change —
  including deselecting the last pack): writes missing/stale copies into
  the asset source, patches a **fenced block of `@`-import lines** into the workspace `CLAUDE.md`
  (operator content outside the block is never touched — the settings.json field-patch
  philosophy), and records every managed path + hash in a state-tier **manifest**
  (`packs-manifest.json`). The manifest is the ours/theirs boundary: un-manifested files are
  operator-owned and win collisions; a drifted managed copy is **curated-wins** (backed up, then
  re-stamped from the library); deselection removes exactly the manifested paths — from the asset
  source **and** the binds, since `sync_in` merges and never propagates deletions. Failure is
  soft throughout: a broken or unreadable library skips with a note and never blocks a start or
  create.
- **Because the binds are live host dirs, a selection change applies to a running container
  immediately** (claude reads it at its next session launch) — packs need no recreate, ever.
- **Surfaces**: CLI `packs list`, `project packs add|rm|list|defaults`, `project create
  --language`; TUI Project… → **Packs…** — a checklist grouped Common / `<language>` with a
  **State** column (`tui/packsview.py::pack_states`, a read-only freshness probe: stale /
  drifted / operator-collision / not-in-library) and a re-apply-defaults action. The create modal
  has a Language field pre-filled from the Overlay choice.
- Relatedly, `launch_workdir` now defaults to **`/workspace` always** (the injected CLAUDE.md is
  what you see where you land; multi- and single-repo projects behave identically); an explicit
  `[project] workdir` still wins.

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
  MITM, no CA install** (HTTPS stays end-to-end); `http_access deny all` otherwise. squid logs every
  request — allowed and denied — to stdout, so `project egress-log` surfaces the **blocked**
  destinations for allowlist tuning, and the TUI's always-on **Network panel** shows per-project
  blocked/allowed **counts** (via the pure `egress.parse_access`/`summarize_access` parsers over the
  same access log) alongside Traffic. The counts reflect completed connections — a CONNECT tunnel is
  logged only at close, so in-flight HTTPS isn't counted until it ends.
- **Allowlist** (`network/allowlist.py`, rendered to squid.conf by the pure `network/squid.py`):
  `.anthropic.com` (the wildcard covers `api.anthropic.com` / `statsig.anthropic.com`),
  **`claude.ai`** (OAuth refresh — critical), `downloads.claude.ai`, `sentry.io`, the registries
  (`registry.npmjs.org`, PyPI `pypi.org` / `files.pythonhosted.org`, yarn `registry.yarnpkg.com` /
  `repo.yarnpkg.com`), Debian apt mirrors (`deb.debian.org`, `security.debian.org`), GitHub
  (`.github.com`, `.githubusercontent.com` — the wildcards cover `codeload.github.com` /
  `raw.githubusercontent.com`) + the project's `egress.allowlist[]` extras. Bare hosts already
  covered by a leading-dot wildcard are NOT listed separately (squid rejects such an overlap inside
  one `dstdomain` ACL — `build_allowlist` drops them).
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

## Sync-back (review-gated three-way merge — Phase 5)

Engine: a stdlib **three-way manifest reconcile** with the **denylist enforced before any read**.
The security-reviewed copy/backup/symlink-guard primitives are shared with the asset sync via
`syncback/fsmerge.py` (one audited implementation). Git is the audit layer only (accepted changes are
committed to `config.sync_audit_dir()` — a state-tier repo — for free revert history).

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
5. **Merge (accepted only):** under a **single GLOBAL merge lock** (`config.syncback_lock_path()` —
   the host `~/.claude` target is singular regardless of profile), back up every host target first
   (refuse the overwrite on backup failure — nothing lost) → copy file-tree artifacts through the same
   gated `fsmerge` primitive (per-entry denylist re-assert + containment; never a blind `copytree`).
   The source read is **TOCTOU-safe** against the still-running untrusted agent: the source path is
   walked component-by-component with `O_NOFOLLOW` from a bind-root anchor the agent can't reach, so a
   symlink swapped in at *any* level (leaf or intermediate dir) fails with `ELOOP` rather than
   redirecting the read to an out-of-tree operator secret (a leaf symlink to an in-tree target is still
   dereferenced, via a bounded all-`O_NOFOLLOW` re-walk) → **inverse field-patch** `settings.json` (start from the HOST dict, overlay
   only accepted keys; host `hooks` + `statusLine` structurally immune, denied keys skipped; atomic
   write) → **MCP is gate-only in v1** (detected/diffed, apply deferred — no `claude mcp` exec) →
   mirror accepted file artifacts into `config.sync_audit_dir()/<slug>/` and `git commit` (denylist
   re-asserted at staging time) → **refresh `baseline.json` from the real post-merge on-disk state**
   (partial-merge safe).

**Never synced:** credentials / OAuth tokens (env-injected — no file to sync), identity
(`oauthAccount`/`userID`/`accountUuid`), history/sessions/transcripts, statsig/caches/shell-snapshots,
host-absolute paths, and the live `.claude.json` wholesale.

## TUI

Textual app (`tui/app.py`). The **projects screen** is a `DataTable`
(Project · Status · Profile · Egress · Repos · Version · Detail) populated by an async worker running
`docker ps -a --filter label=claude-man.slug --format '{{json .}}'`, JOINed with the registry so
DEFINED projects with no container still show. A 10 s `set_interval` poll drives the refresh (a
`docker events` event-driven worker is deferred to Phase 2). The **Repos column** is the live git-state summary — an aggregate per-flag rollup (`3 ✓`, `1 ✓ 2 ⚠ 1 ~`,
`1 uncloned`) from a separate **30 s fetch-less gitstate worker** (`checkout/gitstate.py`, off the UI
thread, cached between scans — host-FS state, distinct from the never-cached container liveness); a
**repo-detail panel** below the table lists the cursor project's repos (Dir · Branch · State · ↑/↓ ·
Last commit), repainted on cursor-move from the same cache. A per-project **Network panel**
(Project · Egress · Blocked · Allowed · Traffic) is repainted on the projects-poll cycle by
`refresh_net`: Traffic is the whole-container `docker stats` NetIO (since container start — shown for
every running project, open or locked), while Blocked/Allowed are the distinct denied/permitted
destinations from the squid access log (locked projects only — open ones have no sidecar). The Traffic
figure carries a load-bearing semantic (`docker/stats.py`): for an **open** project it is real internet
RX/TX, but for a **locked** project the agent sits on the `--internal` net only, so its NetIO is the
agent↔sidecar (proxied) traffic — NOT a per-destination egress total (per-destination detail comes only
from the squid access log / `project egress-log`). It resets to zero on each recreate. The bottom
**key bar** is a two-row
`Static` (replacing the stock Footer) so the scope of every key is explicit — row 1 `project`
(acts on the cursor's row): `enter` shell · `c` claude · `e` editor (nvim) · `b` browse ·
`s` start/stop · `g` Repos… · `p` Project… · `y` sync-review; row 2 `global`: `n` new ·
`S` stop-all · `v` View… · `,` settings (ssh keys + git identity; `g` there opens the git-identity
edit modal) · `q` quit. Lower-frequency verbs live behind the g/p/v submenus
(`tui/screens/menu.py`): Repos… = add/remove-repo · refresh-git (fetch-ful) · pull-all;
Project… = env-mounts · ports · packs · egress · recreate · delete; View… = refresh-usage ·
focus-logs. Add/Remove-repo are modal
screens (`tui/screens/{add_repo,remove_repo}.py`) whose clone/registry work runs off the UI thread via
`lifecycle.add_repo`/`remove_repo` (the `_busy` reserve + per-slug `flock` guard concurrent edits).
Project… → `e` opens an **env-mounts manager** (`tui/screens/env_mounts.py` + `add_mount.py`): a modal listing the
project's ssh/file mounts with in-screen add (validated against the dest-denylist) / remove / resync —
the fast registry mutations run inline, `resync` (docker exec) on a thread worker.

Project… → `g` opens the **Egress screen** (`tui/screens/egress.py` + `add_allow.py`): the single place
to *change* the network policy the always-on Network panel reflects. It shows the mode (OPEN/STRICT) and
the project's allowlist extras on top of the base set, with: **lock/unlock** (the heavy part — it
recreates, so the screen dismisses the target mode `'strict'`/`'open'` and the app runs `lifecycle.set_egress`
off-thread, mirroring the recreate worker); **allowlist add/remove** (fast flocked registry writes via
`lifecycle.add_allow`/`remove_allow`, validated with `is_valid_dstdomain`, run inline — each reminds that a
recreate re-renders squid.conf); and **promote-blocked** (a picker over `egress.summarize_access` of the
sidecar's access log — pick a destination the proxy actually denied straight into the allowlist, the common
tuning loop). The per-destination CLI readout stays at `project egress-log`.

- **Logs:** View… → Logs opens a `LogsScreen` modal — a `RichLog` fed by a `@work(thread=True,
  exclusive, group="logs")` worker running `docker logs --tail 200 --timestamps -f` (argv from the
  pure, unit-tested `runner.build_logs_argv`), lines pushed via `self.app.call_from_thread`. The
  follower subprocess is reaped (`terminate`→`kill`) in `on_unmount`, so dismissing the screen or
  quitting the app never leaks a `docker logs -f`. Read-only — it never writes to the container.
- **Terminal spawn** (`tui/terminals.py`): a **separate OS window** (not `suspend()`), launched
  detached via `Popen(..., start_new_session=True)`. The emulator is chosen from a **settings-driven
  per-platform launcher table**: on Linux 9 built-ins (`ghostty`, `alacritty`, `kitty`, `wezterm`,
  `foot`, `ptyxis`, `gnome-terminal`, `konsole`, `xterm` — ptyxis title-only, it has no class flag;
  issue #31), on macOS `kitty`/`alacritty`/`wezterm` plus iTerm2
  and the always-present Terminal.app (via `osascript`), and on WSL2 the Linux table plus `wt.exe`.
  The `[terminal] program`/`command` settings (`config terminal`) pick a named launcher or a
  `program = "custom"` `{argv}` template (editable in the TUI via the picker's custom row); absent a
  setting, auto-detection walks the table in
  preference order (`ghostty` then `alacritty` first on Linux — the historical default). A custom
  template is availability-probed at resolve time exactly like a named launcher. `spawn` returns a
  `SpawnHandle` (Popen + a tempfile stderr capture) that every caller passes to `watch_spawn`: a
  short (`SPAWN_PROBE_S`) wait classifies the launcher as still-running / exited-0 (client-server
  terminals like gnome-terminal/ptyxis) / failed — a start-then-fail surfaces its exit code and
  stderr tail (TUI: log + error toast; CLI: non-zero exit) instead of a false success (issue #31).
  On Linux/Wayland the launcher's `--class`/`--app-id` carries `claude-man-<slug>` so a compositor
  rule (e.g. a Hyprland `windowrulev2`) can place the window. The inner command is
  `docker exec -it -w <launch_workdir> claude-man-<slug> {bash|claude|nvim}`.
  `claude`/shell/nvim open in the project's **`launch_workdir`** (`Project.launch_workdir`): an explicit
  `[project] workdir`, else **`/workspace`** (the uniform anchor since Phase 6 — the lone-repo auto-cd
  was dropped so the pack-injected workspace `CLAUDE.md` is what you land on).

## Open risks

See [`docs/SECURITY.md`](SECURITY.md) for the threat model. Top risks: plaintext long-lived tokens
that can't self-refresh (mitigate with `0600` storage + expiry warnings + treat 401 as re-mint); the
fully hardened profile surfacing an undocumented write path (mitigate with the `image smoke` gate +
reactive writable mounts); strict-egress DNS under `internal: true` (mitigate with the dnsmasq
sidecar → `172.17.0.1`); and sync-back leaking secrets or clobbering host hooks (mitigate with the
before-read denylist, field-patch, pre-merge backups, and deletions-default-reject).
