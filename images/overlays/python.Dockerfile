# claude-man overlay: python — adds python3 + uv on top of the base image.
# Build:  claudemanctl image build python   (tags claude-man:python)

FROM claude-man:base

USER root
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3 python3-venv; \
    rm -rf /var/lib/apt/lists/*; \
    # uv to a read-only system path
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh; \
    chmod a+rx /usr/local/bin/uv /usr/local/bin/uvx

LABEL claude-man.overlay="python"
USER agent
