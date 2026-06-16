# claude-man overlay: python-node — a polyglot toolchain (python3 + uv AND corepack yarn/pnpm) on
# the base image. The base already ships node + npm; this overlay adds the python toolchain and
# activates the yarn/pnpm package managers, so a node project that also needs python/pip lives in one
# image. Project python deps belong in a venv under /workspace (use `uv` — the read-only rootfs makes
# `pip install --user` / system installs fail; pip/uv caches AND uv's downloaded interpreters + tool
# venvs are redirected to /workspace, see runner._BAKED_ENV / config.CONTAINER_UV_*).
# Build:  claudemanctl image build python-node   (tags claude-man:python-node)

FROM claude-man:base

USER root
# python toolchain (mirrors images/overlays/python.Dockerfile): python3 + venv + uv on a read-only path.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3 python3-venv; \
    rm -rf /var/lib/apt/lists/*; \
    # uv to a read-only system path
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh; \
    chmod a+rx /usr/local/bin/uv /usr/local/bin/uvx
# node package managers (mirrors images/overlays/node.Dockerfile): corepack -> yarn/pnpm.
RUN set -eux; \
    corepack enable; \
    corepack prepare yarn@stable --activate; \
    corepack prepare pnpm@latest --activate

LABEL claude-man.overlay="python-node"
USER agent
