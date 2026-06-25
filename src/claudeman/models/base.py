"""Provider-shaped model-management framework (Phase 9 — issue #14).

A *backend* abstracts a local model server (Ollama first; vLLM later) behind one interface so claude-man
can list / install / update / inspect / remove local models from inside ``claudemanctl model …``. The
PURE return types live here (frozen dataclasses, no IO) so this module imports dependency-free; each
backend (``models/ollama.py``) splits its urllib daemon calls from PURE parsers (the ``updates.py`` /
``usage_api.py`` pattern) and FAILS OPEN — an absent/offline daemon degrades to a ``note``, never raises.

This first cut manages MODELS only; bringing up the model server (and the Phase-9 gateway sidecar that
routes the agent's ``local-*`` calls to it) is separate. ``Project.model`` (a thin per-project pin in the
registry) is the only persisted state — the installed set is always queried LIVE (invariant 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable


@dataclass(frozen=True)
class Liveness:
    """Is the backend daemon reachable? ``note`` explains why not (offline / http NNN / bad resp)."""

    up: bool
    version: str = ""
    note: str = ""


@dataclass(frozen=True)
class InstalledModel:
    """One locally-installed model (from a list call). ``digest`` is the manifest digest used for the
    update probe; ``size`` is bytes on disk."""

    name: str
    digest: str = ""
    size: int = 0
    family: str = ""
    param_size: str = ""
    quant: str = ""
    modified_at: str = ""


@dataclass(frozen=True)
class ModelInfo:
    """Metadata for one model (from a show call): the real context window + capabilities (e.g.
    ``tools`` — the make-or-break for agentic use)."""

    name: str
    context_length: int = 0
    capabilities: tuple[str, ...] = ()
    family: str = ""
    param_size: str = ""
    quant: str = ""
    note: str = ""


@dataclass(frozen=True)
class PullEvent:
    """One line of an install/update progress stream, normalised across the daemon's status strings.

    ``kind`` ∈ {``manifest``, ``layer``, ``verifying``, ``writing``, ``removing``, ``success``,
    ``error``}. For ``layer`` events ``total``/``completed`` are byte counts (a percentage reducer lives
    in the backend); ``status`` keeps the raw line for display/debug.
    """

    kind: str
    digest: str = ""
    total: int = 0
    completed: int = 0
    status: str = ""


@dataclass(frozen=True)
class UpdateStatus:
    """Result of the no-download update probe: is a newer build of this tag available? ``note`` is empty
    when the verdict is known, else why it's unknown (offline / http NNN) — fail-open, like updates.py."""

    name: str
    behind: bool = False
    local_digest: str = ""
    remote_digest: str = ""
    note: str = ""


# Progress callback for a streamed pull (each normalised PullEvent as it arrives). May be None.
ProgressFn = Callable[[PullEvent], None]


@runtime_checkable
class ModelBackend(Protocol):
    """The provider seam. Every method FAILS OPEN — folds daemon/network errors into a ``note`` on the
    returned value rather than raising — so the CLI/TUI degrade gracefully when the server is down."""

    name: str

    def liveness(self) -> Liveness: ...

    def list_models(self) -> tuple[list[InstalledModel], str]:
        """(installed models, note). ``note`` non-empty (and the list empty) when the daemon is
        unreachable."""
        ...

    def show(self, ref: str) -> ModelInfo: ...

    def pull(self, ref: str, *, on_progress: ProgressFn | None = None) -> PullEvent:
        """Install/update ``ref``, streaming progress to ``on_progress``. Returns the terminal event
        (``success`` or ``error``)."""
        ...

    def remove(self, ref: str) -> tuple[bool, str]:
        """(removed?, note). Idempotent — a missing model is ``(True, "already absent")``."""
        ...

    def update_status(self, model: InstalledModel) -> UpdateStatus: ...
