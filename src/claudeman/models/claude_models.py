"""Curated claude ``--model`` picker rows (the per-project claude-model pin).

A PURE in-repo data table (the ``presets.py`` shape) of the Claude models an operator can launch
the in-container claude with, plus the ref-shape helper the raw-input path uses to tell a claude
ref from an ollama tag. Aliases (``opus``) track the latest model of their tier — preferred over
full ids where an alias exists so the table doesn't rot with releases; full ids (or any bracket
variant like ``claude-sonnet-5[1m]``) can always be typed raw. The pin is LAUNCH-time argv only
(``terminals.spawn_claude``) — no container change, no gateway; whether the account is entitled to
the model stays claude's own concern (an unentitled pick errors inside claude, not in claude-man).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_REF_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*(\[[a-z0-9]+\])?$", re.IGNORECASE)

# Bare aliases the claude CLI resolves itself (the latest model of a tier). Used only to
# disambiguate a RAW-typed ref — validity stays claude's concern.
ALIASES = frozenset({"opus", "sonnet", "haiku", "fable", "opusplan"})


@dataclass(frozen=True)
class ClaudeModel:
    ref: str    # the --model value (an alias or a full model id)
    label: str  # human label
    note: str = ""


# Ordered as surfaced in the picker. Fable 5 uses the full id (no alias documented); the tiers
# below use aliases so the table survives model releases.
CLAUDE_MODELS: tuple[ClaudeModel, ...] = (
    ClaudeModel("claude-fable-5", "Fable 5",
                "premium Mythos-class tier — the plan/seat must include it"),
    ClaudeModel("opus", "Opus (latest)", "the claude default on most plans"),
    ClaudeModel("sonnet", "Sonnet (latest)", "faster/cheaper tier"),
    ClaudeModel("haiku", "Haiku (latest)", "fastest tier"),
)


def is_claude_ref(ref: str) -> bool:
    """True when a raw-typed ``ref`` names a CLAUDE model rather than an ollama tag.

    The raw-input disambiguator (TUI Model picker; the CLI has an explicit ``--claude`` flag
    instead). A colon marks an ollama ``name:tag``; otherwise a ``claude-`` prefix or a known bare
    alias is a claude ref. Heuristic only — a claude-rejected alias errors inside claude."""
    if ":" in ref:
        return False
    low = ref.lower()
    return low.startswith("claude-") or low in ALIASES


def lint_claude_models() -> list[str]:
    """Structural problems with the shipped table (empty == clean). Asserted by a test so a bad
    edit (dup ref, malformed ref, missing label) is caught dependency-free."""
    problems: list[str] = []
    seen: set[str] = set()
    for m in CLAUDE_MODELS:
        if not _REF_RE.match(m.ref):
            problems.append(f"claude model ref {m.ref!r} is malformed")
        if m.ref in seen:
            problems.append(f"duplicate claude model ref {m.ref!r}")
        seen.add(m.ref)
        if not m.label:
            problems.append(f"claude model {m.ref!r}: label is required")
        if not is_claude_ref(m.ref):
            problems.append(f"claude model ref {m.ref!r} would not classify as a claude ref "
                            f"(is_claude_ref) — add the alias or use a claude- id")
    return problems
