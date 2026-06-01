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

ARG CLAUDE_VERSION=2.1.159

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

# Bake the pinned native claude binary to a read-only system path, then relocate it
# off the per-user install dir so the runtime rootfs can be read-only.
# NOTE: the installer's layout is validated by `image smoke`; pin + re-verify per bump.
RUN set -eux; \
    curl -fsSL https://claude.ai/install.sh -o /tmp/install.sh; \
    HOME=/root bash /tmp/install.sh "${CLAUDE_VERSION}"; \
    install -d /usr/local/share/claude; \
    cp -a /root/.local/bin/claude /usr/local/bin/claude; \
    chmod -R a+rX /usr/local/bin/claude /usr/local/share/claude; \
    rm -f /tmp/install.sh; \
    /usr/local/bin/claude --version

# Baked environment (also set explicitly at run time by docker/runner.py).
ENV HOME=/home/agent \
    CLAUDE_CONFIG_DIR=/home/agent/.claude \
    XDG_CACHE_HOME=/home/agent/.cache \
    USE_BUILTIN_RIPGREP=0 \
    DISABLE_AUTOUPDATER=1

LABEL claude-man.image="base" \
      claude-man.claude-version="${CLAUDE_VERSION}"

USER agent
WORKDIR /workspace

# Long-lived: the container idles; shells and `claude` are opened via `docker exec`.
CMD ["sleep", "infinity"]
