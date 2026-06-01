# claude-man overlay: rust — adds a system-wide rustup toolchain on top of the base image.
# Build:  claudemanctl image build rust   (tags claude-man:rust)
#
# CARGO_HOME/RUSTUP_HOME live under a read-only system path so the toolchain survives the
# read-only rootfs; per-project build output goes to the writable /workspace bind.

FROM claude-man:base

USER root
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential; \
    rm -rf /var/lib/apt/lists/*; \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --no-modify-path --profile minimal; \
    chmod -R a+rX /usr/local/rustup /usr/local/cargo

LABEL claude-man.overlay="rust"
USER agent
