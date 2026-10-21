# Cross-project requests — the relay

One project's agent can ask **another project's agent** for help. The motivating case: the
`infrastructure` agent wants a `dev-server` project to bring the dev system up on a branch it
names, then reply with the URLs. claude-man carries the request between the two containers,
checks it against what the target allows, asks the operator when it should, runs it as one
headless session in the target's own container, and carries the reply back.

Every request and reply is persisted and attributable (there is no private channel between
agents), and nothing about it widens the hardened floor.

## Shape

```
infrastructure container           host: claude-man relay                     dev-server container
────────────────────────           ──────────────────────                     ────────────────────
relay ask dev-server dev-up \
  branch=feat/x --note "…"
  → /workspace/.relay/out/<id>.json
                                   drains every project's .relay/out/ (no-follow IO)
                                   sender = WHOSE bind the file was in (can't be forged)
                                   checks: dev-server's helpdesk allows infrastructure?
                                     known task? exact params? loop / rate guards?
                                   records it + appends to the audit log (state tier)
                                   needs approval? → TUI toast / `relay approve`
                                   prompt = dev-server's OWN template + fenced note
                                   lifecycle.run(dev-server, …)  ── docker exec ──►  claude -p
                                     (busy? waits; stopped? autostart if allowed)       does the work
                                   masked reply + events ◄─────────────────────────── final message
  /workspace/.relay/in/<id>.json ◄─ status/reply written into the requester's bind
relay wait <id> → prints the reply
```

The relay is a host process: the open **TUI** runs it, or `claudemanctl relay run` does. A
single-instance lock in the state tier means exactly one of them acts. Requests sitting in an
outbox while no relay runs are picked up as soon as one starts.

## Configuring a target (the helpdesk)

A project accepts requests only when its `[project.helpdesk]` names at least one sender **and**
offers at least one task:

```toml
[project.helpdesk]
from = ["infrastructure"]      # explicit allowlist of senders (no wildcard)
approve = "operator"           # default for tasks that don't set their own: operator | auto
autostart = false              # start this container to serve a request when it's stopped

[[project.helpdesk.task]]
name = "dev-up"
summary = "Start the dev stack on a branch and report the URLs"
params = { branch = "git ref to check out" }    # all required; {branch} in the prompt
permission = "edits"           # default (read-only consult) | edits | full
approve = "auto"
timeout = 1200                 # seconds (CLI/TUI accept 20m / 2h)
prompt = """
Fetch and check out {branch} in landarna-backend. Start the api and queue under the tmux
session `dev` (kill an existing one first). Wait until the api answers on :8080, then report
the branch, the short commit SHA and the URLs.
"""

[[project.helpdesk.task]]
name = "ask"                   # no prompt = free-form: the requester's note IS the request
summary = "Questions about the dev stack"
```

Set it with `claudemanctl project helpdesk allow|disallow|set|task add|task rm|show`
([CLI.md](CLI.md)) or in the TUI under **Project… → `h` Helpdesk…**
([TUI-GUIDE.md](TUI-GUIDE.md)). Changes apply **immediately**: no recreate, because nothing about
the container changes.

The **target owns everything that shapes the work**: the prompt template, the permission level, the
timeout and the approval rule. A requester picks a task, fills in its params and may add a note.
Nothing it sends can raise the permission or skip an approval.

## What each agent is told

On every `up`, and right after any helpdesk change, claude-man re-stamps a managed block (the same
`claudemd` patcher the scratch note and packs use) into the context file of every affected project
(CLAUDE.md, or AGENTS.md for codex):

- **A requester** (some helpdesk accepts it) gets the list of projects it can ask, each task with
  its params and whether it needs approval, and how to use the helper. It also gets
  `/workspace/.relay/{out,in,bin}`, the helper at `/workspace/.relay/bin/relay`, and
  `/workspace/.relay/directory.json`.
- **A target** gets a short note that requests may arrive as separate headless sessions whose prompt
  starts with `[claude-man relay]`.
- A project that is neither has the block removed and no `.relay` directory.

### The in-container helper

The host writes a dependency-free node script into the bind on every start (the base image ships
node), so it never needs an image rebuild:

| Command | What it does |
|---|---|
| `relay list` | who this project can ask, with their tasks (from `directory.json`) |
| `relay ask <project> <task> [name=value …] [--note TEXT \| --note-file F \| --note -] [--wait] [--timeout S]` | send a request; prints its id |
| `relay wait <id> [--timeout S]` | wait for the reply (default 540 s; exit 0 done, 1 failed/rejected/cancelled, 2 still open — run it again) |
| `relay status [<id>]` | this project's requests and their state |

Param values must be identifier-shaped: letters, digits and `. _ / @ : + = , % -`, no leading
`-`, at most 200 chars. That means no whitespace, quotes, brackets, braces or shell metacharacters,
so a value can't smuggle an instruction or an option into the target's template. Prose (and
anything like a URL with a query string) goes in the note.

## A request's life

`awaiting_approval → queued → running → done | failed`, or `rejected` (by policy, by the operator
denying it, or unapproved for 24 h) or `cancelled` (by the operator, at any point before it
finishes; cancelling a running request stops the run and the agent inside the target).

1. **Ingest.** The relay drains each project's `.relay/out/`. The sender is the project whose bind
   the file was in. The file is parsed (`relay/protocol.py`) and checked against the target's
   helpdesk (`relay/policy.py`). A failed check is answered at once as `rejected`, with the reason.
   Each file is handled in isolation, so one hostile file can't take the others down with it.
2. **Approval.** A request needs the operator when any of these holds:
   - the task (or the helpdesk default) says `operator`;
   - the request crosses the egress lock in either direction (see below);
   - the sender → target pair has used its 30 auto-approved requests for the last 24 h.

   The TUI shows a toast; review it under `r` Requests or with
   `claudemanctl relay list|show|approve|deny`.
3. **Dispatch.** At most one request runs per target at a time. Policy is **re-checked** on every
   relay pass, so a task removed, a sender disallowed or an approval tightened applies at once to
   requests that haven't started. A task whose definition changed since the request was approved
   (or queued) goes back to the operator. Then:
   - the TUI's relay never starts or runs into a project while the operator has a lifecycle action
     in flight on it (its busy guard);
   - a stopped target is started only when its helpdesk has `autostart`, and policy is checked
     again after the start (it can take minutes); otherwise the request waits;
   - a target with an agent already live in it waits, re-probed every 10 s (one agent per container,
     invariant 6);
   - the run is ONE headless session (`lifecycle.run`, the 7-run seam) with the task's permission,
     its timeout (enforced by a watchdog even while the agent is silent; on expiry the exec and the
     in-container agent are both stopped), and the project's claude `--model` pin if it has one.
4. **Reply.** The target agent's final message is secret-masked, capped at 32 KB, and written into
   the requester's `.relay/in/<id>.json`, fenced as another agent's words. The record keeps the
   target's session id, so the operator can `claude --resume` it inside the target.

A queued request not started within 6 h of being queued (or approved) fails as expired. One not
approved within 24 h is rejected. A relay that dies mid-run leaves its request `running`; the next
relay to start marks it failed, so nobody waits forever.

## Security model

The relay keeps every load-bearing invariant ([`../CLAUDE.md`](../CLAUDE.md)) intact:

- **No floor change (invariant 2).** `.relay/` is a subdirectory of the existing `/workspace`
  bind, exactly like `scratch/` and `.tmp/`. There is no new mount, socket, capability or
  writable surface, and the create argv is byte-identical.
- **No network path (invariant 3).** Requests are files moved by a host process. Locked and open
  projects work the same way, and no container ever gains a route to another.
- **No credentials move (invariant 1).** The target runs under its own profile, auth mode and
  egress posture; nothing is injected into either container.
- **One agent per container (invariant 6).** A dispatch is a headless agent run under the same
  `/proc` probe as `spawn_claude`; a busy target makes the request wait.
- **Registry is the source of truth (invariant 4).** Policy is read fresh from the registry at
  ingest and again at dispatch.

The threats this design addresses:

| Threat | Mitigation |
|---|---|
| A container forges who it is | The sender is derived from **which bind** the file appeared in; the request has no sender field. A project can only fake replies to itself. |
| A container plants a symlink, FIFO, hard link or huge file in its mailbox, or swaps a directory for a symlink, to make the HOST read or write outside the bind | `relay/fsio.py`: every path is opened fd-relative from the workspace dir with `O_NOFOLLOW` (a non-directory component is unlinked and recreated, never followed). Reads need a regular, single-link file under 32 KB, opened `O_NONBLOCK`. Writes go to an `O_EXCL` temp file then `rename`, which replaces a planted symlink instead of writing through it. The context-file patch uses the same primitives. |
| Prompt injection from the requester into a more privileged target | The target's own template forms the prompt. Params are identifier-shaped. The note is fenced with a per-request random nonce and labelled as information, not instructions. The permission level is the target's. Free-form tasks default to read-only (`permission = "default"`). |
| Exfiltration across the egress lock | A request between a **locked** and an **open** project always needs the operator's approval, whatever the task says. Locked → open would carry the request's data out of the lock; open → locked would carry the locked project's data out in the reply. |
| Secrets or terminal escapes crossing between projects | Notes, replies and failure details have control characters stripped, are truncated *before* masking (the masker is expensive on huge input), and are then masked line by line (`syncback.denylist.mask_line`: bearer tokens, `key=value` secrets, token-shaped values) before they are stored or delivered. Full 40-hex SHAs are redacted too, so ask for short SHAs. Params are *not* masked: they are identifier-shaped and declared by the target's operator. |
| Agents looping or flooding each other | A request raised by a project while it is serving one is that request's child. Chains deeper than 2 are refused, as is asking a project already in the chain. A project may have at most 5 open requests, and at most 20 are ingested per sender per minute; the excess waits in its outbox. At most 30 rejections per sender per hour are recorded (beyond that the sender only gets the status file), and rejected records are pruned after a day, so a flood can't grow the store or slow the loop. Auto-approval is capped at 30 per sender → target pair per day. |
| Unattributed agent-to-agent traffic | `~/.local/state/claude-man/relay/events.jsonl` is an append-only audit log (who did what, when) in the host-only state tier. Records live in `relay/requests/`. Finished records older than 30 days are pruned when a relay starts; the event log is never pruned. |

Accepted limits:

- Killing the relay process itself ends only the `docker exec` client. An in-flight agent inside
  the target may finish its work unobserved, and the next relay marks the request failed. A timeout
  or cancel, by contrast, stops the in-container agent too: it TERMinates every agent process in the
  target container, which by invariant 6 is only the relay's own run.
- The loop guard only sees *nested* requests. A target that schedules a later request (say, from a
  tmux job) starts a fresh chain. The per-pair daily auto-approval cap and the open-request limit
  bound what that can cost.
- The `directory.json` approval labels are advisory. The relay enforces the live rule.
- Deleting a project removes it from every other helpdesk's sender list (so a new project reusing
  the slug inherits nothing) and rejects its pending requests.

## Operating notes

- **Long-running work.** A process started from a headless session can die with it. The relay's
  prompt tells the target to start servers under `tmux new-session -d`, and task templates should
  say so too.
- **Network reach between projects is out of scope.** If `infrastructure` needs to *call* the dev
  server it asked for, today it goes through published ports on the host. Container-to-container
  networking is a separate feature.
- **Billing.** A request runs on the **target's** profile, which may be a different account from
  the requester's.
- **Waiting from an interactive session.** `relay wait` blocks up to 540 s by default, which fits a
  10-minute shell-tool timeout. For longer tasks the agent simply runs it again.

## Follow-ups

- Threads: follow-up messages that `--resume` the target session for the same request.
- File attachments through `scratch/` (copied host-side, containment-checked).
- Delivery into a live interactive session (a prompt-submit hook) instead of waiting for it to exit.
- A status-bar cue (`⇄ waiting on dev-server` / `📥 dev-up running`). This needs a baked probe, so
  an image rebuild.
- An MCP wrapper around the helper, for agents that prefer tools to shell commands.
