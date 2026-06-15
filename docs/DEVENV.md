# In-container dev environment (Phase 8)

Turning every hardened container into a **working hardened dev environment** — a curated shell and
editor the operator works in alongside the agent, not just a sandbox the agent runs in.

Status: **8a + 8c + 8d landed 2026-06-15** (curated bash baked; funky shell-open banner; opt-in
persistent history). **8b dropped** — covered by `n` (see below). `image smoke base` green — the rc
loads under `--read-only --user` and starship renders the git branch.
Tracking: [`ROADMAP.md`](../ROADMAP.md) Phase 8.

<p align="center">
  <img src="images/shell-banner.png" alt="claude-man dev-shell banner — the CLAUDE MAN block wordmark over a cheat-sheet of shell commands (n, ls/lt, g/gcm, Ctrl-R), the history mode, and the git-prompt legend" width="640">
</p>

The shell-open banner above (`images/bash/motd`, re-shown with `hints`) is the cheat-sheet for what
each container ships. Regenerate this screenshot with `uv run python docs/images/capture_banner.py`
(then `rsvg-convert -z 2 …`), exactly like the boot-splash captures.

**Goal:** make the `shell` (`b`-less terminal) and `nvim` (`e`) experiences inside a container feel
like the operator's host **Arch/Omarchy bash** — a git-aware prompt, the same history search, the
same `n` shortcut, a few quality-of-life CLIs, and an explanatory banner on shell open. Everything
is **baked, curated, network-free at runtime, and floor-preserving** — the same model as the baked
`images/nvim/` config, so the hardened profile (invariant 2) is unchanged on the default path.

## Why this is mostly an image change, not a code change

The shell terminal is opened by `tui/terminals.py::_inner_exec`, which for `program == "bash"`
execs a **plain interactive bash**: `docker exec -it -w <wd> <container> bash`. An interactive
non-login bash sources `~/.bashrc` — so a baked `/home/agent/.bashrc` is picked up automatically,
with **no change to the spawn path**. The work is: bake a curated rc + supporting tools into the
base image, exactly as `images/nvim/` bakes a curated neovim.

## Load-bearing finding: why `n` opens the tree but `nvim` opens the dashboard

This is the operator's local (host) behaviour, and it is **not** two different commands — it is one
argument:

- `n` is a shell function in `~/.local/share/omarchy/default/bash/aliases`:
  `n() { [ $# -eq 0 ] && command nvim . || command nvim "$@"; }`. With no args it runs **`nvim .`**
  — neovim launched with a single argument, the current directory.
- The operator runs **LazyVim** with the `lazyvim.plugins.extras.editor.neo-tree` extra enabled
  (`~/.config/nvim/lazyvim.json`). That extra registers a one-shot `BufEnter` autocmd
  (`Neotree_start_directory`) which calls `vim.uv.fs_stat(vim.fn.argv(0))`; when the first CLI
  argument is a **directory** it `require("neo-tree")`, and neo-tree (hijacking netrw) opens the
  **file tree as the main view**. So `nvim .` → `argv(0)` is `.` → a directory → tree.
- Bare `nvim` has **no** arguments (`argc(-1) == 0`). The directory check finds nothing, so neo-tree
  is never triggered and the **snacks dashboard** renders instead (LazyVim's no-args starter; the
  lualine `init` confirms the `argc(-1) > 0` gate — 0 args hides the statusline for the starter
  page).

**The whole difference is the `.` that `n` passes.** Same binary, same config; a directory argument
trips LazyVim's "start Neo-tree with directory" path, no argument shows the dashboard. *(The
operator's local config is intentionally left untouched — this section is research only.)*

### What that means for the container — 8b DROPPED (covered by `n`)

The container neovim is the **curated** config (`images/nvim/`), not LazyVim. But neo-tree's default
`hijack_netrw_behavior = "open_default"` already opens the tree as the main view when nvim is given a
**directory** argument — and the operator's `n` is `nvim .`. So `n` already yields the tree-on-open,
matching the host muscle memory, with **no extra autocmd**. The originally-planned `VimEnter`
side-panel (for the bare-`nvim` dashboard and single-file cases) was judged redundant and dropped:
bare `nvim` intentionally keeps the mini.starter dashboard, and the `<leader>e` toggle (8a) covers the
single-file case on demand. Net: one less moving part, and `git commit` (with `EDITOR=nvim`) never has
a tree pop over the message because we add no startup autocmd at all.

## Curated bash environment

A new `images/bash/` asset dir, `COPY`'d into the image like `images/nvim/`:

- `bashrc` → `/home/agent/.bashrc`
- `inputrc` → `/home/agent/.inputrc`
- `starship.toml` → `/home/agent/.config/starship.toml`
- `motd` (the banner; see below)

**Hard requirement:** `bashrc` begins with the interactive guard `[[ $- != *i* ]] && return`.
claude-man drives the container through **non-interactive** `docker exec sh -c …` / `bash -c …`
probes (the one-claude comm probe, the ssh-seed exec-stdin, gitstate). Those must not run rc logic.
This is the same discipline as the operator's own Omarchy `~/.bashrc`.

### Prompt with git branch/status (ask: "show what branch")

Reproduce the operator's **starship** prompt verbatim:

- Install starship — **packaged in Debian Trixie**, so `apt install starship` alongside the other
  CLIs (consistent with ripgrep/node; no pinned-binary fetch needed). *(Latest upstream is 1.25.1;
  Trixie's build is recent enough — the operator's config uses long-stable modules like
  `repo_root_format`.)*
- Bake the operator's `~/.config/starship.toml` (the `$directory$git_branch$git_status$character`
  format with the `⇡ ⇣ ? ` symbols and repo-root highlighting) unchanged.
- Env: `STARSHIP_CONFIG=/home/agent/.config/starship.toml`,
  `STARSHIP_CACHE=/home/agent/.cache/starship` (writable `.cache` tmpfs).
- rc: `eval "$(starship init bash)"` behind a `command -v starship` guard. `command_timeout = 200`
  (already in the operator's config) keeps git status responsive on large repos.

Glyphs render from the **host terminal's nerd font** (ghostty/alacritty), the same host-side
dependency the baked nvim config already documents.

### History search (ask: "similar to my current setup")

Two layers, both ported from Omarchy:

- **Prefix match on ↑/↓** — bake the operator's `inputrc` (auto-read by readline) with
  `"\e[A": history-search-backward` / `"\e[B": history-search-forward` plus the rest (menu-complete,
  case-insensitive completion, `colored-stats`). Type `git`, press ↑, cycle only `git …` lines.
- **Fuzzy `Ctrl-R`** — `apt install fzf`, then `eval "$(fzf --bash)"` in the rc (Trixie's fzf is new
  enough for `--bash`; version-proof vs. sourcing distro example paths).
- **History config** (from Omarchy `bash/shell`): `HISTCONTROL=ignoreboth`, `HISTSIZE=32768`,
  `shopt -s histappend`, and `HISTFILE` per the persistence option below.

### History persistence — config option, default OFF

The read-only home cannot hold the default `~/.bash_history`, and the only always-writable surface
is the **ephemeral `.cache` tmpfs**. So:

- **Default (off):** `HISTFILE=/home/agent/.cache/bash_history` — history is shared across shells in
  one container's lifetime but **resets on `recreate`** (same model as nvim shada today). **Zero new
  attack surface; floor byte-identical (invariant 2).**
- **Opt-in (on):** history survives `recreate`. Persistence needs a **new, small, owner-pinned
  writable bind** because neither persistent surface today is suitable (`/workspace` is the repo and
  would commit history into git; `/home/agent/.claude` is the syncback-managed config bind and is
  denylisted). The bind is a per-project state-tier dir
  (`~/.local/state/claude-man/projects/<slug>/shell/`) mounted read-write at a container path **not
  shadowed by the tmpfs/binds** (e.g. `/home/agent/.local/state/shell`), pinned `uid=1000`; the
  baked rc points `HISTFILE` there when the mount is present, else falls back to the tmpfs.

This is a **deliberate, documented invariant-2 relaxation** scoped to the opt-in path only: it adds
exactly one writable surface, owner-pinned, justified, and never on by default. The smoke/floor test
for the default path stays byte-identical; a separate test covers the opt-in bind.

**Config surface** (general-features tier, mirroring `ui_splash` in `registry/settings.py`):
`[shell] persist_history = false`, with a `set_shell_history(enabled)` setter and a
`config shell-history on|off` CLI verb (TUI: a Settings entry). Like other mount-affecting changes,
it takes effect on `recreate` (surfaced in the `Result`). Stores **no secret** — it is a plain bool
in `config.toml`.

### Banner / MOTD on shell open (ask: "explain the dev environment setup")

A baked `motd` printed by the rc on interactive shell open, explaining the environment: the `n`
shortcut, history search keys, the git aliases, and whether history is ephemeral or persistent.
Sketch (ANSI-coloured, terracotta/ember to echo the TUI splash palette):

```
 claude-man dev shell · <slug> · profile <name>
   n              neovim with the file tree open  (plain `nvim` = dashboard)
   ↑ / ↓          history prefix-search · Ctrl-R fuzzy history
   g gcm gcam     git aliases · ls / lt  eza · cd  zoxide
   <leader>e      toggle file tree in nvim · <leader>gg git status
   history        ephemeral — resets on recreate   (enable persistence: config shell-history on)
```

The history-mode line is rendered from `CLAUDEMAN_HISTFILE` (set only when persistence is on), so the
banner reflects the actual project. **As built:** shown on **every interactive shell**, gated on a
real terminal (`[[ -t 1 ]]`) so non-tty exec probes (smoke/comm/ssh/gitstate) print nothing;
`CLAUDEMAN_NO_MOTD` opts out, and `hints` re-shows it on demand.

### Supporting CLIs (parity)

`apt install` from Trixie (verify at build): **eza, zoxide, fzf, bat, bash-completion**, optionally
**git-delta**. Binary-name gotchas handled in the rc: Debian ships bat as `batcat` and delta as
`delta`. Curated container-safe alias subset from Omarchy `bash/aliases`:

- **`n`** — the operator's exact function (`nvim .` on no args, else `nvim "$@"`).
- `g` / `gcm` / `gcam`, the `eza` `ls` / `lt` family, the zoxide `cd` override, `..` / `...`,
  `EDITOR=nvim`, `BAT_THEME` + the bat `MANPAGER` (from Omarchy `bash/envs`).
- **Deliberately excluded:** `c` / `cx` / `icx` (launch claude/opencode) — a `claude` started from
  the in-container shell **bypasses the one-claude guard (invariant 6)**, which is host-side and
  can't see it. Also dropped: host-only `d=docker`, `r=rails`, `t=tmux`, `tdl`, and the kitty-only
  `ff`. *(Optional hardening: a `claude` wrapper function that refuses if `/proc` already shows a
  live claude.)*

Writable state for these (zoxide db `_ZO_DATA_DIR`, starship cache, history) all targets the
`.cache` tmpfs by default — ephemeral, no new surface.

## Floor / invariant analysis

- **Invariant 1 (auth):** untouched — no credentials, no `ANTHROPIC_*`. The banner reads only the
  already-injected non-secret env (slug/profile). The excluded claude-launching aliases protect
  invariant 6.
- **Invariant 2 (hardened floor):** the **default path is byte-identical** — everything baked is on
  the read-only rootfs; all writes go to the existing `.cache` tmpfs. The **only** floor change is
  the **opt-in** history bind, which adds exactly one owner-pinned writable surface when explicitly
  enabled, and is gated behind a default-off config bool. `image smoke base` must stay green; a
  separate test covers the opt-in bind.
- **Invariant 6 (one claude):** reinforced — no aliased/baked path launches claude from a shell.

## Smoke / tests / docs

- Extend `docker/smoke.py` (base gate) to assert under `--read-only --user`: interactive bash
  sources the rc (`type n` resolves), `starship --version` runs, the default `HISTFILE` is writable,
  the banner prints, and **neo-tree opens headless** without writing to the read-only rootfs.
- Unit/inspection test: the baked `bashrc`'s first effective line is the non-interactive guard
  (protects the exec probes); the opt-in history bind renders additively (floor unchanged when off).
- Rebuild **base → overlays** explicitly (`image build base`, then node/python/rust) — `up`/`recreate`
  reuse stale images.
- Docs: a "Shell environment" section in the base-image rationale + a CLAUDE.md note; this file is
  the design of record.

## Decisions (resolved)

1. **History persistence** — landed both: default-off ephemeral on the `.cache` tmpfs, plus the
   opt-in persistent bind behind `config shell-history` (8d).
2. **Banner cadence** — shown on **every interactive shell** (operator's call), gated on a real tty
   so non-tty execs stay silent; `CLAUDEMAN_NO_MOTD` opts out, `hints` re-shows.
3. **Alias breadth** — the curated container-safe subset (no claude/opencode/host-only aliases).
