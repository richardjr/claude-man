# claude-man

A Python **Textual TUI** + a scriptable **`claudemanctl`** CLI that provisions, persists, and
manages **hardened Docker containers**, each running **Claude Code** under a chosen **account
profile** (e.g. work / home), for a set of long-lived **git-checkout projects** on a single
host.

It exists to solve four things at once:

1. **Multiple accounts** — launch each Claude instance under a chosen profile. A profile is one
   OAuth identity minted once with `claude setup-token` and injected per-launch as
   `CLAUDE_CODE_OAUTH_TOKEN`. Each project picks a profile (or inherits the default).
2. **Persistent projects** — a project is a named set of git repos checked out once and kept
   across container restarts and host reboots **until you explicitly delete it**. The manager
   shows the live state, profile, egress mode, and version of every project.
3. **Secure sandbox** — every project runs in its own hardened container (read-only rootfs,
   all capabilities dropped, no-new-privileges, non-root, pid-limited), loadable with project
   environment variables and extra software via image overlays. Egress is open by default and
   **lockable** to a strict per-project allowlist.
4. **Config sync-back** — when you close a session, changes the agent made to its Claude config
   (agents, skills, slash-commands, `settings.json`, MCP servers, memory, `CLAUDE.md`) are
   diffed against a baseline and offered back to your host config behind an **accept/reject
   gate**. Credentials and identity are **never** synced.

> Status: **Phases 0–2 + most of Phase 3 working** (2026-06-05). Mint work/home profiles; create /
> start / stop / shell / run Claude in hardened containers under a chosen account; switch accounts
> (mismatch-guarded); watch per-account token usage; add / remove / inspect a project's git repos with
> live state; and mount ssh (agent-forward) + host files into a container — all from both the CLI and
> the TUI. Phase 3 remainder (`project delete`, version-bump), Phase 4 (strict egress) and Phase 5
> (sync-back) are next — see [`ROADMAP.md`](ROADMAP.md). 143 dependency-free tests; the hardened image
> is `image smoke`-gated.

## How it fits together

```
~/.config/claude-man/         durable, secret-free definitions (git-versionable)
  profiles/<name>.toml          one account identity (display name, email, default flag)
  projects/<slug>.toml          a project EXISTS iff this file exists

~/.local/state/claude-man/    durable runtime state (some secret; never committed)
  profiles/<name>/token         0600 long-lived OAuth token (from `claude setup-token`)
  profiles/<name>/identity.json scrubbed oauthAccount block (no UUIDs)
  profiles/<name>/seed/         allowlisted ~/.claude assets new projects inherit
  projects/<slug>/workspace/    the checked-out repos  ->  bind /workspace
  projects/<slug>/claude-config/ per-project CLAUDE_CONFIG_DIR  ->  bind /home/agent/.claude
  sync-audit/                   git repo: per-session commit of accepted sync-back

docker                        the live status oracle
  one named container per project: claude-man-<slug>, labelled claude-man.*
```

Both roots default to `$XDG_CONFIG_HOME` / `$XDG_STATE_HOME` (falling back to `~/.config` /
`~/.local/state`), and can be relocated wholesale with the `CLAUDE_MAN_CONFIG_HOME` /
`CLAUDE_MAN_STATE_HOME` env overrides — the test suite points these at a tmpdir so it never touches
real operator state.

The TOML registry answers *what a project is*; `docker ps` (queried fresh, never cached) answers
*what state its container is in right now*. The two never describe the same fact, so they can't
drift — on any divergence the **registry wins** and the container is recreated.

## Quick start (dev)

```bash
uv sync                       # install textual + tomlkit into .venv
uv run claudemanctl --help    # the CLI surface
uv run claudeman              # launch the TUI
uv run python -m unittest     # run the (dependency-free) unit tests

# Build + smoke-test the hardened base image (needs docker + a claude install)
uv run claudemanctl image build base
uv run claudemanctl image smoke base

# Mint an account profile, then create + run a project under it
uv run claudemanctl profile add home --default     # completes `claude setup-token` (browser flow)
uv run claudemanctl project create demo            # write registry + seed config + create container
uv run claudemanctl project up demo                # start it
uv run claudemanctl project shell demo             # open a shell inside (or `project claude demo`)
uv run claudemanctl profile usage                  # per-account token usage
```

## Setting up accounts (profiles)

A **profile** is one Claude account identity, minted once on the host with `claude setup-token`
and injected per-launch as `CLAUDE_CODE_OAUTH_TOKEN`. claude-man never copies `.credentials.json`
and never sets `ANTHROPIC_*` keys (see invariant 1 in [`CLAUDE.md`](CLAUDE.md)).

```bash
# A personal subscription account, made the default for new projects:
uv run claudemanctl profile add home --default

# A WORK account behind SSO — runs `claude auth login --sso` first to point the
# host session at the right seat, then mints the token for THAT account:
uv run claudemanctl profile add work --sso --email you@company.com

# Give it a friendlier label shown in the TUI / `profile list`:
uv run claudemanctl profile add work --sso --display-name "Work (ACME SSO)"

# Other login front-ends, used instead of --sso:
uv run claudemanctl profile add work --login     # plain `claude auth login`
uv run claudemanctl profile add api  --console   # Anthropic Console (API billing)
```

`--sso`, `--login`, and `--console` all run `claude auth login` **before** `claude setup-token`,
so the token is minted for the account you just signed into:

| Flag | Effect |
|---|---|
| `--sso` | `claude auth login --sso` — sign in via your org's SSO seat first |
| `--login` | plain `claude auth login` first |
| `--console` | `claude auth login --console` — Anthropic Console (API billing) |
| `--email <addr>` | passed to the login **and** recorded as the profile's account |
| `--default` | make this the default profile new projects inherit |
| `--display-name <s>` | human-readable label for the TUI and `profile list` |

With **no** login flag, `profile add` mints against whatever account your host `claude` is already
logged into. `--email` is optional; without it the account is read back from `claude auth status`.

Managing existing profiles:

```bash
uv run claudemanctl profile list            # all profiles, default flag, token age
uv run claudemanctl profile verify work     # which account the token authenticates as (--raw for JSON)
uv run claudemanctl profile renew work      # re-mint an expired token (≈1-year life, can't self-refresh)
uv run claudemanctl profile seed work       # (re)capture the host ~/.claude seed new projects inherit
uv run claudemanctl profile usage           # per-account token usage across all projects
```

## Managing projects

```bash
# Create a project, choosing its account, image overlay, and egress mode:
uv run claudemanctl project create demo --profile work --overlay python --egress open
#   --profile <name>             account to run under   (default: the default profile)
#   --overlay base|python|rust|node   toolchain baked into the image (default: base)
#   --egress  open|strict        network policy         (default: open; strict is Phase 4)

uv run claudemanctl project up demo         # create-if-needed + start
uv run claudemanctl project status [demo]   # live state JOINed with the registry (all, or one slug)
uv run claudemanctl project stop demo       # stop the container (project + workspace are kept)
uv run claudemanctl project shell demo      # open a shell in a new terminal
uv run claudemanctl project claude demo     # run claude in a new terminal

# Switch a project to a different account (mismatch-guarded; --force to override + re-seed identity):
uv run claudemanctl project recreate demo --profile home
uv run claudemanctl project recreate demo --profile home --force
```

### Repos in a project's workspace

Repos are cloned **host-side** into `workspace/` (the gh PAT / ssh-agent never enters the container);
adding one to a live project clones into the running container's `/workspace` bind immediately — no
recreate. Live git state (branch, clean/dirty, ahead/behind, branch-vs-config drift) is read fresh per
scan.

```bash
uv run claudemanctl project repo add demo git@github.com:org/svc.git   # register + clone live
#   --branch <name>   branch to track          (default: main)
#   --dir <subdir>    workspace subdir         (default: derived from the url)
#   --no-clone        register only; a later `sync-repos` clones it
uv run claudemanctl project repo list demo     # per-repo live state table (fetch-less)
uv run claudemanctl project sync-repos demo    # clone any missing + git fetch all, then show state
uv run claudemanctl project repo rm demo svc   # drop from the registry (checkout left on disk)
uv run claudemanctl project repo rm demo svc --purge   # ...and delete the on-disk checkout
```

### Environment mounts (ssh + files)

Make host material available **inside** the container for the agent's own runtime / git-over-ssh.
`ssh` forwards your running ssh-agent (private keys stay on the host); `file` binds a host file at a
container path (read-only by default). Mounts are fixed at create, so a change needs `recreate`; the
base image needs `openssh-client` (rebuild with `image build base`).

```bash
uv run claudemanctl project env add demo ssh                          # agent-forward + ~/.ssh config/known_hosts
uv run claudemanctl project env add demo file ~/.netrc /home/agent/.netrc   # [--rw] writable
uv run claudemanctl project env add demo file ~/Work/CLAUDE.md /workspace/   # workspace-root guidance (cp-style trailing /)
uv run claudemanctl project env list demo
uv run claudemanctl project resync demo        # re-validate sources + re-seed ssh (no recreate)
uv run claudemanctl project env rm demo /home/agent/.netrc            # by container dst or 'ssh'
```

A single-repo project launches `claude`/shell **in the repo dir** by default (`docker exec -w`); set
`[project] workdir = "<subdir>"` in the TOML to override (a multi-repo project defaults to `/workspace`).

## Building images

```bash
uv run claudemanctl image build base                  # the hardened base image
uv run claudemanctl image build python                # an overlay (base|python|rust|node)
uv run claudemanctl image build base --claude-version 2.1.160   # pin the claude version
uv run claudemanctl image build base --dry-run        # print the docker build argv only, don't run
uv run claudemanctl image smoke base                  # gate an image against the hardened run profile
```

See [`CLAUDE.md`](CLAUDE.md) for the invariants any contributor (human or Claude) must keep,
[`ROADMAP.md`](ROADMAP.md) for the phase plan, [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for the full design, and [`docs/SECURITY.md`](docs/SECURITY.md) for the threat model and
hardening rationale.

## Host requirements

- Linux with Docker (rootful is fine; this was designed against Docker 29.x on Arch/Hyprland).
- Python ≥ 3.11 and [`uv`](https://docs.astral.sh/uv/).
- A terminal emulator for spawned shells — `ghostty` (preferred) or `alacritty`.
- A Claude Code install on the host to mint profile tokens (`claude setup-token`).
