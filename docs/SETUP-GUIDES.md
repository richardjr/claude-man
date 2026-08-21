# Setup guides — common use cases

Task-oriented recipes for getting a working project running fast. Each guide has a **CLI track** and
a **TUI track** — pick whichever you prefer; they build the same thing. These string together the
reference commands documented feature-by-feature in [`CLI.md`](CLI.md); follow the links there
when you want the full detail or all the flags.

All CLI commands run from the checkout and are shown with their `uv run` prefix; the TUI is
`uv run claudeman`. Replace `myproj`/`web`/`infra` etc. with your own slug.

- [Before you start (once per machine)](#before-you-start-once-per-machine)
- [The universal loop](#the-universal-loop) — create → up → shell, the shape every guide shares
- [Add-ons](#add-ons) — **SSH (private git)**, **AWS credentials**, env vars, a config file
- Guides: [Node / frontend](#guide-node--frontend-app) · [Python service](#guide-python-service) ·
  [Polyglot (Python + Node)](#guide-polyglot-python--node) · [Rust](#guide-rust) ·
  [**Infrastructure (Terraform + AWS)**](#guide-infrastructure-terraform--aws) ·
  [Lock a project down (strict egress)](#guide-lock-a-project-down-strict-egress) ·
  [Hybrid local model](#guide-add-a-local-model-hybrid)

Pick a guide:

| Use case | Overlay | `--language` | Guide |
|---|---|---|---|
| React / Vite / Node service | `node` | `node` | [Node / frontend](#guide-node--frontend-app) |
| Python API / scripts / data | `python` | `python` | [Python service](#guide-python-service) |
| Node app that also needs Python | `python-node` | `node` | [Polyglot](#guide-polyglot-python--node) |
| Rust crate / binary | `rust` | `rust` | [Rust](#guide-rust) |
| Terraform / Packer + AWS | `terraform` | — | [Infrastructure](#guide-infrastructure-terraform--aws) |
| Anything, network-locked | (any) | (any) | [Strict egress](#guide-lock-a-project-down-strict-egress) |

---

## Before you start (once per machine)

You need this **once**, then every guide below is a couple of commands.

**The easy way:** install ([README § Install](../README.md#install)), then launch
`uv run claudeman` — on a fresh machine the **setup wizard** checks the host (docker, the
`claude` CLI, terminal), creates your first account profile, and can build the base image, all
guided ([README § Getting started](../README.md#getting-started-tui)). `claudemanctl doctor`
re-checks the host any time.

**The CLI track** (full detail: [`CLI.md`](CLI.md)):

```bash
# 1. Install (from a git checkout — it IS the install; keep the clone around).
git clone https://github.com/richardjr/claude-man.git
cd claude-man
uv sync
uv run claudemanctl doctor                              # host prerequisites, with fix hints

# 2. Build + smoke-test the hardened base image (needs docker + network).
uv run claudemanctl image build base
uv run claudemanctl image smoke base

# 3. Mint an account profile (needs a host `claude` install; a browser flow completes `claude setup-token`).
uv run claudemanctl profile add home --default          # personal subscription, default for new projects
# work account behind SSO:  profile add work --sso --email you@company.com
```

> Building an **overlay** image (node/python/rust/python-node/terraform) is also a one-time-per-machine
> step — each guide notes which to build. Rebuild only when you bump the baked toolchain.

---

## The universal loop

Every guide is a variation on this. Substitute the overlay/language/repo for your use case.

**CLI:**

```bash
uv run claudemanctl project create myproj --overlay <overlay> --language <tier>   # write registry + seed
uv run claudemanctl project up    myproj                                          # create container + start
uv run claudemanctl project repo add myproj git@github.com:org/repo.git           # clone into /workspace
uv run claudemanctl project claude myproj                                         # open claude in a terminal
#   project shell myproj   → a shell    ·   project nvim myproj   → the baked neovim
```

**TUI** (`uv run claudeman`):

1. `n` → **New project**: type the slug, pick the **profile**, **overlay**, **language** (pack tier),
   and **egress** mode, then confirm. The container is created but **not** started — press `s`
   (opening it with **Enter**/`c` auto-starts it too).
2. `g` → `a` → **Add repo**: paste the clone URL (optionally branch / subdir); it clones live into
   `/workspace`.
3. With the project selected: **Enter** → shell · `c` → claude · `e` → neovim · `b` → open the
   workspace in your file manager.

That's it — you're running. The sections below just add the right overlay and any host material
(SSH, AWS, …) the project needs.

---

## Add-ons

Reusable building blocks several guides reference. Env mounts are **fixed at container create**, so
adding one needs a `recreate` (the CLI/TUI both remind you).

### SSH — private git, agent-forwarded

Your **private keys never enter the container**: claude-man forwards your host ssh-agent socket, so
`git clone`/`push` over `git@…` work inside the container using keys held on the host. The common
forges (github / gitlab / bitbucket / azure) are pre-trusted, so there's no host-key prompt.

**CLI:**

```bash
uv run claudemanctl config ssh add ~/.ssh/id_ed25519     # load the key into the managed host agent
uv run claudemanctl project env add myproj ssh           # forward the agent + ~/.ssh config/known_hosts
uv run claudemanctl project recreate myproj              # env mounts apply at create
# A self-hosted/unknown forge?  project ssh-trust myproj on   (accept-new / TOFU; common forges already trusted)
```

**TUI:**

1. `,` (**Settings**) → `a` **Add key** → enter the key path (it's `ssh-add`ed immediately).
2. Select the project → `p` (**Project**) → `e` (**Env mounts**) → `a` **Add** → Kind = **ssh**.
3. `p` → `r` **Recreate** to apply. (In Env mounts, `t` toggles auto-trust for unknown forges.)

> The base image needs `openssh-client` — if `ssh` mounts predate it, `image build base` then recreate.

### AWS credentials — as environment variables

The cleanest way to give a container AWS access (used by both Terraform and the AWS CLI) is
environment-variable creds. Each value is stored `0600` in the state tier and injected **pass-through**
(never in argv, never in the config file, never in the `/workspace` git checkout).

**CLI** (each `env add … env <NAME>` prompts hidden for the value):

```bash
uv run claudemanctl project env add infra env AWS_ACCESS_KEY_ID
uv run claudemanctl project env add infra env AWS_SECRET_ACCESS_KEY
uv run claudemanctl project env add infra env AWS_SESSION_TOKEN    # only for temporary/STS creds
uv run claudemanctl project env add infra env AWS_REGION           # e.g. eu-west-1
uv run claudemanctl project recreate infra                        # apply
uv run claudemanctl project env list infra                        # confirm (values stay hidden)
```

Prefer to pipe a value instead of typing it? add `--stdin`:
`printf '%s' "$KEY" | uv run claudemanctl project env add infra env AWS_ACCESS_KEY_ID --stdin`.

**TUI:** select the project → `p` → `e` (**Env mounts**) → `a` → Kind = **env var**, enter the name
and the (hidden) value; repeat per variable, then `p` → `r` **Recreate**.

> `aws sso login` / STS role-caching write to `~/.aws/{sso,cli}/cache` on the read-only HOME and aren't
> supported in-container — pass **already-resolved** credentials via these env vars. (`AWS_CONFIG_FILE`
> /`AWS_SHARED_CREDENTIALS_FILE` are redirected to the ephemeral `~/.cache` tmpfs, so `aws configure`
> works too, but it doesn't survive the session.)

### Other env vars and config files

- **Any env var** (a token, a `DATABASE_URL`): same as AWS — `project env add <slug> env <NAME>`
  (TUI: Kind = env var). Forbidden names (the auth/`GH_TOKEN`/`ANTHROPIC_*` set) are rejected.
- **A host config file** (a `~/.netrc`, a kubeconfig): `project env add <slug> file <host-path>
  <container-path>` (read-only by default; add `--rw` to make it writable). TUI: Kind = **file**.
- **A GitHub token** for `gh`: `config gh-token` (TUI: Settings `,` → `t`). Or just run
  `gh auth login` inside the container.

### claude.ai connectors (remote MCP) — login auth mode

The default setup-token auth is inference-only, so your claude.ai **account connectors**
(Gmail/Drive/Linear/custom, configured on claude.ai) never appear in-container. Opt the project
into **login mode**:

```bash
uv run claudemanctl project auth myproj login && uv run claudemanctl project recreate myproj
uv run claudemanctl project claude myproj    # first launch: /login — authorise in the host
                                             # browser, paste the code back into the terminal
```

The minted credential lives in that project's own bind (survives stop/recreate; `project logout
myproj` removes it). Locally-added MCP (`claude mcp add`) needs none of this. On a **locked**
project, allowlist each connector's hosts via `project egress-log`. Details + security
trade-offs: [`CLI.md` § Auth mode](CLI.md#auth-mode-claudeai-connectors),
`docs/SECURITY.md` residual risk 6.

---

## Guide: Node / frontend app

A React/Vite/Node service. The `node` overlay bakes Node + corepack (yarn/pnpm).

**CLI:**

```bash
uv run claudemanctl image build node                                       # once per machine
uv run claudemanctl project create web --overlay node --language node
uv run claudemanctl project up web
uv run claudemanctl project repo add web git@github.com:org/web-app.git     # needs SSH — see Add-ons
uv run claudemanctl project claude web
```

**TUI:** `n` New project (overlay **node**, language **node**) → `g`/`a` Add repo → **Enter**/`c` to
open. For a private repo, set up [SSH](#ssh--private-git-agent-forwarded) first.

> `yarn install` / `pnpm i` work under the read-only floor — the package caches are redirected onto
> the `/workspace` bind. Run them in the repo dir inside the container.

## Guide: Python service

A Python API, scripts, or data work. The `python` overlay bakes Python + `uv`.

**CLI:**

```bash
uv run claudemanctl image build python                                     # once per machine
uv run claudemanctl project create api --overlay python --language python
uv run claudemanctl project up api
uv run claudemanctl project repo add api git@github.com:org/api.git
uv run claudemanctl project claude api
```

**TUI:** `n` New project (overlay **python**, language **python**) → `g`/`a` Add repo → open.

> Keep the project's virtualenv inside `/workspace` (e.g. `uv venv /workspace/.venv`); `uv sync`
> writes its caches and downloaded interpreters to the `/workspace` bind, not the read-only rootfs.

## Guide: Polyglot (Python + Node)

A Node app that also needs Python/pip (build tooling, a sidecar script). The `python-node` overlay has
both toolchains in one image.

**CLI:**

```bash
uv run claudemanctl image build python-node
uv run claudemanctl project create app --overlay python-node --language node
uv run claudemanctl project up app
uv run claudemanctl project repo add app git@github.com:org/app.git
uv run claudemanctl project claude app
```

**TUI:** `n` New project (overlay **python-node**, language **node**) → add repo → open.

## Guide: Rust

A Rust crate or binary. The `rust` overlay bakes rustup/cargo.

**CLI:**

```bash
uv run claudemanctl image build rust
uv run claudemanctl project create svc --overlay rust --language rust
uv run claudemanctl project up svc
uv run claudemanctl project repo add svc git@github.com:org/svc.git
uv run claudemanctl project claude svc
```

**TUI:** `n` New project (overlay **rust**, language **rust**) → add repo → open.

## Guide: Infrastructure (Terraform + AWS)

The full infra setup: the `terraform` overlay (Terraform + Packer + the AWS CLI v2), AWS credentials
as env vars, SSH for private modules/repos, and — optionally — a network lock. This is the recipe to
copy for an `infrastructure/`-style repo.

**CLI:**

```bash
# 1. Build the overlay (once per machine).
uv run claudemanctl image build terraform

# 2. Create + start the project.
uv run claudemanctl project create infra --overlay terraform --profile work
uv run claudemanctl project up infra

# 3. SSH for private modules / the infra repo (keys stay on the host — see Add-ons).
uv run claudemanctl config ssh add ~/.ssh/id_ed25519
uv run claudemanctl project env add infra ssh

# 4. AWS credentials as env vars (both Terraform and the AWS CLI read these — see Add-ons).
uv run claudemanctl project env add infra env AWS_ACCESS_KEY_ID
uv run claudemanctl project env add infra env AWS_SECRET_ACCESS_KEY
uv run claudemanctl project env add infra env AWS_SESSION_TOKEN     # temporary/STS creds only
uv run claudemanctl project env add infra env AWS_REGION            # e.g. eu-west-1

# 5. Apply the SSH + env mounts (they're fixed at create), then clone the repo + open.
uv run claudemanctl project recreate infra
uv run claudemanctl project repo add infra git@github.com:org/infrastructure.git
uv run claudemanctl project claude infra
```

Inside the container: `aws sts get-caller-identity` confirms the creds; `terraform init && terraform
plan` run with state written to `/workspace`.

**TUI:**

1. `n` **New project**: slug `infra`, overlay **terraform**, profile **work**.
2. `,` (**Settings**) → `a` **Add key** (`~/.ssh/id_ed25519`).
3. Select `infra` → `p` (**Project**) → `e` (**Env mounts**) → `a`:
   - Kind = **ssh** (forward the agent), then
   - Kind = **env var** ×4 for `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` /
     `AWS_SESSION_TOKEN` / `AWS_REGION` (hidden values).
4. `p` → `r` **Recreate** to apply all mounts.
5. `g`/`a` **Add repo**, then **Enter**/`c` to open.

**Lock it down (optional but recommended for infra).** A locked terraform project must allowlist the
provider/cloud hosts. See the next guide; for AWS+Terraform the extras are typically
`registry.terraform.io`, `releases.hashicorp.com`, and `.amazonaws.com`.

> AWS CLI floor notes and the credential model live in
> [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) (Overlays) and the overlay Dockerfile header.

## Guide: Lock a project down (strict egress)

Restrict a container to an **allowlist** of domains, routed through a squid proxy on a no-direct-route
network — for running untrusted code, or just to keep an infra project from talking to anything but
its provider APIs. Egress is fixed at create, so lock/unlock **recreate**. Full detail:
[`CLI.md` § Strict egress](CLI.md#strict-egress-lock-a-project-to-an-allowlist).

**CLI:**

```bash
uv run claudemanctl project create infra --overlay terraform --egress strict   # locked from the start
# ...or lock an existing project (builds the proxy image once, recreates):
uv run claudemanctl project lock infra

# Add the hosts this project legitimately needs (edit [project.egress].allowlist in its TOML), e.g.
#   registry.terraform.io, releases.hashicorp.com, .amazonaws.com, cloud.mongodb.com
# then re-lock to re-render + recreate:
uv run claudemanctl project lock infra

uv run claudemanctl project egress-log infra     # what the allowlist BLOCKED — promote the legit ones
uv run claudemanctl project egress-smoke infra    # prove enforcement: allowed host reaches, blocked one doesn't
```

**TUI:** select the project → `p` (**Project**) → `g` (**Egress…**): `l` lock/unlock, `a`/`x`
add/remove an allowlist domain (validated, applied on the next recreate), `b` to **promote** a
destination the proxy actually blocked straight into the allowlist, and `r` **apply** (recreate) —
the key that makes inline allowlist edits take effect on a locked project. The always-on
**Network panel** shows per-project Blocked/Allowed counts and Traffic.

> The base allowlist always includes `claude.ai` (OAuth refresh), the Anthropic API, GitHub, and the
> package registries. `ssh`-based git **works under lock** for github / gitlab / bitbucket: their
> SSH-over-443 endpoints are tunnelled through the sidecar on the same allowlist (the project needs
> the `ssh` env-mount — see Add-ons). Azure DevOps has no 443 SSH endpoint — use HTTPS remotes
> there, or open mode.

## Guide: Add a local model (hybrid)

Run a project's in-container `claude` against a **self-hosted model** alongside your claude.ai
subscription — both appear in the `/model` picker and switch mid-session. Requires host Ollama (GPU
build, `0.0.0.0:11434` bind, model pulled). Full setup: [`docs/MODELS.md`](MODELS.md); the CLI
verbs: [`CLI.md` § Local models](CLI.md#local-models-hybrid-mode).

**CLI:**

```bash
uv run claudemanctl model add qwen3-coder:30b              # install a model (host Ollama; streamed)
uv run claudemanctl project model set web qwen3-coder:30b  # pin it -> hybrid mode
uv run claudemanctl project recreate web                   # apply (the gateway sidecar comes up)
uv run claudemanctl project model clear web                # back to subscription-direct
```

**TUI:** `m` (global) opens the **Models** screen to install/update/remove models; select a project →
`p` → `m` (**Model…**) to pin/unpin — the pick applies itself (persisted + an automatic recreate for
a local pick; a **claude**-model pick from the same list is registry-only and just changes what the
next `c` launches, `claude --model <ref>`).

---

See also: [`CLI.md`](CLI.md) (the full command reference), [README](../README.md) (getting
started + prerequisites), [`docs/TUI-GUIDE.md`](TUI-GUIDE.md) (every
keybinding + screen), [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) (how it works),
[`docs/SECURITY.md`](SECURITY.md) (the hardening model).
