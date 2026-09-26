# Approved tools — a per-project image layer (design + operator guide)

Status: **implemented 2026-09-10** (registry + renderer + CLI/TUI + smoke gate). Tracking:
[`ROADMAP.md`](../ROADMAP.md) Phase 10.

## Why

The fixed overlays (`base` / `python` / `rust` / `node` / `python-node` / `terraform`) are all-or-
none: a project either gets an overlay's whole toolchain or nothing. The infrastructure project
needed `kubectl`, `helm`, the AWS Session Manager plugin, `psql`, `jq`, PyYAML and `uv` on top of the
terraform overlay — side-loading into `/workspace/.tools` works but is unpinned, unverified, and
outside the smoke gate. `Project.extra_apt` (a never-implemented "thin apt layer" stub) was the
seed of this; an unrestricted apt list is exactly the unapproved surface we don't want, so it was
replaced by a **registry of approved tools**.

## Concept

- A **registry** ships in this repo: `library/tools/<name>/tool.toml`, one entry per tool. Every
  entry is **pinned** (a Debian package, or an upstream release artefact with a per-arch sha256
  that is verified at build) and carries the **custom config** the tool needs to actually work
  under the hardened floor: `[env]` redirects, `[[smoke]]` probes of its core operation, `requires`,
  `build_deps`, and a locked-egress `allowlist` hint.
- A project **selects** tools (`Project.tools`). The selection is **additive on top of the
  project's overlay** — the overlay stays the base stack (the terraform overlay keeps its own
  recipe), tools are a further layer. "Custom" is simply `base` + a selection; no new overlay type.
- The selection is rendered into a Dockerfile (`tools/render.py`, pure) `FROM claude-man:<overlay>`
  and built as a **content-addressed** image `claude-man:<overlay>-t-<12 hex>` (a sha256 prefix of
  the rendered Dockerfile). The tool set, every version/checksum pin and every env redirect change
  the tag: two projects with the same selection share one image, a stale image is never mistaken for
  a current one, and `ensure_chain`'s build-only-if-missing logic stays correct. The build chain is
  base → overlay → tools layer, so the on-start claude update rebuilds the layer along with its
  parents.
- **Recreate to apply.** The tools are baked into the image, so a changed selection lands on the
  next `recreate`/`up`, exactly like ports and env-mounts (unlike packs, which apply per toggle).
- **The hardened run profile is untouched** (invariant 2): the layer changes only what is baked on
  the read-only rootfs. `image smoke --project <slug>` proves every selected tool's core operation
  works as uid 1000 under `--read-only`.

## Registry entry (`tool.toml`)

```toml
description = "Kubernetes CLI (kubectl)"     # required
kind = "release"                              # "apt" | "release"

# kind = "apt": Debian trixie packages
packages = ["postgresql-client"]

# kind = "release": a pinned upstream artefact per arch, sha256-verified at build
version = "1.37.0"
install = "binary"                            # "binary" (bin = …) | "tar" (members = […]) | "deb" | "bundle" (tree + bins)
bin = "kubectl"
# tree = "aws/dist"                           # bundle: a zip's self-contained dir, installed whole to /opt/<name>
# bins = ["aws", "aws_completer"]             # bundle: TREE-relative executables symlinked into /usr/local/bin (build fails if missing)
[release.amd64]
url = "https://dl.k8s.io/release/v1.37.0/bin/linux/amd64/kubectl"
sha256 = "…64 hex…"
# members = ["linux-amd64/helm"]            # tar: archive paths installed to /usr/local/bin by basename
[release.arm64]
url = "…"
sha256 = "…"

requires = ["python3"]     # other registry tools pulled into the layer automatically
build_deps = ["unzip"]     # apt packages needed only at build (purged after; required for a bundle)
note = "needs the AWS CLI — select aws-cli, or run on the terraform overlay"   # free-text caveat shown in listings
allowlist = [".amazonaws.com"]   # runtime hosts a LOCKED project should allowlist (hint only)

[env]                      # image ENV — the read-only-floor redirects (overlay-scoped, like the
KUBECONFIG = "/home/agent/.cache/kube/config"   # terraform overlay's; the global _BAKED_ENV is untouched)

[[smoke]]                  # image-smoke probes: the tool's CORE op as uid 1000 under the floor
name = "kubeconfig writes to the .cache tmpfs (read-only floor)"
argv = ["sh", "-lc", "kubectl config set-cluster smoke --server=https://127.0.0.1:1 && echo ok"]
expect = "ok"
timeout = 15
```

Validation (`tools/library.py`, lint-tested against the shipped tree): names are slug-shaped;
both arches are required; URLs are plain https with no shell metacharacters (they are quoted into
a generated `RUN`); sha256 is 64 hex; tar members and a bundle's `tree`/`bins` can't escape (`..`/absolute) and carry
no shell metacharacters; a ZIP bundle must list `unzip` in `build_deps` (a tar.gz bundle needs
nothing; all arches must be one kind); `tree = "."` means the archive root is the tree; an optional
`version_label` (a release tool only) stamps `claude-man.<label>=<version>` on the layer; `[env]` can't touch
`HOME`/`PATH`/`USER`/the claude config/XDG floor keys or any `FORBIDDEN_ENV_NAMES` (invariant 1),
and its values are single-line strings (empty is allowed — set-but-empty, e.g. `AWS_PAGER = ""`);
`requires` must resolve and be acyclic; two selected tools setting one env key differently is an
error (a silent last-wins would break a floor redirect).

## Where writes go (the floor fixups the shipped entries carry)

| Tool | Writes at use time | Redirect |
|---|---|---|
| kubectl | `~/.kube/config` (credentials), `~/.kube/cache` | `KUBECONFIG`, `KUBECACHEDIR` → the `.cache` tmpfs (ephemeral; re-run `aws eks update-kubeconfig` per session — creds never land in `/workspace`) |
| helm | `~/.config/helm` (repos + `registry login` creds), `~/.local/share/helm` (plugins), `~/.cache/helm` (repo indexes, tens of MB) | `HELM_CONFIG_HOME` → tmpfs; `HELM_DATA_HOME`, `HELM_CACHE_HOME` → `/workspace/.helm` (disk-backed, like the yarn/uv caches) |
| psql | `~/.psql_history`, `~/.pgpass` | `PSQL_HISTORY`, `PGPASSFILE` → tmpfs |
| session-manager-plugin | nothing (stateless) | — |
| aws-cli | `~/.aws/config`, `~/.aws/credentials` (credentials) | `AWS_CONFIG_FILE`, `AWS_SHARED_CREDENTIALS_FILE` → tmpfs — the terraform overlay's exact values, so the two coexist; env-var creds via a `kind="env"` env-mount preferred. No redirect exists for the STS role cache / SSO cache (`~/.aws/cli`, `~/.aws/sso`), so `aws sso login` / role-caching are unsupported. Plus `AWS_PAGER=""` (issue #41): on a TTY the CLI pages through `less`, which the image doesn't ship — empty disables the pager (pipe to `bat` for long output) |
| k9s | `~/.config/k9s` (config, skins, per-context configs, screen dumps, benchmarks) | `K9S_CONFIG_DIR` → tmpfs (with it set, k9s puts every one of those under it; logs go to the `/tmp` tmpfs). Rides kubectl's `KUBECONFIG` via `requires` |
| uv | caches, interpreters, tool venvs | already redirected by the baked `UV_*` env |
| codex | `$CODEX_HOME` (sqlite state, `auth.json`, `config.toml`) | provider plumbing, not a tool `[env]`: the runner injects `CODEX_HOME=/home/agent/.codex` and binds the project's config dir there (docs/AGENTS.md § Codex). Pulled in automatically by `agent = "codex"` — the full `codex-package` tar.gz as a bundle (`tree = "."`, `bin/codex` + `bin/codex-code-mode-host` symlinked), `version_label = "codex-version"` stamped on the layer so the provider can read its version |
| jq, python3, python3-yaml | nothing | — |

The rule from CLAUDE.md applies to every new entry: **exercise the real workflow** under the floor
(`image smoke --project`), not just `--version`, and redirect every write onto a writable surface —
small/ephemeral/credential → the `.cache` tmpfs, large or persistent → `/workspace`.

## Operator usage

```bash
uv run claudemanctl tools list [-v]                          # the registry (-v: env redirects + locked-egress hosts)
uv run claudemanctl project create infra --overlay terraform --tool kubectl --tool helm
uv run claudemanctl project tools add infra session-manager-plugin   # validated now; recreate to apply
uv run claudemanctl project tools rm  infra helm
uv run claudemanctl project tools list infra                 # selection + what `requires` pulls in
uv run claudemanctl project recreate infra                   # bakes the layer (built if missing) + recreates
uv run claudemanctl image build --project infra              # build the project's image by hand (parents first)
uv run claudemanctl image smoke --project infra              # base + overlay + per-tool probes under the floor
```

TUI: select the project → `p` (Project…) → `t` **Tools (image)…**. The checklist marks the
selection (`✓`) and what `requires` pulls in (`+`); toggles edit a pending selection, **Apply**
persists it and recreates (streamed to the log pane), Close discards.

`project.toml`:

```toml
[project]
overlay = "terraform"
tools   = ["kubectl", "helm", "session-manager-plugin", "postgresql-client", "jq", "python3-yaml", "uv"]
```

Locked projects: a tool's `allowlist` is a **hint** — `project tools add` on a strict project
prints the hosts the new tools reach at runtime; add what you need to `[project.egress].allowlist`
(the Egress… screen can promote a blocked host). Image builds run on the host, not through the
sidecar, so build URLs need no allowlist.

## Adding a tool

1. `library/tools/<name>/tool.toml` — pin the version, resolve the per-arch sha256 from the
   vendor's published checksum (or compute it from the downloaded artefact and say so in the
   header comment), write the `[env]` redirects, and at least one `[[smoke]]` probe that exercises
   a real write path. Pick the install kind by the artefact's shape: a bare executable → `binary`;
   a tarball of executables → `tar`; a Debian package → `deb`; a zip carrying a self-contained
   directory (an embedded runtime + its libs, like the AWS CLI v2 bundle) → `bundle` — the tree
   lands whole under `/opt/<name>` and its `bins` are symlinked into `/usr/local/bin`, so the
   executables still find their sibling files (never `tar`/`unzip` single members out of such a
   tree).
2. `uv run python -m unittest tests.test_tools_library` — the lint.
3. Build + smoke it on a project (or a throwaway selection) and confirm no `EROFS`/`Permission
   denied` marker — the smoke fails any probe whose output carries one.

Keep entries **public-safe** (the repo is public): house tools in; client-specific material stays
out.

## Known limits / follow-ups

- **Image accumulation.** Every distinct selection (or a registry bump) is a new
  `claude-man:<overlay>-t-<hex>` image; superseded ones are never removed automatically. Prune with
  `docker image ls claude-man` + `docker rmi` once no project references a tag (`project tools list`
  / `project status` shows what each project runs on). An `image prune` verb is a follow-up.
- **Drift signal.** A running container carries the `claude-man.tools` label of the selection it
  was built with; a registry edit isn't surfaced as "needs recreate" in the projects table yet.
- **The fixed overlays are not yet registry presets.** `python` = `{python3, uv}` etc. could be
  expressed as named selections over this registry, which would make their smoke probes
  data-driven too. Deferred; the overlays keep their hand-written Dockerfiles.
- **No arbitrary apt.** By design — the registry is the approved list. `extra_apt` was removed.
