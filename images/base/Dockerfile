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

# Base tooling. ripgrep is installed from a read-only system path on purpose.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl git ripgrep nodejs npm; \
    rm -rf /var/lib/apt/lists/*

# Non-root user with a real passwd entry and a baked home dir (owned 1000:1000).
RUN set -eux; \
    groupadd -g 1000 agent; \
    useradd  -u 1000 -g 1000 -m -d /home/agent -s /bin/bash agent

# Bake the pinned native claude to a read-only system path reachable by uid 1000.
#
# The installer drops the real ELF at $HOME/.local/share/claude/versions/<v> and a SYMLINK at
# $HOME/.local/bin/claude. We must relocate the *binary tree* (not the symlink) off /root — which
# is mode 0700 and unreachable by the agent under --user 1000:1000 --read-only — then drop the
# root-only install dir entirely. (Review IMG-1: the old `cp -a <symlink>` stranded the ELF under
# /root and the build-time --version check passed only because it ran as root.)
#
# install.sh always bootstraps "latest", then forwards the positional arg to `claude install
# <target>` to pin (review IMG-6) — so we assert the *resolved* version below, as the agent user.
RUN set -eux; \
    curl -fsSL https://claude.ai/install.sh -o /tmp/install.sh; \
    HOME=/root bash /tmp/install.sh "${CLAUDE_VERSION}"; \
    real="$(readlink -f /root/.local/bin/claude)"; \
    rel="${real#/root/.local/share/claude/}"; \
    cp -a /root/.local/share/claude /usr/local/share/claude; \
    ln -sfn "/usr/local/share/claude/${rel}" /usr/local/bin/claude; \
    chmod -R a+rX /usr/local/share/claude; \
    chmod a+rx /usr/local/bin/claude; \
    rm -rf /root/.local /root/.cache /root/.claude /tmp/install.sh; \
    ver="$(runuser -u agent -- env HOME=/home/agent /usr/local/bin/claude --version)"; \
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
    USE_BUILTIN_RIPGREP=0 \
    DISABLE_AUTOUPDATER=1

LABEL claude-man.image="base" \
      claude-man.claude-version="${CLAUDE_VERSION}"

USER agent
WORKDIR /workspace

# Long-lived: the container idles; shells and `claude` are opened via `docker exec`.
CMD ["sleep", "infinity"]
