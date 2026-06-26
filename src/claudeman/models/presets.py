"""Curated 'recommended coding models' presets (Phase 9 — issue #14).

A PURE in-repo data table (curation = edit-file-commit, the ``library/packs/`` shape) mapping a short
claude-man key to a concrete ``ollama pull`` tag plus the facts an operator needs to choose: parameter
count, download size, the realistic single-GPU VRAM tier, and an HONEST tool-calling note (agentic
tool-use fidelity is the make-or-break for Claude Code, and it is genuinely shaky on local models).

Presets are a convenience over raw tags — ``model add <preset-key>`` resolves to the tag, and
``model add <raw-tag>`` always works for anything not listed. Tags drift as the ecosystem moves; this
table is curated and updated by commit. The shipped reference/default is Qwen3-Coder-30B-A3B, the only
genuinely capable agentic coder that fits a single 24 GB consumer GPU.

Two HOST-daemon config traps to surface (operator actions claude-man documents, can't set from a
container): a deliberately LARGE context (``OLLAMA_CONTEXT_LENGTH`` — the default is VRAM-gated low and
silently truncates agentic context) and a known-good tool-call template (the stock Qwen3-Coder template
drops the opening ``<tool_call>`` tag). See docs/MODELS.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[a-z0-9][a-z0-9._-]*)?$", re.IGNORECASE)


@dataclass(frozen=True)
class Preset:
    key: str            # claude-man preset key (slug)
    tag: str            # the `ollama pull` tag
    label: str          # human label
    params: str         # e.g. "30B (3.3B active, MoE)"
    size_gb: float      # approx download size
    vram_gb: int        # realistic single-GPU tier
    tool_use: str       # honest tool-calling note
    note: str = ""
    default: bool = False


# Ordered as surfaced. Qwen3-Coder-30B-A3B is the reference/default; the rest span VRAM tiers.
PRESETS: tuple[Preset, ...] = (
    Preset(
        key="qwen3-coder", tag="qwen3-coder:30b", label="Qwen3-Coder 30B-A3B",
        params="30B (3.3B active, MoE)", size_gb=19.0, vram_gb=24,
        tool_use="shaky with the stock template (drops the opening <tool_call> tag) — pin a known-good "
                 "template; set a large OLLAMA_CONTEXT_LENGTH",
        note="reference / default coding model; only capable agentic coder that fits a 24 GB GPU",
        default=True,
    ),
    Preset(
        key="devstral", tag="devstral:24b", label="Devstral Small (24B)",
        params="24B dense", size_gb=15.0, vram_gb=24,
        tool_use="agentic/tool-use focused (Mistral); often steadier in agent loops than Qwen at the "
                 "same tier",
        note="strong second pick for tool-heavy agent work",
    ),
    Preset(
        key="gpt-oss-20b", tag="gpt-oss:20b", label="gpt-oss 20B",
        params="20B", size_gb=14.0, vram_gb=16,
        tool_use="native function-calling — the most reliable low-VRAM tool-caller here",
        note="low-VRAM fallback (16 GB GPU)",
    ),
    Preset(
        key="qwen2.5-coder-7b", tag="qwen2.5-coder:7b", label="Qwen2.5-Coder 7B",
        params="7B dense", size_gb=5.0, vram_gb=8,
        tool_use="limited agentic reliability — best for generation/autocomplete, not long tool loops",
        note="smallest genuinely useful coder (8 GB GPU)",
    ),
    Preset(
        key="qwen2.5-coder-32b", tag="qwen2.5-coder:32b", label="Qwen2.5-Coder 32B",
        params="32B dense", size_gb=20.0, vram_gb=24,
        tool_use="proven dense generation; tool-use weaker than the agentic-tuned picks above",
        note="proven dense 32B (24 GB GPU)",
    ),
)


def lint_presets() -> list[str]:
    """Return a list of structural problems with the shipped table (empty == clean). Asserted by a test
    so a bad edit (dup key, malformed tag, no/multiple defaults, missing VRAM) is caught dependency-free."""
    problems: list[str] = []
    seen: set[str] = set()
    for p in PRESETS:
        if not _SLUG_RE.match(p.key):
            problems.append(f"preset key {p.key!r} is not a slug")
        if p.key in seen:
            problems.append(f"duplicate preset key {p.key!r}")
        seen.add(p.key)
        if not _TAG_RE.match(p.tag):
            problems.append(f"preset {p.key!r}: malformed ollama tag {p.tag!r}")
        if p.vram_gb <= 0:
            problems.append(f"preset {p.key!r}: vram_gb must be > 0")
        if not p.tool_use:
            problems.append(f"preset {p.key!r}: tool_use note is required (the make-or-break)")
    defaults = [p.key for p in PRESETS if p.default]
    if len(defaults) != 1:
        problems.append(f"exactly one preset must be default, found {defaults}")
    return problems


def resolve_preset(key: str) -> Preset | None:
    """The preset for ``key``, or ``None`` if ``key`` isn't a preset (caller treats it as a raw tag)."""
    for p in PRESETS:
        if p.key == key:
            return p
    return None


def default_preset() -> Preset:
    """The reference/default coding model (Qwen3-Coder)."""
    return next(p for p in PRESETS if p.default)
