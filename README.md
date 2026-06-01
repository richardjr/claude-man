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

> Status: **early scaffold.** The foundations (XDG layout, container-label model, the hardened
> `docker create` argv renderer, the sync-back denylist, the base image, config templates) are
> implemented and tested; per-phase feature logic lands phase-by-phase per [`ROADMAP.md`](ROADMAP.md).

## How it fits together

```
~/.config/claude-man/         durable, secret-free definitions (git-versionable)
  profiles/<name>.toml          one account identity (display name, email, default flag)
  projects/<slug>.toml          a project EXISTS iff this file exists

~/.local/state/claude-man/    durable runtime state (some secret; never committed)
  profiles/<name>/token         0600 long-lived OAuth token (from `claude setup-token`)
  projects/<slug>/workspace/    the checked-out repos  ->  bind /workspace
  projects/<slug>/claude-config/ per-project CLAUDE_CONFIG_DIR  ->  bind /home/agent/.claude
  sync-audit/                   git repo: per-session commit of accepted sync-back

docker                        the live status oracle
  one named container per project: claude-man-<slug>, labelled claude-man.*
```

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
