"""Per-session scratch / data-transfer directory (``/workspace/scratch``).

A known drop-zone for moving files in and out of a container. It is a SUBDIR of the existing
``/workspace`` bind — not a new mount — so the hardened floor is untouched (invariant 2). The
lifecycle WIPES it on every container start and stop (``clear``), so it never persists across
sessions: stage inputs there while the container runs, collect outputs before stop, but keep nothing
durable in it.

``ensure_note`` patches a small claude-man-owned managed block into ``/workspace/CLAUDE.md`` telling
the agent to look here for "provided files" — so when the operator drops a file in and says "check
the data", the agent knows where to look. The block is rewritten in place each start (operator
content outside it is preserved), via the shared ``claudemd`` patcher; it coexists with the packs
block. Pure stdlib (no docker/textual), so the CLI and lifecycle import it freely.
"""

from __future__ import annotations

import shutil

from . import claudemd, config
from .checkout.repos import is_within
from .registry.schema import Project

# Managed-block markers — a distinct prefix from the packs block (``claude-man:packs``) so both can
# live in one CLAUDE.md without colliding (claudemd.patch_block matches by prefix).
_BEGIN = "<!-- claude-man:scratch (managed — edits inside this block are overwritten) -->"
_END = "<!-- /claude-man:scratch -->"
_BEGIN_PREFIX = "<!-- claude-man:scratch"
_END_PREFIX = "<!-- /claude-man:scratch"

# The block's interior. Static prose pointing the agent at the scratch dir. Kept terse — it rides
# every project's CLAUDE.md.
_NOTE_LINES = [
    "## Scratch / data-transfer directory",
    "",
    f"`{config.CONTAINER_SCRATCH}/` is a shared drop-zone for moving files in and out of this",
    "container. When you are asked to look for provided files, inputs, attachments, or \"the data\"",
    f"and no path is given, check `{config.CONTAINER_SCRATCH}/` first.",
    "",
    "It is **wiped on every container start and stop**, so treat it as ephemeral — never leave",
    "anything there you want to keep. Write durable output into a repo under `/workspace/`, not",
    f"into `{config.CONTAINER_SCRATCH}/`.",
]

# Minimal CLAUDE.md created when a project has none (so the note always has a host, even with asset
# sync disabled — the agent reads /workspace/CLAUDE.md regardless of claude-man's sync settings).
def _stub(slug: str) -> str:
    return f"# CLAUDE.md — project: {slug}\n"


def clear(slug: str) -> str:
    """Wipe + recreate the per-session scratch dir (the start/stop hook). Best-effort.

    Containment-checked against the workspace bind so a corrupted path can never delete outside it.
    Returns a human note for the lifecycle Result ('' = quiet success); NEVER raises — a clear fault
    must not block a container start or make a stop look failed. Foreign-owned files (a uid mismatch
    between host and the container's uid 1000) can survive the wipe; that residue is noted, not fatal.
    """
    ws = config.workspace_dir(slug)
    d = config.scratch_dir(slug)
    if not is_within(d, ws):  # defence — only ever operate inside the workspace bind
        return f"scratch: refused to clear {d} (escapes {ws})"
    try:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)  # best-effort: tolerate a foreign-owned leftover
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"scratch: clear failed ({exc})"
    try:
        if any(d.iterdir()):
            return "scratch: some files could not be removed (foreign-owned?)"
    except OSError:
        pass
    return ""


def ensure_note(project: Project) -> str:
    """Patch the managed scratch-dir block into the project's ``/workspace/CLAUDE.md`` (the bind copy
    the agent reads). Unconditional — independent of pack/sync settings — so the agent is always told
    about the scratch dir. Best-effort; returns a note ('' = quiet / unchanged). Creates a minimal
    CLAUDE.md when the project has none so the note has a host."""
    path = config.workspace_dir(project.slug) / "CLAUDE.md"
    existed = path.exists()
    try:
        text = path.read_text(encoding="utf-8") if existed else _stub(project.slug)
        patched = claudemd.patch_block(text, _NOTE_LINES, begin=_BEGIN, end=_END,
                                       begin_prefix=_BEGIN_PREFIX, end_prefix=_END_PREFIX)
        if not existed or patched != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(patched, encoding="utf-8")
    except OSError as exc:
        return f"scratch: CLAUDE.md note patch failed ({exc})"
    return ""
