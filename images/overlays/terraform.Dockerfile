# claude-man overlay: terraform — HashiCorp infra toolchain (Terraform + Packer) on the base image.
# For the infrastructure/ repo (AWS + MongoDB Atlas via Terraform; AMIs via Packer). Bundling both in
# one overlay is the natural unit: same vendor, same project, identical read-only-floor + egress
# concerns (cf. the python-node combo).
#
# Both binaries are pinned release zips from releases.hashicorp.com (arch-aware), unzipped to a
# read-only system path — the same baked-tool pattern as nvim/marksman/gh in the base image. `unzip`
# is a build-only dependency, purged after extraction (mirrors the base's purge-the-build-toolchain
# discipline) so no extra binary ships at runtime.
#
# Read-only-floor notes (invariant 2 — see CLAUDE.md "When adding or changing an overlay"):
#   * terraform writes its working state to the CWD — .terraform/, .terraform.lock.hcl,
#     terraform.tfstate — which is /workspace (the writable bind), so `terraform init/plan` work
#     as-is. No shared plugin cache is configured (TF_PLUGIN_CACHE_DIR must pre-exist, a footgun);
#     providers cache per-workdir under /workspace instead.
#   * CHECKPOINT_DISABLE=1 stops both tools' version-check telemetry writing to ~/.terraform.d /
#     ~/.config on the read-only HOME (best-effort, but would otherwise EROFS noisily).
#   * packer installs plugins to PACKER_PLUGIN_PATH; its default (~/.config/packer) is on the
#     read-only rootfs, so PACKER_CONFIG_DIR/PACKER_PLUGIN_PATH are redirected onto the writable
#     /workspace bind (packer creates the dir on `packer init`). Verified end-to-end, as uid 1000
#     under --read-only, by `claudemanctl image smoke terraform` (smoke._overlay_probes).
#   These env vars are scoped to this overlay (like the rust overlay's CARGO_HOME/RUSTUP_HOME), so the
#   global hardened floor / _BAKED_ENV is untouched.
#
# Egress (invariant 3): a LOCKED terraform project must add `registry.terraform.io` +
# `releases.hashicorp.com` (provider/module fetch) to its [project.egress].allowlist, plus its own
# cloud targets (e.g. .amazonaws.com, cloud.mongodb.com). GitHub-hosted providers/plugins are already
# covered by the base .github.com/.githubusercontent.com wildcards. The base allowlist is left lean
# (these hosts serve one project, not all). The TUI Egress screen can promote a blocked host too.
#
# Build:  claudemanctl image build terraform   (tags claude-man:terraform)

FROM claude-man:base

USER root

# Versions resolved from HashiCorp's checkpoint API (2026-06-23). To bump: re-resolve the latest
# stable and update both ARGs, then rebuild + `image smoke terraform`.
ARG TERRAFORM_VERSION=1.15.6
ARG PACKER_VERSION=1.15.4

# Set the floor redirects BEFORE the RUN so the build-time `terraform version` / `packer version`
# also skip the checkpoint network call + telemetry write.
ENV CHECKPOINT_DISABLE=1 \
    PACKER_CONFIG_DIR=/workspace/.packer \
    PACKER_PLUGIN_PATH=/workspace/.packer/plugins

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends unzip; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in amd64) ha=amd64 ;; arm64) ha=arm64 ;; *) echo "unsupported arch $arch" >&2; exit 1 ;; esac; \
    curl -fsSL -o /tmp/terraform.zip \
        "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${ha}.zip"; \
    curl -fsSL -o /tmp/packer.zip \
        "https://releases.hashicorp.com/packer/${PACKER_VERSION}/packer_${PACKER_VERSION}_linux_${ha}.zip"; \
    unzip -o /tmp/terraform.zip terraform -d /usr/local/bin; \
    unzip -o /tmp/packer.zip   packer   -d /usr/local/bin; \
    chmod 0755 /usr/local/bin/terraform /usr/local/bin/packer; \
    rm -f /tmp/terraform.zip /tmp/packer.zip; \
    apt-get purge -y unzip; \
    apt-get autoremove -y --purge; \
    rm -rf /var/lib/apt/lists/*; \
    terraform version; \
    packer version

LABEL claude-man.overlay="terraform"
USER agent
