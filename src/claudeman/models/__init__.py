"""Dynamic model-management framework (Phase 9 — issue #14).

``models/base.py`` defines the provider-shaped ``ModelBackend`` interface + pure return types;
``models/ollama.py`` implements it against the host Ollama daemon (stdlib urllib, fail-open);
``models/presets.py`` is the curated 'recommended coding models' table. ``get_backend()`` is the entry
point used by the CLI/TUI. Importing this package pulls in no third-party dependency.
"""

from __future__ import annotations

from . import presets
from .base import InstalledModel, Liveness, ModelInfo, PullEvent, UpdateStatus
from .ollama import get_backend

__all__ = [
    "get_backend",
    "presets",
    "InstalledModel",
    "Liveness",
    "ModelInfo",
    "PullEvent",
    "UpdateStatus",
]
