# claude-man overlay: node — extra node toolchain (corepack -> yarn/pnpm) on the base image.
# Build:  claudemanctl image build node   (tags claude-man:node)

FROM claude-man:base

USER root
RUN set -eux; \
    corepack enable; \
    corepack prepare yarn@stable --activate; \
    corepack prepare pnpm@latest --activate

LABEL claude-man.overlay="node"
USER agent
