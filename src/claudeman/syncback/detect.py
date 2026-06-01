"""Change detection (Phase 5).

Enforce ``denylist`` FIRST (before any read), then re-hash each allowlisted artifact
and classify vs the baseline: unchanged / added / modified / deleted (per-key for JSON).
A three-way conflict (host changed since seed AND container changed) is flagged and
forced to manual review.
"""

from __future__ import annotations


def detect_changes(slug: str) -> list:
    raise NotImplementedError("phase 5: change detection — see ROADMAP.md")
