# claude-man overlay: terraform — infra toolchain (Terraform + Packer + AWS CLI) on the base image.
# For the infrastructure/ repo (AWS + MongoDB Atlas via Terraform; AMIs via Packer; ad-hoc AWS ops via
# the AWS CLI). Bundling them in one overlay is the natural unit: same project, identical
# read-only-floor + egress concerns (cf. the python-node combo).
#
# Terraform + Packer are pinned release zips from releases.hashicorp.com; the AWS CLI v2 is its pinned
# self-contained bundle from awscli.amazonaws.com (its own embedded Python — no system Python dep).
# All are arch-aware and land on a read-only system path — the same baked-tool pattern as
# nvim/marksman/gh in the base image. `unzip` is a build-only dependency (needed by all three),
# purged after extraction (mirrors the base's purge-the-build-toolchain discipline) so no extra binary
# ships at runtime.
#
# Read-only-floor notes (invariant 2 — see CLAUDE.md "When adding or changing an overlay"):
#   * terraform writes its working state to the CWD — .terraform/, .terraform.lock.hcl,
#     terraform.tfstate — which is /workspace (the writable bind), so `terraform init/plan` work
#     as-is. No shared plugin cache is configured (TF_PLUGIN_CACHE_DIR must pre-exist, a footgun);
#     providers cache per-workdir under /workspace instead.
#   * CHECKPOINT_DISABLE=1 stops both HashiCorp tools' version-check telemetry writing to
#     ~/.terraform.d / ~/.config on the read-only HOME (best-effort, but would otherwise EROFS noisily).
#   * packer installs plugins to PACKER_PLUGIN_PATH; its default (~/.config/packer) is on the
#     read-only rootfs, so PACKER_CONFIG_DIR/PACKER_PLUGIN_PATH are redirected onto the writable
#     /workspace bind (packer creates the dir on `packer init`).
#   * the AWS CLI's config + credentials default to ~/.aws on the read-only HOME, so AWS_CONFIG_FILE +
#     AWS_SHARED_CREDENTIALS_FILE are redirected onto the writable, agent-owned `.cache` tmpfs (so
#     `aws configure` doesn't EROFS) — deliberately ephemeral and OFF the /workspace git checkout so a
#     credentials file never lands in a repo. The PREFERRED way to give the container AWS creds is
#     env vars (a `kind="env"` env-mount: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY /
#     AWS_SESSION_TOKEN / AWS_REGION) — both terraform and the AWS CLI read those directly, with no
#     file write. The CLI's STS assume-role cache (~/.aws/cli/cache) and SSO cache (~/.aws/sso/cache)
#     have no redirect env and would EROFS under the floor, so role-caching / `aws sso login` are not
#     supported in-container — pass already-resolved credentials via env instead.
#   These env vars are scoped to this overlay (like the rust overlay's CARGO_HOME/RUSTUP_HOME), so the
#   global hardened floor / _BAKED_ENV is untouched. All three tools' CORE ops are verified end-to-end,
#   as uid 1000 under --read-only, by `claudemanctl image smoke terraform` (smoke._overlay_probes).
#
# Egress (invariant 3): a LOCKED terraform project must add `registry.terraform.io` +
# `releases.hashicorp.com` (provider/module fetch) to its [project.egress].allowlist, plus its own
# cloud targets (e.g. `.amazonaws.com` for the AWS CLI/provider, `cloud.mongodb.com`). GitHub-hosted
# providers/plugins are already covered by the base .github.com/.githubusercontent.com wildcards. The
# base allowlist is left lean (these hosts serve one project, not all). The TUI Egress screen can
# promote a blocked host too.
#
# Build:  claudemanctl image build terraform   (tags claude-man:terraform)

FROM claude-man:base

USER root

# Versions resolved from each vendor's release index (HashiCorp checkpoint API 2026-06-23; AWS CLI
# GitHub tags 2026-06-29). To bump: re-resolve the latest stable and update the ARGs, then rebuild +
# `image smoke terraform`.
ARG TERRAFORM_VERSION=1.15.6
ARG PACKER_VERSION=1.15.4
ARG AWSCLI_VERSION=2.35.12

# Set the floor redirects BEFORE the RUN so the build-time `terraform version` / `packer version` /
# `aws --version` also skip the checkpoint network call + telemetry write.
ENV CHECKPOINT_DISABLE=1 \
    PACKER_CONFIG_DIR=/workspace/.packer \
    PACKER_PLUGIN_PATH=/workspace/.packer/plugins \
    AWS_CONFIG_FILE=/home/agent/.cache/aws/config \
    AWS_SHARED_CREDENTIALS_FILE=/home/agent/.cache/aws/credentials

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends unzip; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) ha=amd64; aws_arch=x86_64 ;; \
        arm64) ha=arm64; aws_arch=aarch64 ;; \
        *) echo "unsupported arch $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/terraform.zip \
        "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${ha}.zip"; \
    curl -fsSL -o /tmp/packer.zip \
        "https://releases.hashicorp.com/packer/${PACKER_VERSION}/packer_${PACKER_VERSION}_linux_${ha}.zip"; \
    curl -fsSL -o /tmp/awscliv2.zip \
        "https://awscli.amazonaws.com/awscli-exe-linux-${aws_arch}-${AWSCLI_VERSION}.zip"; \
    unzip -o /tmp/terraform.zip terraform -d /usr/local/bin; \
    unzip -o /tmp/packer.zip   packer   -d /usr/local/bin; \
    unzip -q /tmp/awscliv2.zip -d /tmp; \
    /tmp/aws/install --bin-dir /usr/local/bin --install-dir /usr/local/aws-cli; \
    chmod 0755 /usr/local/bin/terraform /usr/local/bin/packer; \
    rm -rf /tmp/terraform.zip /tmp/packer.zip /tmp/awscliv2.zip /tmp/aws; \
    apt-get purge -y unzip; \
    apt-get autoremove -y --purge; \
    rm -rf /var/lib/apt/lists/*; \
    terraform version; \
    packer version; \
    aws --version

LABEL claude-man.overlay="terraform"
USER agent
