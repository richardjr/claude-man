"""New-project form (Phase 1/3).

A form for slug, profile (Select, defaulting to the default profile), overlay, egress
mode, and a repos sub-list. On submit: write projects/<slug>.toml, host-side git clone,
seed claude-config/, and `docker create` the hardened container.
"""

from __future__ import annotations

# Implemented in Phase 1/3 — see ROADMAP.md and registry/projects.py + checkout/repos.py.
