"""Apply accepted sync-back changes to the host + setups repo (Phase 5).

Order (accepted rows only): back up every host target -> copy file-tree artifacts
(symlink-preserving, container->host path rewrite, honouring user/project/repo scope and
the NEW commands/ target) -> field-patch settings.json (hooks/statusLine structurally
immune) -> apply MCP via ``claude mcp add/remove --scope`` -> mirror into
``~/Work/setups/claude-code`` and git-commit (denylist re-asserted at staging) ->
refresh baseline. A per-profile merge lock serialises concurrent reconciles.
"""

from __future__ import annotations


def apply_accepted(slug: str, decisions: dict) -> None:
    raise NotImplementedError("phase 5: review-gated merge — see ROADMAP.md")
