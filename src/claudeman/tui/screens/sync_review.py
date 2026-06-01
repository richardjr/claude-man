"""Sync-back accept/reject gate (Phase 5).

A DataTable of changed artifacts (artifact | kind | change-type | scope | default-decision)
beside a RichLog rendering the selected artifact's secret-masked diff. Bindings:
a=accept r=reject s=skip A=accept-all-non-default-reject space=toggle enter=apply.
Defaults: authored text accept; settings.json/MCP/deletions/conflicts reject. Nothing is
written until enter.
"""

from __future__ import annotations

# Implemented in Phase 5 — see ROADMAP.md and the syncback/ package.
