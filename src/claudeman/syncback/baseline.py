"""Three-way baseline manifest (Phase 5).

At session start, snapshot the seeded config dir AND the current host targets so the
session-close diff can detect both container-side and host-side drift. sha256 for
file trees, canonical-JSON-subtree hash for settings.json/MCP, link-target string for
symlinked skills. Written to ``config.baseline_path(slug)`` — a sibling of the mount,
never inside it.
"""

from __future__ import annotations


def write_baseline(slug: str) -> None:
    raise NotImplementedError("phase 5: baseline snapshot — see ROADMAP.md")


def read_baseline(slug: str) -> dict:
    raise NotImplementedError("phase 5: baseline read — see ROADMAP.md")
