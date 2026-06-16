# Issue #3 — Browse (`b`) opens nothing on WSL2

- **Issue:** https://github.com/richardjr/claude-man/issues/3
- **Reporter:** @Samdh125 (WSL2)
- **Filed:** 2026-06-15 · **Note updated:** 2026-06-16
- **Status:** Awaiting reporter info — follow-up questions posted
  ([comment](https://github.com/richardjr/claude-man/issues/3#issuecomment-4716738772)).
  Blocked on a real WSL2 config to confirm the GUI-open end to end.

## Symptom (reporter's words)

> Pressing 'b' to open a browser window prints to the console, but no window opens.

## What Browse actually does

`b` is **not** a web browser — the issue title is a misnomer (clarified with the reporter). It opens
the project's **host workspace directory** (the `/workspace` bind source) in the **system file
manager**:

- `app.action_browse` (`src/claudeman/tui/app.py` ~638) → `terminals.spawn_path(<workspace dir>)`.
- `terminals._pick_opener` (`src/claudeman/tui/terminals.py` ~306) resolves the opener: a configured
  `[opener] command` if set, else the platform default. **WSL2 candidate order:**
  `wslview` → `xdg-open` → `gio open` → `explorer.exe` (first one found on `PATH` wins).
- `terminals.spawn_path` (~337) `Popen`s it detached (`start_new_session=True`) with
  **stdout/stderr → DEVNULL** and **no exit-code/stderr check** (fire-and-forget). The
  `explorer.exe` branch translates the path with `wslpath -w` first.
- WSL detection: `hostplatform.is_wsl` (~44) — true if `$WSL_DISTRO_NAME` is set or `/proc/version`
  contains `microsoft`.

## Reproduced locally (Arch — no Windows needed)

Faking WSL detection flips `is_wsl()` true, so we can watch the selection logic:

```bash
WSL_DISTRO_NAME=test uv run python -c \
  "from claudeman.tui import terminals; print(terminals._pick_opener())"
# -> ['xdg-open']
```

So on a WSL2 box that has `xdg-open` (ships in `xdg-utils` — almost always present) but **no
`wslu`/`wslview`**, we choose `xdg-open` and **never reach** the reliable `explorer.exe` interop
fallback. On a GUI-less WSL2 (no WSLg, no Linux file manager) `xdg-open` has nothing to launch → no
window opens, and `xdg-open` emits a "no method available" diagnostic. Because `spawn_path` is
fire-and-forget with DEVNULL'd output, the failure is swallowed and surfaces only as "nothing
happened" (the TUI still logs a green `browsing <path>`).

## Root-cause hypotheses (ranked)

1. **No `wslu` + opener-precedence bug.** A present-but-nonfunctional `xdg-open` shadows the
   Windows-interop openers (`explorer.exe`/`wslview`) that actually work on headless WSL2. The README
   already nudges "install `wslu` for the best Browse experience" — likely the reporter's exact state.
   *(Both a user remedy and a real code bug.)*
2. **Silent failure by design.** `spawn_path`/`action_browse` never inspect the opener's exit code or
   stderr, so the true error is hidden and any opener chatter looks like stray console output.
3. **`is_wsl()` mis-detects → False** → only `xdg-open`/`gio` are tried, never the Windows openers.
   Unlikely (`$WSL_DISTRO_NAME` + `/proc/version` are reliable) — confirm via the reporter's
   `$WSL_DISTRO_NAME`.
4. **`explorer.exe` chosen but path translation / permissions fail** → window doesn't open. Less
   likely; the `wslpath -w` handling looks correct.

**Still unresolved without the reporter:** the exact text that "prints to the console" — it
disambiguates 1 vs 2 vs 3.

## Info requested from the reporter (posted on the issue)

1. Exact printed text (red `browse failed: …` / green `browsing …` / raw terminal output?).
2. `which wslview xdg-open gio explorer.exe`; `echo $WSL_DISTRO_NAME`;
   `echo "$DISPLAY $WAYLAND_DISPLAY"`; `wslview --version`.
3. WSLg vs headless WSL2 (`wsl --version` on the Windows side).
4. Do `explorer.exe .` / `wslview .` open a window when run directly in the workspace dir?
5. Custom opener set? (`claudemanctl config show` → `opener:` line.)
6. claude-man version/commit + terminal (Windows Terminal?).

## Proposed fix direction (do once we can test on real WSL2)

- **Reorder WSL2 openers** so the Windows-interop openers (`wslview`, then `explorer.exe`) are
  preferred over a GUI-less `xdg-open`/`gio` — or only fall back to `xdg-open`/`gio` when a display is
  actually present (`$DISPLAY`/`$WAYLAND_DISPLAY`, i.e. WSLg). (`terminals._pick_opener`, WSL2 branch.)
- **Surface opener failure** instead of swallowing it: a short post-spawn `poll()` (or capture stderr
  with a small timeout) and `_log` the real error, so "no window" is never silent. Point the message
  at `wslu` / `config opener`. (`terminals.spawn_path` + `app.action_browse`.)
- **Docs:** make the `wslu` recommendation more prominent and hint it in the failure message.
- Keep the existing `config opener` override as the escape hatch.

## How to test locally (Arch/Omarchy — mostly no Windows)

1. **Opener selection (cheap):**
   `WSL_DISTRO_NAME=test uv run python -c "from claudeman.tui import terminals; print(terminals._pick_opener())"`.
2. **Exact argv (shims):** put fake `wslview`/`explorer.exe`/`wslpath` on `PATH` (each just
   `echo "$0 $@"`), set `WSL_DISTRO_NAME`, call `terminals.spawn_path(<dir>)` — confirms the argv
   including the `wslpath -w` translation branch.
3. **Unit tests (durable, dependency-free):** add to `tests/test_terminals.py`, monkeypatching
   `is_wsl`/`shutil.which`/settings, asserting the chosen opener per scenario: `wslview` present;
   `wslview` absent + `xdg-open` present (with display vs without); only `explorer.exe`; custom opener.
4. **Real WSL2 (only to confirm a window truly opens):** Windows 11 VM under QEMU/KVM with nested
   virtualization (`kvm_amd`/`kvm_intel nested=1`, host-passthrough CPU) then `wsl --install`; or a
   `windows-latest` CI job with `Vampire/setup-wsl` (non-interactive, no GUI but real WSL2).

## Acceptance criteria (when picked up)

- On WSL2 without `wslu`, `b` either opens a window (via `explorer.exe`/`wslview`) or logs a clear,
  actionable error (pointing at `wslu` / `config opener`) — never silently does nothing.
- New unit tests pin the WSL2 opener precedence.
- Reporter confirms the fix on their box.

## Touchpoints

- `src/claudeman/tui/terminals.py` — `_pick_opener` (~306), `build_open_path_argv` (~326),
  `spawn_path` (~337)
- `src/claudeman/tui/app.py` — `action_browse` (~638)
- `src/claudeman/hostplatform.py` — `is_wsl` (~44)
- `README.md` — Platform-support WSL2 row + Browse/`wslu` notes
- `tests/test_terminals.py` — where the new opener-precedence tests belong
