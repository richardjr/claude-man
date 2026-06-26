"""Ollama backend for the model-management framework (Phase 9 — issue #14).

Drives the HOST Ollama daemon's HTTP API (default ``127.0.0.1:11434``, no auth) over stdlib ``urllib``
— no new dependency, no shelling out to the ``ollama`` CLI. The PURE parsers (``parse_tags`` /
``parse_pull_line`` / ``aggregate_pull_progress`` / ``parse_show`` / ``split_ref`` / ``update_verdict``)
are split from the network IO so they unit-test with no daemon (the ``updates.py`` / ``egress.py``
pattern). Every IO method FAILS OPEN — folds a daemon/network error into a note/terminal-error value
rather than raising — so an absent or offline Ollama degrades gracefully.

Update detection is a TOKEN-LESS registry manifest-digest probe (no multi-GB pull): compare the local
manifest digest (from ``/api/tags``) with the registry's ``Docker-Content-Digest`` for the same tag.
Applying an update is just ``pull`` again (incremental — Ollama skips blobs already present).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .. import config
from .base import InstalledModel, Liveness, ModelInfo, ProgressFn, PullEvent, UpdateStatus

_SHORT_TIMEOUT_S = 4.0      # version / tags / show / registry probe — tiny responses
_DELETE_TIMEOUT_S = 30.0
_PULL_TIMEOUT_S = 3600.0    # a multi-GB download streams for a long time

# The registry media types Ollama's manifest endpoint speaks (Docker/OCI v2 + Ollama's own).
_MANIFEST_ACCEPT = ", ".join((
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.ollama.image.manifest.v1+json",
))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Drop redirects (anomalous on the fixed localhost API / registry manifest endpoint); mirrors
    updates.py's hardened opener so a 30x can't redirect us into parsing an unrelated body."""

    def redirect_request(self, *args, **kwargs):  # noqa: ARG002 - intentionally drop all redirects
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


# ---------------------------------------------------------------------------
# Pure layer (unit-tested without sockets)
# ---------------------------------------------------------------------------
def normalize_digest(d: str) -> str:
    """A bare lowercase hex digest — strips an algorithm prefix (``sha256:abc`` -> ``abc``) so a local
    ``/api/tags`` digest and a registry ``Docker-Content-Digest`` compare apples-to-apples."""
    return (d or "").split(":")[-1].strip().lower()


def split_ref(ref: str) -> tuple[str, str, str]:
    """``model[:tag]`` -> ``(namespace, model, tag)`` for the registry manifest path. A bare name is in
    the ``library`` namespace and defaults to the ``latest`` tag (Ollama's scheme)."""
    name, _, tag = ref.strip().partition(":")
    tag = tag or "latest"
    if "/" in name:
        ns, _, model = name.partition("/")
        return ns or "library", model, tag
    return "library", name, tag


def parse_tags(obj: object) -> list[InstalledModel]:
    """``GET /api/tags`` body -> installed models. Tolerates missing fields/garbage (drops nameless)."""
    out: list[InstalledModel] = []
    models = obj.get("models") if isinstance(obj, dict) else None
    for m in models or []:
        if not isinstance(m, dict):
            continue
        details = m.get("details") if isinstance(m.get("details"), dict) else {}
        name = str(m.get("name") or m.get("model") or "")
        if not name:
            continue
        out.append(InstalledModel(
            name=name,
            digest=normalize_digest(str(m.get("digest", ""))),
            size=int(m.get("size") or 0),
            family=str(details.get("family", "")),
            param_size=str(details.get("parameter_size", "")),
            quant=str(details.get("quantization_level", "")),
            modified_at=str(m.get("modified_at", "")),
        ))
    return out


def parse_pull_line(obj: object) -> PullEvent:
    """One ``/api/pull`` NDJSON object -> a normalised PullEvent (manifest/layer/verifying/writing/
    removing/success/error). Layer lines carry the byte counters; everything else maps by status."""
    if not isinstance(obj, dict):
        return PullEvent(kind="error", status="bad line")
    if obj.get("error"):
        return PullEvent(kind="error", status=str(obj["error"]))
    status = str(obj.get("status", ""))
    low = status.lower()
    # Terminal/structural lines first (note: "writing manifest" must beat the bare "manifest" check).
    if low == "success":
        return PullEvent(kind="success", status=status)
    if low.startswith("verifying"):
        return PullEvent(kind="verifying", status=status)
    if low.startswith("writing"):
        return PullEvent(kind="writing", status=status)
    if low.startswith("removing"):
        return PullEvent(kind="removing", status=status)
    if low == "pulling manifest":
        return PullEvent(kind="manifest", status=status)
    digest = str(obj.get("digest", ""))
    total = int(obj.get("total") or 0)
    completed = int(obj.get("completed") or 0)
    if digest or total or completed:
        return PullEvent(kind="layer", digest=normalize_digest(digest),
                         total=total, completed=completed, status=status)
    return PullEvent(kind="manifest", status=status)   # any other info line


def aggregate_pull_progress(events: list[PullEvent]) -> float:
    """Overall percent across the layer events (latest completed/total per layer, summed). 0.0 when no
    byte-bearing layer has been seen yet."""
    layers: dict[str, tuple[int, int]] = {}
    for e in events:
        if e.kind == "layer" and e.total:
            layers[e.digest or e.status] = (e.completed, e.total)
    total = sum(t for _, t in layers.values())
    done = sum(c for c, _ in layers.values())
    return (100.0 * done / total) if total else 0.0


def parse_show(obj: object, name: str = "") -> ModelInfo:
    """``POST /api/show`` body -> ModelInfo. ``context_length`` is the first ``*.context_length`` in
    ``model_info`` (key is architecture-prefixed, e.g. ``qwen3moe.context_length``)."""
    obj = obj if isinstance(obj, dict) else {}
    details = obj.get("details") if isinstance(obj.get("details"), dict) else {}
    info = obj.get("model_info") if isinstance(obj.get("model_info"), dict) else {}
    ctx = 0
    for k, v in info.items():
        if k.endswith(".context_length") and isinstance(v, (int, float)):
            ctx = int(v)
            break
    caps = obj.get("capabilities")
    caps_t = tuple(str(c) for c in caps) if isinstance(caps, list) else ()
    return ModelInfo(
        name=name,
        context_length=ctx,
        capabilities=caps_t,
        family=str(details.get("family", "")),
        param_size=str(details.get("parameter_size", "")),
        quant=str(details.get("quantization_level", "")),
    )


def update_verdict(name: str, local_digest: str, remote_digest: str, note: str = "") -> UpdateStatus:
    """Pure verdict: a newer build is available iff both digests are known and differ. ``note`` (offline
    / http NNN) means the verdict is UNKNOWN — never reported as behind (fail-open)."""
    ld, rd = normalize_digest(local_digest), normalize_digest(remote_digest)
    if note:
        return UpdateStatus(name=name, local_digest=ld, remote_digest=rd, note=note)
    return UpdateStatus(name=name, behind=bool(ld and rd and ld != rd), local_digest=ld, remote_digest=rd)


# ---------------------------------------------------------------------------
# Network (needs a daemon; not unit-tested) — every method fails OPEN
# ---------------------------------------------------------------------------
class OllamaBackend:
    """The Ollama implementation of the ``ModelBackend`` protocol (models/base.py)."""

    name = "ollama"

    def __init__(self, base_url: str | None = None) -> None:
        self.base = (base_url or config.ollama_url()).rstrip("/")

    # -- internal --
    def _json(self, method: str, path: str, body: dict | None = None, *, timeout: float):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read()
        return json.loads(raw) if raw.strip() else {}

    def liveness(self) -> Liveness:
        try:
            obj = self._json("GET", "/api/version", timeout=_SHORT_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            return Liveness(False, note=f"http {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return Liveness(False, note=f"no Ollama daemon at {self.base}")
        return Liveness(True, version=str((obj or {}).get("version", "")))

    def list_models(self) -> tuple[list[InstalledModel], str]:
        try:
            obj = self._json("GET", "/api/tags", timeout=_SHORT_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            return [], f"http {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return [], f"no Ollama daemon at {self.base}"
        return parse_tags(obj), ""

    def show(self, ref: str) -> ModelInfo:
        try:
            obj = self._json("POST", "/api/show", {"model": ref}, timeout=_SHORT_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            return ModelInfo(name=ref, note=f"http {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return ModelInfo(name=ref, note=f"no Ollama daemon at {self.base}")
        return parse_show(obj, name=ref)

    def pull(self, ref: str, *, on_progress: ProgressFn | None = None) -> PullEvent:
        """Stream ``POST /api/pull``; emit each normalised event to ``on_progress``. Returns the terminal
        event. Re-running is safe + incremental (Ollama skips blobs already present)."""
        body = json.dumps({"model": ref, "stream": True}).encode()
        req = urllib.request.Request(self.base + "/api/pull", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        last = PullEvent(kind="error", status="no response")
        try:
            with _OPENER.open(req, timeout=_PULL_TIMEOUT_S) as resp:
                for raw in resp:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except ValueError:
                        continue
                    ev = parse_pull_line(obj)
                    last = ev
                    if on_progress:
                        on_progress(ev)
                    if ev.kind == "error":
                        return ev
        except urllib.error.HTTPError as exc:
            return PullEvent(kind="error", status=f"http {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError):
            return PullEvent(kind="error", status=f"no Ollama daemon at {self.base}")
        return last

    def remove(self, ref: str) -> tuple[bool, str]:
        try:
            self._json("DELETE", "/api/delete", {"model": ref}, timeout=_DELETE_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return True, "already absent"
            return False, f"http {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return False, f"no Ollama daemon at {self.base}"
        return True, ""

    def update_status(self, model: InstalledModel) -> UpdateStatus:
        """Token-less registry manifest-digest probe — is a newer build of this tag available, WITHOUT a
        multi-GB pull? Reads the registry's ``Docker-Content-Digest`` and compares to the local digest."""
        ns, name, tag = split_ref(model.name)
        url = f"{config.OLLAMA_REGISTRY_URL}/v2/{ns}/{name}/manifests/{tag}"
        req = urllib.request.Request(url, headers={"Accept": _MANIFEST_ACCEPT}, method="HEAD")
        try:
            with _OPENER.open(req, timeout=_SHORT_TIMEOUT_S) as resp:
                remote = resp.headers.get("Docker-Content-Digest", "")
        except urllib.error.HTTPError as exc:
            return update_verdict(model.name, model.digest, "", note=f"http {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError):
            return update_verdict(model.name, model.digest, "", note="offline")
        if not remote:
            return update_verdict(model.name, model.digest, "", note="no digest")
        return update_verdict(model.name, model.digest, remote)


def get_backend(name: str = "ollama", *, base_url: str | None = None) -> OllamaBackend:
    """Resolve a model backend by name. Only ``ollama`` exists today (vLLM later — same interface)."""
    if name != "ollama":
        raise ValueError(f"unknown model backend {name!r} (only 'ollama' today)")
    return OllamaBackend(base_url=base_url)
