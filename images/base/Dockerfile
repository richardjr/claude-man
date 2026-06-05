# claude-man base image — debian-slim + node + pinned native claude + non-root uid 1000.
#
# Design constraints (see docs/ARCHITECTURE.md / docs/SECURITY.md):
#   * glibc base (NOT alpine/musl): `claude` is a glibc native ELF.
#   * A REAL /etc/passwd entry + baked HOME for uid 1000 — without it, getpwuid()
#     fails under `--read-only --user` and HOME resolves to "/".
#   * `claude` baked to a read-only system path (NOT npm-installed at runtime) so the
#     binary dir can stay read-only and auto-update is disabled.
#   * ripgrep from apt + USE_BUILTIN_RIPGREP=0 so claude never extracts a binary to temp.
#
# Validate every build with `claudemanctl image smoke base` before trusting it.

FROM debian:trixie-slim

ARG CLAUDE_VERSION=2.1.160
ARG GH_VERSION=2.93.0

# Base tooling. ripgrep is installed from a read-only system path on purpose.
# openssh-client provides `ssh` for in-container git-over-ssh when a project adds an `ssh` env-mount
# (agent-forwarded — no keys baked or stored here).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl git ripgrep nodejs npm openssh-client; \
    rm -rf /var/lib/apt/lists/*

# GitHub CLI (`gh`) — not in Debian's repos, so install the pinned upstream .deb (arch-aware). Gives
# every container `gh` for PRs/issues; auth is the operator's (GH_TOKEN env, or `gh auth login` into the
# writable GH_CONFIG_DIR below).
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    curl -fsSL -o /tmp/gh.deb \
        "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${arch}.deb"; \
    apt-get update; \
    apt-get install -y --no-install-recommends /tmp/gh.deb; \
    rm -rf /tmp/gh.deb /var/lib/apt/lists/*; \
    gh --version

# Non-root user with a real passwd entry and a baked home dir (owned 1000:1000).
RUN set -eux; \
    groupadd -g 1000 agent; \
    useradd  -u 1000 -g 1000 -m -d /home/agent -s /bin/bash agent

# Install the pinned native claude into the AGENT's own home (~/.local) — the exact location the
# native installer and `claude doctor` expect. Running the installer AS the agent user means the
# whole tree (~/.local/bin/claude symlink + ~/.local/share/claude/versions/<v> ELF) is agent-owned
# and reachable by uid 1000 under --read-only --user, installMethod:native is honest, and `doctor`
# is clean (no "native install missing" / "leftover npm at /usr/local/bin" warnings). Auto-update
# is disabled so a read-only ~/.local is fine. (Review IMG-1: the earlier relocate-to-/usr/local
# left the ELF unreachable under root-only /root and triggered the doctor warnings.)
#
# install.sh bootstraps "latest" then forwards the positional arg to `claude install <target>` to
# pin (review IMG-6) — so we assert the *resolved* version, as the agent user.
RUN set -eux; \
    curl -fsSL https://claude.ai/install.sh -o /tmp/install.sh; \
    runuser -u agent -- env HOME=/home/agent bash /tmp/install.sh "${CLAUDE_VERSION}"; \
    rm -rf /tmp/install.sh /home/agent/.claude /home/agent/.cache; \
    ver="$(runuser -u agent -- env HOME=/home/agent PATH=/home/agent/.local/bin:/usr/bin:/bin claude --version)"; \
    echo "claude (as agent uid 1000): ${ver}"; \
    case "${ver}" in "${CLAUDE_VERSION}"*) : ;; \
        *) echo "FATAL: resolved claude ${ver} != pinned ${CLAUDE_VERSION}" >&2; exit 1 ;; esac

# Baked environment (also set explicitly at run time by docker/runner.py).
# XDG_STATE_HOME points at the writable .cache tmpfs so claude's version-lock dir
# (state/claude/locks) doesn't land on the read-only rootfs (review IMG-2). Non-persistent
# is fine — the lock is per-session and self-recreates.
ENV HOME=/home/agent \
    CLAUDE_CONFIG_DIR=/home/agent/.claude \
    XDG_CACHE_HOME=/home/agent/.cache \
    XDG_STATE_HOME=/home/agent/.cache/state \
    GIT_CONFIG_GLOBAL=/home/agent/.cache/gitconfig \
    GH_CONFIG_DIR=/home/agent/.cache/gh \
    PATH=/home/agent/.local/bin:$PATH \
    USE_BUILTIN_RIPGREP=0 \
    DISABLE_AUTOUPDATER=1

LABEL claude-man.image="base" \
      claude-man.claude-version="${CLAUDE_VERSION}"

USER agent
WORKDIR /workspace

# Long-lived: the container idles; shells and `claude` are opened via `docker exec`.
CMD ["sleep", "infinity"]
