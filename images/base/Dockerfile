# claude-man base image — debian-slim + node + pinned native claude + non-root uid 1000.
#
# Design constraints (see docs/ARCHITECTURE.md / docs/SECURITY.md):
#   * glibc base (NOT alpine/musl): `claude` is a glibc native ELF.
#   * A REAL /etc/passwd entry + baked HOME for uid 1000 — without it, getpwuid()
#     fails under `--read-only --user` and HOME resolves to "/".
#   * `claude` baked to a read-only system path (NOT npm-installed at runtime) so the
#     binary dir can stay read-only and auto-update is disabled. To UPDATE claude, rebuild this image
#     with a newer CLAUDE_VERSION — claude-man does this for you on start (it checks the tracked
#     channel's latest and offers a rebuild before `up`; see src/claudeman/updates.py + lifecycle.py).
#     `claude update` INSIDE a container fails by design (EROFS on the read-only ~/.local).
#   * ripgrep from apt + USE_BUILTIN_RIPGREP=0 so claude never extracts a binary to temp.
#   * neovim baked with a curated, no-plugin-manager config (TS + Markdown + git-from-nvim): plugins
#     are native packages + treesitter parsers/LSP servers are baked, so it runs read-only with no
#     runtime network. See the "Neovim" section below + images/nvim/.
#
# Validate every build with `claudemanctl image smoke base` before trusting it.

FROM debian:trixie-slim

ARG CLAUDE_VERSION=2.1.173
ARG GH_VERSION=2.93.0
# Pin Neovim to the 0.11 STABLE line, NOT 0.12. The curated plugins are baked as native packages
# pinned to exact commits (below); nvim-treesitter's `master` branch (the only branch with the classic
# `.configs`/highlight API this config uses) is FROZEN — its custom predicates/directives
# (`is?`/`kind-eq?`/`nth?`, `set-lang-from-info-string!`, `downcase!`, …) register with the legacy
# single-node match opt-out (`{all=false}`). Neovim 0.12 REMOVED that legacy mode, so on 0.12 those
# handlers receive a list-valued match and crash ("attempt to call method 'range'/'start' (a nil
# value)") the moment treesitter resolves an injection — e.g. viewing any markdown with a ```lang
# fenced code block. 0.11.x still honours `{all=false}`, so the pinned plugin set works as designed.
# (Bumping nvim-treesitter is not an option — master HEAD already IS the pinned commit; the 0.12 path
# is its `main`-branch rewrite, a separate, larger config migration.)
ARG NVIM_VERSION=0.11.7
ARG MARKSMAN_VERSION=2026-02-08

# Base tooling. ripgrep is installed from a read-only system path on purpose.
# openssh-client provides `ssh` for in-container git-over-ssh when a project adds an `ssh` env-mount
# (agent-forwarded — no keys baked or stored here). corkscrew tunnels that ssh through the squid CONNECT
# proxy on 443 under STRICT egress (issue #12): ssh ignores HTTP(S)_PROXY, so a locked project rewrites
# each forge to its SSH-over-443 endpoint and ProxyCommands through corkscrew — same allowlist as HTTPS.
# corkscrew is pure stdin/stdout (no disk writes) so it runs clean under the read-only floor (invariant 2).
#
# Shell dev environment (Phase 8a — see images/bash/ + docs/DEVENV.md): starship (git-aware prompt),
# fzf (Ctrl-R history), eza (ls), zoxide (smart cd), bat (pager/man), bash-completion — all from
# Trixie. Debian ships bat's binary as `batcat`; symlink a `bat` onto PATH so the alias + MANPAGER
# work. These only affect the interactive shell; the hardened runtime floor is unchanged (invariant 2).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl git ripgrep nodejs npm openssh-client corkscrew \
        bash-completion starship fzf eza zoxide bat; \
    ln -s /usr/bin/batcat /usr/local/bin/bat; \
    rm -rf /var/lib/apt/lists/*

# Curated forge host keys baked as OpenSSH's GLOBAL known_hosts (issue #4, fix B). With these,
# in-container `git`-over-SSH verifies github/gitlab/bitbucket/azure WITHOUT a prompt regardless of the
# host machine's SSH history, and the trust survives every `recreate` (the ~/.ssh tmpfs is ephemeral;
# this read-only file is not). PUBLIC host keys only — no secret, so invariant 1 is untouched. ssh
# trusts a host whose key is in EITHER this global file OR the user's ~/.ssh/known_hosts (the latter
# seeded from the host + grown by the opt-in accept-new auto-trust). See images/base/ssh_known_hosts
# for provenance + verified fingerprints, and docs/SECURITY.md for the per-project ssh_auto_trust flag.
COPY images/base/ssh_known_hosts /etc/ssh/ssh_known_hosts
RUN chmod 0644 /etc/ssh/ssh_known_hosts

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

# Yarn Classic (v1) writes its home config to ~/.yarnrc, which is on the read-only rootfs at runtime
# (EROFS — e.g. its post-install update-check `saveHomeConfig`, or `yarn config set`). Symlink it onto
# the writable .cache tmpfs so those writes land somewhere writable. Berry uses .yarnrc.yml, unaffected.
# The target is created on first write at runtime; a dangling symlink at build is fine. (The package
# CACHE is redirected to the disk-backed /workspace bind via YARN_CACHE_FOLDER below — see runner.py.)
RUN set -eux; \
    ln -s /home/agent/.cache/yarnrc /home/agent/.yarnrc; \
    chown -h agent:agent /home/agent/.yarnrc

# --- Neovim (baked curated config: TypeScript + Markdown + git-from-nvim) ------------------------
# A reproducible, no-plugin-manager setup so it works under --read-only with NO runtime network:
#   * nvim from the official release tarball -> /opt/nvim (on PATH).
#   * LSP servers (ts_ls / marksman / jsonls) + prettier baked on PATH.
#   * curated config -> ~/.config/nvim (see images/nvim/); plugins baked as native packages
#     (pack/curated/start); treesitter parsers compiled into /opt/nvim-parsers (build-only gcc, purged).
# Runtime writes (shada/state/cache) go to the .cache tmpfs (XDG_STATE_HOME/XDG_CACHE_HOME) — no new
# writable surface, so the hardened floor (invariant 2) is unchanged.

# nvim binary (arch-aware tarball).
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in amd64) na=x86_64 ;; arm64) na=arm64 ;; *) echo "unsupported arch $arch" >&2; exit 1 ;; esac; \
    curl -fsSL -o /tmp/nvim.tar.gz \
        "https://github.com/neovim/neovim/releases/download/v${NVIM_VERSION}/nvim-linux-${na}.tar.gz"; \
    mkdir -p /opt/nvim; \
    tar -xzf /tmp/nvim.tar.gz -C /opt/nvim --strip-components=1; \
    ln -s /opt/nvim/bin/nvim /usr/local/bin/nvim; \
    rm -f /tmp/nvim.tar.gz; \
    nvim --version | head -1

# LSP servers + formatter, baked on PATH (no runtime Mason/network): ts_ls, jsonls, prettier (npm) +
# marksman (arch-aware prebuilt binary).
RUN set -eux; \
    npm install -g --no-fund --no-audit \
        typescript@6.0.3 typescript-language-server@5.3.0 \
        vscode-langservers-extracted@4.10.0 prettier@3.8.3; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in amd64) ma=x64 ;; arm64) ma=arm64 ;; *) echo "unsupported arch $arch" >&2; exit 1 ;; esac; \
    curl -fsSL -o /usr/local/bin/marksman \
        "https://github.com/artempyanykh/marksman/releases/download/${MARKSMAN_VERSION}/marksman-linux-${ma}"; \
    chmod 0755 /usr/local/bin/marksman; \
    typescript-language-server --version; \
    marksman --version

# Curated config + baked plugins (native packages, no plugin manager). Plugins are PINNED to exact
# commit SHAs (resolved 2026-06-09) for a reproducible build — `pin` shallow-fetches just that commit.
# To bump: `git ls-remote https://github.com/<repo> <branch>` and update the SHA here.
COPY images/nvim /home/agent/.config/nvim
RUN set -eux; \
    pack="/home/agent/.config/nvim/pack/curated/start"; \
    mkdir -p "$pack" /opt/nvim-parsers; \
    pin() { d="$pack/$2"; mkdir -p "$d"; git -C "$d" init -q; \
        git -C "$d" remote add origin "https://github.com/$1.git"; \
        git -C "$d" fetch -q --depth 1 origin "$3"; \
        git -C "$d" checkout -q --detach FETCH_HEAD; }; \
    pin nvim-treesitter/nvim-treesitter            nvim-treesitter      cf12346a3414fa1b06af75c79faebe7f76df080a; \
    pin neovim/nvim-lspconfig                      nvim-lspconfig       ed19590a3a9792901553c388d1aadafce012f80d; \
    pin hrsh7th/nvim-cmp                            nvim-cmp             a1d504892f2bc56c2e79b65c6faded2fd21f3eca; \
    pin hrsh7th/cmp-nvim-lsp                        cmp-nvim-lsp         cbc7b02bb99fae35cb42f514762b89b5126651ef; \
    pin hrsh7th/cmp-buffer                          cmp-buffer           b74fab3656eea9de20a9b8116afa3cfc4ec09657; \
    pin hrsh7th/cmp-path                            cmp-path             c642487086dbd9a93160e1679a1327be111cbc25; \
    pin lewis6991/gitsigns.nvim                    gitsigns.nvim        25050e4ed39e628282831d4cbecb1850454ce915; \
    pin tpope/vim-fugitive                          vim-fugitive         3b753cf8c6a4dcde6edee8827d464ba9b8c4a6f0; \
    pin stevearc/conform.nvim                      conform.nvim         619363c30309d29ffa631e67c8183f2a72caa373; \
    pin MeanderingProgrammer/render-markdown.nvim  render-markdown.nvim 5adf0895310c1904e5abfaad40a2baad7fe44a07; \
    pin tahayvr/matteblack.nvim                    matteblack.nvim      a8a039f5d6189b28d76fa0221e64965efec7e996; \
    # File explorer: neo-tree (the same left panel as the operator's local LazyVim) + its deps.
    # neo-tree/nui/plenary SHAs match the operator's lazy-lock.json (resolved 2026-06-11).
    pin nvim-neo-tree/neo-tree.nvim                neo-tree.nvim        6ba0ef540a8dc92f9533433a3f10d414014676aa; \
    pin MunifTanjim/nui.nvim                       nui.nvim             de740991c12411b663994b2860f1a4fd0937c130; \
    pin nvim-lua/plenary.nvim                      plenary.nvim         b9fd5226c2f76c951fc8ed5923d85e4de065e509; \
    pin nvim-tree/nvim-web-devicons                nvim-web-devicons    dfbfaa967a6f7ec50789bead7ef87e336c1fa63c; \
    # Start dashboard: mini.starter (zero-dep; lists recent files inline natively — the picker-driven
    # snacks dashboard the operator runs locally would be mostly dead keys here). Resolved 2026-06-11.
    pin nvim-mini/mini.starter                     mini.starter         a7d8b353cf120fee32bb5d88e7a9de5eaec746e7; \
    find "$pack" -name .git -maxdepth 2 -type d -prune -exec rm -rf {} +; \
    chown -R agent:agent /home/agent/.config

# Compile treesitter parsers into /opt/nvim-parsers (build-only toolchain, then PURGED so no compiler
# ships at runtime). Headless nvim loads the curated config, which sets parser_install_dir.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends gcc g++ make; \
    chmod 0777 /opt/nvim-parsers; \
    runuser -u agent -- env HOME=/home/agent PATH=/usr/local/bin:/usr/bin:/bin \
        nvim --headless \
        -c "TSInstallSync! typescript tsx javascript json markdown markdown_inline bash yaml python sql dockerfile html css lua diff gitcommit" \
        -c "qa" 2>&1 | tail -25 || true; \
    test -f /opt/nvim-parsers/parser/typescript.so \
        || { echo "FATAL: typescript treesitter parser not baked" >&2; exit 1; }; \
    chown -R agent:agent /opt/nvim-parsers; \
    chmod -R a+rX /opt/nvim-parsers; \
    apt-get purge -y gcc g++ make; \
    apt-get autoremove -y --purge; \
    rm -rf /var/lib/apt/lists/*; \
    echo "baked parsers:"; ls /opt/nvim-parsers/parser/ | tr '\n' ' '; echo

# --- Shell environment (curated bash: starship prompt + history search + host aliases) -----------
# Baked dotfiles sourced by the interactive `docker exec ... bash` the launcher opens (terminals.py).
# Mirrors the operator's Arch/Omarchy bash. All read-only on the rootfs; the rc's only writes
# (history, zoxide db, starship cache) land on the .cache tmpfs (XDG_CACHE_HOME). The rc bails on
# non-interactive shells FIRST, so the non-interactive exec probes are untouched. See images/bash/
# + docs/DEVENV.md (Phase 8a). The .config dir already exists (agent-owned) from the nvim COPY above.
COPY --chown=agent:agent images/bash/bashrc        /home/agent/.bashrc
COPY --chown=agent:agent images/bash/inputrc       /home/agent/.inputrc
COPY --chown=agent:agent images/bash/starship.toml /home/agent/.config/starship.toml
# The dev-shell banner (Phase 8c): the funky boot-splash wordmark + a cheat-sheet, printed once per
# container by the rc and re-shown via `hints`. On PATH, root-owned + read-only like every other tool.
COPY images/bash/motd /usr/local/bin/claude-man-motd
RUN chmod 0755 /usr/local/bin/claude-man-motd

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
    YARN_GLOBAL_FOLDER=/home/agent/.cache/yarn \
    YARN_ENABLE_GLOBAL_CACHE=false \
    YARN_ENABLE_MIRROR=false \
    YARN_CACHE_FOLDER=/workspace/.yarn-cache \
    PATH=/home/agent/.local/bin:$PATH \
    USE_BUILTIN_RIPGREP=0 \
    DISABLE_AUTOUPDATER=1 \
    EDITOR=nvim \
    VISUAL=nvim \
    BAT_THEME=ansi \
    MANROFFOPT=-c \
    MANPAGER="sh -c 'col -bx | bat -l man -p'" \
    STARSHIP_CONFIG=/home/agent/.config/starship.toml \
    STARSHIP_CACHE=/home/agent/.cache/starship \
    _ZO_DATA_DIR=/home/agent/.cache/zoxide

LABEL claude-man.image="base" \
      claude-man.claude-version="${CLAUDE_VERSION}"

USER agent
WORKDIR /workspace

# Long-lived: the container idles; shells and `claude` are opened via `docker exec`.
CMD ["sleep", "infinity"]
