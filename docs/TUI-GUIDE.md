# TUI guide — building a working environment

A start-to-finish walkthrough of standing up a complete claude-man environment from the
**Textual TUI** (`uv run claudeman`): minting an account profile, creating a hardened project,
adding repos, wiring up the git identity and GitHub CLI, and forwarding your ssh-agent into the
container. Every step lists the exact keys and what each screen does. Where something is
CLI-only (the TUI deliberately doesn't cover it), the step says so and gives the
`claudemanctl` command.

This is the *operator* walkthrough. For what the screens are built on, see
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md); for why the container is shaped the way it is, see
[`docs/SECURITY.md`](SECURITY.md); for the CLI-first version of everything here, see the
[`README.md`](../README.md).

## 0. Before you launch: one-time host setup (CLI)

Two things can't be done from the TUI and are needed once per host.

**Mint at least one account profile.** The TUI's New-project form only *selects* among
already-defined profiles — minting one is CLI-only because it's an interactive browser flow:

```bash
uv run claudemanctl profile add home --default     # personal subscription account
uv run claudemanctl profile add work --sso --email you@company.com   # SSO work seat
```

`profile add` runs `claude setup-token` on the host (after `claude auth login` if you passed
`--sso`/`--login`/`--console`): complete the flow in the browser, copy the token it prints at
the end, and paste it at claude-man's `Paste the long-lived token:` prompt. The token lands
`0600` at `~/.local/state/claude-man/profiles/<name>/token` and is injected per-launch as
`CLAUDE_CODE_OAUTH_TOKEN` — claude-man never copies `.credentials.json` into a container.
Tokens live ~1 year and can't self-refresh; `profile list` flags them `EXPIRING` past 330 days
(`profile renew <name>` re-mints). See [`README.md`](../README.md) § *Setting up accounts* for
all the login-flow flags.

**(Recommended) smoke-test the hardened image.** You don't *have* to build images by hand —
project create/start auto-builds any missing image, streaming docker progress into the TUI's
log pane — but the smoke gate is CLI-only and worth running once:

```bash
uv run claudemanctl image build base    # explicit build (always rebuilds)
uv run claudemanctl image smoke base    # probe battery under the real hardened run profile
```

You can create a project with **no** profile/token — it builds and starts fine — but the
in-container `claude` won't authenticate, and the log pane tells you so.

## 1. Launch and orientation

```bash
uv run claudeman
```

A short boot splash plays (~2 s; any key skips it; auto-skipped on tiny terminals; disable
with `claudemanctl config splash off`), then scrolls off to reveal the main screen, top to
bottom:

- **Projects table** — `Project · Status · Profile · Egress · Repos · Version · Detail`.
  Status is green `UP`, red `STOPPED`, or yellow `DEFINED` (registry entry, no container);
  it's polled fresh every 10 s, never cached. The Repos cell becomes a live git summary
  (e.g. `2 ✓ client:~↑1`) once the 30 s fetch-less scan has run.
- **Repo detail panel** — follows the table cursor: `Dir · Branch · State · ↑/↓ · Last commit`
  per repo of the selected project.
- **Usage panel** — one row per profile: token totals from container transcripts plus the
  account-wide `5h` / `Week` subscription bars (green < 70 % < yellow < 90 % < red). A `re-mint`
  note means the token predates the usage scope — `profile renew` fixes it.
- **Network panel** — one row per project: `Project · Egress · Blocked · Allowed · Traffic`.
  **Traffic** is the whole-container network I/O since the container started (`docker stats`
  NetIO, RX / TX) and shows for **every** running project, locked or open. **Blocked** /
  **Allowed** are the distinct destinations the squid sidecar denied / permitted and apply
  to **locked** projects only (open projects have no sidecar, so they read `—`); a non-zero
  Blocked count turns red. Stopped projects read `—`. Refreshed on the same 10 s cycle as the
  projects table. For the per-destination detail (which hosts were blocked), use the CLI
  `claudemanctl project egress-log <slug>`.
- **Log pane** — every action's result lands here (green ok / red failure / yellow advisory),
  including streamed docker-build progress.

The bottom key bar has two rows so the scope of every key is explicit: the **project** row
acts on the project under the cursor; the **global** row acts app-wide. Three keys open
**submenus** (press the key, then one more key to pick).

**`project` row** — acts on the selected project:

| Key | Action |
|---|---|
| `enter` | Shell into the selected project (auto-starts it if stopped) |
| `c` | Claude in the selected project (auto-starts it if stopped) |
| `e` | Editor — neovim in the project's workspace (auto-starts it if stopped) |
| `b` | Browse the project's workspace in your file manager |
| `s` | Start / stop the selected project |
| `g` | Repos… → `a` Add repo · `x` Remove repo · `r` Refresh-git (fetch) · `p` Pull all (ff-only) |
| `p` | Project… → `e` Env mounts · `o` Ports · `p` Packs… · `r` Recreate · `d` Delete |
| `y` | Sync-back review gate — **Phase 5 stub**, logs a placeholder line today |

**`global` row** — acts app-wide:

| Key | Action |
|---|---|
| `n` | New project |
| `S` | Stop **all** running projects + sync assets out (end-of-day) |
| `v` | View… → `u` Refresh usage · `l` Focus logs |
| `,` | Settings (ssh keys · git identity · GH token · terminal) |
| `q` | Quit immediately — containers keep running |

Project-row keys act on the row under the cursor, and rows for orphan containers (a
container with no registry entry) are refused — the registry is the source of truth.
(Env mounts used to be a top-level `e`; they now live in the Project… submenu, and `e`
opens the editor.)

## 2. Create a project (`n`)

Press `n`. The **New project** form has five fields:

- **Slug** — lowercase letters/digits/hyphens, ≤ 64 chars (validated inline; duplicates
  rejected). This names the container (`claude-man-<slug>`) and the state dirs.
- **Profile (account)** — pick a profile, or leave the first entry (`(default: <name>)`) to
  inherit the default. Only existing profiles are listed (step 0).
- **Overlay (image)** — `base`, `python`, `rust`, or `node`: the toolchain baked into the
  project's image.
- **Language (pack tier)** — picks which language tier's *default* curated packs are applied
  at create, alongside the common ones (see *Curated packs* below). Choosing an overlay
  pre-fills the matching tier as a suggestion; pick a language yourself and the suggestion
  stops. Leave `(none)` for common-tier packs only.
- **Egress** — `open` or `strict`. `strict` runs the project behind the allowlist egress
  proxy (a squid sidecar on a no-route internal network — see the README's strict-egress
  section); it can also be toggled later with `claudemanctl project lock|unlock <slug>`.
  Start with `open` unless you've already tuned an allowlist.

`Create` writes the registry TOML, auto-builds the image chain if missing (base, then the
overlay — the first build takes minutes and streams into the log pane), seeds the per-project
Claude config from the profile's seed, and `docker create`s the hardened container. **It does
not start it** — that's `s`.

## 3. Add repos (`g` → `a`)

With the project selected, press `g` then `a`. The **Add repo** form:

- **Remote URL** — `git@github.com:org/repo.git` or `https://host/org/repo.git` (shape-checked
  only; the clone itself is the reachability test).
- **Branch** — pre-filled `main`.
- **Dir** — workspace subdir; blank derives it from the URL (the placeholder live-updates to
  show what you'll get).

Repos are cloned **host-side** into the workspace bind, so they appear inside a running
container immediately — no recreate, ever, for repo operations. If a clone fails the registry
entry is kept and the log suggests the retry (`claudemanctl project sync-repos <slug>`).
Note: an ssh-URL clone runs on the *host*, so it uses your host ssh setup — the in-container
ssh mount (step 6) is only for the agent's own pushes/pulls.

The rest of the Repos menu: `x` removes a repo (registry-only by default; tick *also delete
the on-disk checkout (--purge)* to remove the checkout), `r` forces a fetch-ful state rescan
(the background 30 s scan never fetches), and `p` previews then applies a fast-forward-only
pull of every repo (skips dirty/diverged trees, with the reason shown per repo in the
preview). On Linux/WSL2 pull is refused unless your host uid is 1000 — a host-side pull with
another uid would write wrong-owner files and trip git's "dubious ownership" guard; macOS
skips the check (Docker Desktop maps ownership).

## 4. Start it and open Claude (`s`, `enter`, `c`)

Press `s` to start. If a **newer claude** exists than the one baked into the project's image,
the *Update claude* modal appears first: `Enter`/`r` rebuilds the image to the newer version
and starts (a host-side rebuild — `claude update` can't run inside the read-only container);
`s` starts on the current image; `Esc` cancels the start entirely. The check fails open —
offline just starts on the existing image.

Then:

- `enter` opens a detached terminal window with a **shell** in the container.
- `c` opens a detached terminal window running **claude** (authenticated as the project's
  profile). Both auto-start a stopped project first (this path skips the update prompt).
- `b` opens the workspace directory in your file manager.

Terminal windows open at `/workspace` — the uniform anchor where the workspace `CLAUDE.md`
and any pack-injected guidance live (a `workdir` setting in the project TOML lands you in a
repo dir instead). The terminal program is auto-detected
per platform (Linux: ghostty → alacritty → kitty → …; macOS falls back to Terminal.app; WSL2
picks up Windows Terminal); change it in Settings (`,` → `e`) or with
`claudemanctl config terminal`.

**One `claude` per container.** A second `c` on the same project is refused while a claude is
already running (two would race on `.claude.json`). A second *shell* is always fine — but
don't launch `claude` by hand from that shell; the guard can't see a future one.

## 5. Git identity + GitHub CLI (`,` → `g`, `,` → `t`)

So the agent can `git commit` and use `gh` from inside the read-only container.

**Git identity.** Press `,` (Settings) then `g`. Two fields: `user.name` and `user.email` —
**blank means inherit your host `git config --global`** (each placeholder shows the host value
you'd inherit). The identity is injected as git env-config at container create, so commits
made by the agent (including from the baked-in neovim) carry the right author. Saving shows
*"git identity saved — recreate a project to apply"* — do that with `p` → `r` on each project
that should pick it up.

**GitHub token (optional).** `gh` is baked into every image, but it has no credentials by
default. Press `,` then `t`: paste a token into the masked input (`ghp_…` /
`github_pat_…`) and `Save`. It's stored `0600` in the state tier (never in the config file,
never echoed back — the screen only ever says `set` / `not set`) and injected pass-through as
`GH_TOKEN` into every container **on the next recreate**. `Clear` (shown only when a token is
set) removes it.

Without a token, `gh` still works — run `gh auth login` inside the container (its config lands
on a writable tmpfs), or add a project-scoped token as an `env` mount (step 7). Don't name an
env mount `GH_TOKEN` though — that name is reserved for the Settings entry and rejected.

## 6. SSH pass-through (`,` → `a`, then `p` → `e` → `a`)

This gives the agent working `git push` / `ssh` **without any private key ever entering the
container** — the host ssh-agent signs, and only the agent *socket* is forwarded.

**First, register your key with claude-man** (Settings, `,`):

- `a` — Add key. The picker lists keys discovered under `~/.ssh` (each `.pub` with a private
  sibling, not yet configured); or type a path. Adding also loads it into the agent
  immediately, and every TUI start re-loads it, so the forwarded socket always has your
  identity. **Passphrase-protected keys never auto-load** (loading is deliberately
  non-interactive): the Status column just shows `not loaded`, and the *needs passphrase
  (run `ssh-add …` manually)* explanation appears in the status line at the bottom of the
  dialog after the Add attempt — run `ssh-add ~/.ssh/<key>` yourself to load it.
- `l` — Load all configured keys now; `x` — stop auto-loading a key (it stays loaded this
  session until you `ssh-add -d`).

**Then add the ssh mount to the project.** Select the project, press `p` then `e` (Project…
→ Env mounts), then `a` (Add). Set **Kind** to `ssh` — there's nothing else to fill in, just
`Add`. The status
line reminds you: mounts are fixed at container create, so **recreate to apply** (`Esc` back
to the main screen, `p` → `r`).

After the recreate the container has: the agent socket bound read-only at `/ssh-agent` (with
`SSH_AUTH_SOCK` pointing at it), a private `0700` tmpfs at `~/.ssh`, and your host
`~/.ssh/config` + `known_hosts` seeded into it on every start (non-secret material only, so
host aliases resolve and there are no host-key prompts). Verify from a shell (`enter`):

```bash
ssh-add -l                      # your key's fingerprint, signed by the HOST agent
ssh -T git@github.com           # "Hi <you>! You've successfully authenticated…"
```

The `s` (Resync) action on the Env-mounts screen re-validates sources and re-seeds
`config`/`known_hosts` into a *running* container — useful after editing host ssh config; it
does **not** apply mount add/removes (only recreate does).

**macOS:** Docker Desktop can only forward the *default* launchd agent (via its built-in
`/run/host-services/ssh-auth.sock` magic socket), so keys must be loaded into that default
agent — Settings → `l` does exactly that. On Linux/WSL2, claude-man falls back to starting a
managed agent if your session has none.

## 7. Other env mounts, published ports, and curated packs

**File mounts and env vars** (`p` → `e`, then `a`):

- Kind `file` binds a host file (or directory) at a container path, read-only by default
  (e.g. `~/.netrc` → `/home/agent/.netrc`). Destinations inside claude-man-managed mounts
  (anything under `/home/agent/.claude`) are rejected — that's a security guard.
- Kind `env var` injects a named variable: the value is entered once in a masked input,
  stored `0600` in the state tier, shown as `(value hidden)` forever after, and injected
  pass-through (never in argv). The auth-critical names are rejected at add time —
  `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, and `GH_TOKEN`
  (case/padding variants included). To change a value: remove (`x`) and re-add.

**Ports** (`p` → `o`, then `a`): publish a service running inside the container.
Container port must be **≥ 1024** (the hardened profile drops the privileged-port
capability); host port defaults to the same; bind IP defaults to `127.0.0.1` (host-only) —
type `0.0.0.0` to expose on the LAN, which the table then flags. The service inside must
listen on `0.0.0.0`, not container-localhost. As with mounts: **recreate to apply**.

**Curated packs** (`p` → `p`): a checklist of the in-repo pack library — bundles of
CLAUDE.md fragments and skills (guardrails, code-quality, language conventions…) curated in
the claude-man repo ([`docs/PACKS.md`](PACKS.md)). Rows are grouped *Common* / your
project's language tier, with anything else you've selected (cross-tier or stale names)
listed under *Other (selected)*. `space`/`enter` toggles a pack, `d` re-applies the library
defaults for the project's language — every change saves, materializes into the asset
source, and syncs into the binds **immediately** (claude picks it up at its next session
launch; no recreate). The **State** column flags copies that differ from the library:
`stale` (refreshed on next start), `⚠ drifted` (an in-container edit — re-stamped from the
library + backed up; upstream improvements belong in the library), `operator file wins`
(your own same-named file blocks the pack entry), `not in library` (a selection that
outlived the library).

## 8. Day to day

- `s` stops a running project. Stopping syncs the project's assets out (its `CLAUDE.md` +
  skills/agents back to the synced config tier); starting syncs them in.
- `S` is the end-of-day command: confirm once, and every running container is stopped +
  synced out (`Enter` there also quits; `s` stays). The confirm warns that stopping closes
  any detached claude/shell/nvim windows.
- `q` quits the TUI **immediately and leaves containers running** — quit is not stop. The
  containers, workspaces, and config dirs persist until you delete the project; after a host
  reboot the containers come back *stopped* (no restart policy is set) — press `s` to start
  them again.
- `p` → `d` deletes a project. A pre-scan flags repos with unsynced work (`⚠`) before the
  confirm; *keep the /workspace checkout on disk* preserves your checkouts while removing the
  container, config and registry entry. The scan is fetch-less and only ever over-warns —
  `g` → `r` first if you want certainty.
- `p` → `r` recreates the container — the apply step for everything stamped at create time:
  env mounts, ports, git identity, GH token, and profile switches
  (`claudemanctl project recreate <slug> --profile <other>` for an account switch; it's
  mismatch-guarded).

Not in the TUI yet, honestly stubbed: the `y` sync-back review gate (Phase 5 — distinct from
the automatic asset sync above) and live container logs in `v` → `l` (the pane it focuses is
the TUI's own event log). Strict egress is toggled from the CLI (`project lock|unlock`) or
chosen at create; the TUI surfaces the Egress column and the always-on **Network panel** (above).
Its Traffic figures are whole-container `docker stats` NetIO since the container started; the
Blocked/Allowed counts come from the squid access log and apply to locked projects only. Note the
counts reflect **completed** connections — squid logs a CONNECT (HTTPS) tunnel only when it closes,
so an in-flight transfer isn't counted until it ends. For the per-destination detail behind a count,
use the CLI `project egress-log <slug>`.

## Quick reference — what stays CLI-only

| Task | Command |
|---|---|
| Mint / renew / verify / seed a profile | `claudemanctl profile add\|renew\|verify\|seed <name>` |
| Explicit image build / hardened smoke gate | `claudemanctl image build\|smoke <overlay>` |
| Custom terminal template, Browse opener | `claudemanctl config terminal --custom '…'` / `config opener` |
| Boot splash toggle | `claudemanctl config splash on\|off` |
| Clone missing repos (fetch-all is `g` → `r` in the TUI) | `claudemanctl project sync-repos <slug>` |
| Claude release channel / version pin | `claudemanctl config image --channel …` |
| Browse the whole pack library across tiers (the Packs… screen shows your project's tiers) | `claudemanctl packs list [--tier …]` |

Everything else in this guide has a CLI equivalent too — see [`README.md`](../README.md).
