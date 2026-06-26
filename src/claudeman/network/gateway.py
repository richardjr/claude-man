"""Hybrid-model gateway orchestration (Phase 9 — issue #14): a per-project LiteLLM sidecar.

Mirrors the squid ``egress.py`` pattern — PURE ``*_argv`` / config renderers (unit-tested without a
daemon) + thin daemon wrappers over ``docker/runner._run``. The sidecar fronts BOTH legs of hybrid mode
on one Anthropic ``/v1/messages`` endpoint the agent points ``ANTHROPIC_BASE_URL`` at:

  * **Claude leg — passthrough.** Any ``claude-*`` id the agent sends (the full canonical wire id, e.g.
    ``claude-opus-4-8``) routes to Anthropic via the ``model_list`` wildcard. The agent's claude.ai OAuth
    ``Authorization`` reaches Anthropic on LiteLLM's DEDICATED anthropic path (``clean_headers`` preserves
    it when the proxy was authed via ``x-litellm-api-key``; ``optionally_handle_anthropic_oauth`` drops
    ``x-api-key`` and sets ``anthropic-beta: oauth-2025-04-20``) — so the SUBSCRIPTION is used (no console
    key anywhere — invariant 1). The agent authenticates to THIS proxy via ``x-litellm-api-key`` (the
    master key, checked BEFORE ``Authorization`` in LiteLLM auth) so its ``Authorization`` stays free for
    the upstream OAuth. ``forward_client_headers_to_llm_api: true`` is kept for the ``anthropic-beta`` +
    agent ``x-*`` headers — it does NOT itself forward ``Authorization`` (that rides the dedicated path
    above); the OAuth survives regardless.
  * **Local leg — translate.** ``claude-local-<model>`` routes to the HOST Ollama daemon
    (``ollama_chat/<model>`` + ``api_base`` over ``host.docker.internal``). Its EXACT ``model_list`` row
    beats the ``claude-*`` wildcard by precedence, so the local id never leaks to Anthropic.

Both appear in Claude Code's ``/model`` picker (the local one via ``ANTHROPIC_CUSTOM_MODEL_OPTION``) and
switch mid-session.

NOTE (issue #14, 9a): the original blocker — Claude requests 404'ing as ``model: opus-4-8`` — was a
model-MAPPING bug, not an auth bug (a 404 not a 401 proves the OAuth already reached Anthropic). The old
``model: anthropic/*`` wildcard captured the suffix of ``claude-opus-4-8`` (``opus-4-8``) and forwarded
that invalid id; ``anthropic/claude-*`` preserves the ``claude-`` prefix so the full valid id is
forwarded (LiteLLM substitutes the captured ``*`` into the target ``*`` — verified against
``pattern_match_deployments.set_deployment_model_name``). The one thing config can't settle is whether a
RE-ORIGINATED request keeps the Max subscription LANE (LiteLLM re-originates TLS via httpx and replaces
``User-Agent``) — that needs a live capture (read the response header
``anthropic-ratelimit-unified-overage-in-use``: ``false`` = subscription kept). See
``docs/issue-14-hybrid-passthrough-protocol.md``. The master KEY is the only secret — state-tier
``0600``, injected pass-through (never in the rendered config, never config.toml/synced).
"""

from __future__ import annotations

import json
import os
import secrets

from .. import config, hostplatform
from ..docker import runner
from ..models import ollama


# ---------------------------------------------------------------------------
# Pure renderers (no daemon) — unit-tested
# ---------------------------------------------------------------------------
def _label_args(slug: str, role: str) -> list[str]:
    return ["--label", f"{config.LABEL_PREFIX}.slug={slug}",
            "--label", f"{config.LABEL_PREFIX}.role={role}"]


def local_model_id(model: str) -> str:
    """The id the local model is exposed under in the ``/model`` picker (``config.gateway_local_id`` —
    defined in config so runner + gateway share it without importing each other)."""
    return config.gateway_local_id(model)


def _model_present(model: str, installed: list[ollama.InstalledModel]) -> bool:
    """Is the pinned ``model`` among the host's installed Ollama tags? A bare name defaults to
    ``:latest`` (Ollama's scheme), so match that too."""
    want = model if ":" in model else f"{model}:latest"
    names = {m.name for m in installed}
    return model in names or want in names


def local_backend_warning(reachable: bool, tags_obj: object, model: str) -> str | None:
    """PURE: turn a host-Ollama probe outcome into an operator warning (or ``None`` when the local leg
    is ready). Fail-OPEN by design — a hybrid project's Claude leg works regardless; this only flags the
    two reasons selecting the LOCAL model in ``/model`` would silently hang/fail: host Ollama is
    unreachable from the gateway (down, or bound to loopback instead of ``0.0.0.0:11434``), or the pinned
    model isn't pulled. Unit-tested; the daemon wrapper ``check_local_backend`` feeds it a live probe."""
    local_id = local_model_id(model)
    host = hostplatform.host_loopback_host()
    if not reachable:
        return (f"local model unreachable: the gateway can't reach host Ollama at {host}:11434 — is "
                f"Ollama running and bound to 0.0.0.0:11434? (the claude.ai leg still works; selecting "
                f"'{local_id}' in /model would hang). See docs/MODELS.md.")
    installed = ollama.parse_tags(tags_obj)
    if not _model_present(model, installed):
        have = ", ".join(sorted(m.name for m in installed)) or "none"
        return (f"local model '{model}' is not pulled on host Ollama (have: {have}) — run "
                f"`claudemanctl model add {model}` (the claude.ai leg still works; selecting "
                f"'{local_id}' would fail until then).")
    return None


def network_create_argv(slug: str) -> list[str]:
    """`docker network create …` — the per-project network the agent + gateway share. A NORMAL bridge
    (NOT ``--internal``): an OPEN hybrid project keeps general egress via this net's gateway AND resolves
    the sidecar by name. (A locked hybrid project's air-gapped wiring is a follow-on; see ROADMAP 9c.)"""
    return ["docker", "network", "create",
            *_label_args(slug, "gateway-net"), config.gateway_net_name(slug)]


def gateway_config_yaml(model: str, *, ollama_host: str) -> str:
    """Render the LiteLLM ``config.yaml`` (string-built, no yaml dep — like squid.py). Secret-free: the
    master key is injected as ``LITELLM_MASTER_KEY`` env, never written here."""
    local_id = local_model_id(model)
    lines = [
        "# claude-man Phase 9 hybrid gateway (issue #14) — RENDERED, regenerated on every `up`.",
        "model_list:",
        "  # Claude leg: any claude-* id passes through to Anthropic; the forwarded client OAuth (below)",
        "  # is the credential, so the subscription is used. New Claude model ids work with no edit.",
        "  # The target MUST keep the `claude-` prefix (`anthropic/claude-*`, not `anthropic/*`): LiteLLM",
        "  # substitutes the captured wildcard suffix into the target's `*`, so `anthropic/*` would forward",
        "  # the invalid bare id `opus-4-8` (the issue #14 404); `anthropic/claude-*` forwards the full",
        "  # valid `claude-opus-4-8`. Covers Opus/Sonnet/Haiku(background)/Fable + future ids in one row.",
        "  - model_name: claude-*",
        "    litellm_params:",
        "      model: anthropic/claude-*",
        "  # Local leg: the pinned ollama model, exposed under a claude-local-* id for the picker.",
        f"  - model_name: {local_id}",
        "    litellm_params:",
        f"      model: ollama_chat/{model}",
        f"      api_base: http://{ollama_host}:11434",
        "general_settings:",
        "  # Forward the client's Authorization (claude.ai OAuth) + anthropic-beta to Anthropic verbatim",
        "  # so the SUBSCRIPTION is used; the agent auths to THIS proxy via x-litellm-api-key instead.",
        "  forward_client_headers_to_llm_api: true",
        "litellm_settings:",
        "  drop_params: true",
        "",
    ]
    return "\n".join(lines)


def gateway_run_argv(slug: str, *, conf_path: str, host_gateway_args: list[str]) -> list[str]:
    """`docker run -d …` for the LiteLLM sidecar on the project's gateway network.

    The rendered config is bind-mounted READ-ONLY; the master key is injected PASS-THROUGH
    (``-e LITELLM_MASTER_KEY`` — value supplied via the child env, never argv). ``host_gateway_args``
    (``--add-host=host.docker.internal:host-gateway`` on Linux) lets the sidecar reach the host Ollama.
    Trusted infra (pinned image + rendered config, no agent code) so it's not under the agent's hardened
    floor, but still ``no-new-privileges`` + a pids cap. Not published to the host — in-network only."""
    return [
        "docker", "run", "-d",
        "--name", config.gateway_container_name(slug),
        *_label_args(slug, "gateway"),
        "--network", config.gateway_net_name(slug),
        "--restart", "no",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "512",
        *host_gateway_args,
        "-v", f"{conf_path}:/app/config.yaml:ro",
        "-e", "LITELLM_MASTER_KEY",
        config.GATEWAY_IMAGE_REF,
        "--config", "/app/config.yaml", "--port", str(config.GATEWAY_PORT),
    ]


# ---------------------------------------------------------------------------
# Secret + daemon wrappers (need disk / docker; not unit-tested)
# ---------------------------------------------------------------------------
def ensure_master_key(slug: str) -> str:
    """Read (or create + persist ``0600``) the per-project LiteLLM master key. State-tier, never synced
    — the agent presents it via ``x-litellm-api-key``; the gateway requires it. Mirrors gh_token.py."""
    path = config.gateway_key_path(slug)
    if path.exists():
        key = path.read_text().strip()
        if key:
            return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = "sk-" + secrets.token_hex(24)
    path.write_text(key)
    path.chmod(0o600)
    return key


def render_conf(model: str, slug: str) -> str:
    """Render the project's LiteLLM config.yaml to the state tier; return its host path (a string)."""
    path = config.gateway_conf_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    host = hostplatform.host_loopback_host()
    path.write_text(gateway_config_yaml(model, ollama_host=host), encoding="utf-8")
    return str(path)


def ensure_network(slug: str) -> bool:
    """Create the per-project gateway network if absent (idempotent). Returns True on success."""
    name = config.gateway_net_name(slug)
    exists = runner._run(["docker", "network", "inspect", name])
    if exists.returncode == 0:
        return True
    return runner._run(network_create_argv(slug)).returncode == 0


def ensure_gateway(model: str, slug: str, *, on_progress=None):
    """(Re)create the gateway sidecar for ``model`` (fail-closed). Renders the config + key, recreates
    the sidecar from scratch (so a changed model/config always applies), and returns a (ok, detail)
    tuple. The agent must NOT start if this fails — a hybrid project with a dead gateway can't reach any
    model.

    Returns ``(bool, str)``.
    """
    if not ensure_network(slug):
        return False, f"could not create gateway network {config.gateway_net_name(slug)}"
    conf = render_conf(model, slug)
    key = ensure_master_key(slug)
    gw = config.gateway_container_name(slug)
    runner._run(["docker", "rm", "-f", gw], timeout=_TEARDOWN_TIMEOUT_S)  # drop any stale sidecar
    host_args = hostplatform.host_gateway_create_args()
    if on_progress:
        on_progress(f"starting hybrid gateway {gw} ({local_model_id(model)} + claude.ai passthrough) …")
    # Merge with os.environ (subprocess.run's env REPLACES it) so docker keeps PATH etc.; the master key
    # rides the child env so its value never reaches the host argv (`ps aux`) — the pass-through pattern.
    cp = runner._run(gateway_run_argv(slug, conf_path=conf, host_gateway_args=host_args),
                     env={**os.environ, "LITELLM_MASTER_KEY": key})
    if cp.returncode != 0:
        return False, f"gateway sidecar failed to start: {cp.stderr.strip() or cp.stdout.strip()}"
    if not _wait_healthy(gw):
        runner._run(["docker", "rm", "-f", gw], timeout=_TEARDOWN_TIMEOUT_S)  # no half-up gateway
        return False, f"gateway {gw} did not become healthy (check `docker logs {gw}`)"
    return True, f"hybrid gateway up: {local_model_id(model)} + claude.ai passthrough"


def _wait_healthy(gw: str, *, attempts: int = 60, delay: float = 1.0) -> bool:
    """Poll the sidecar's ``/health/liveliness`` from inside the container (the port isn't published)
    until it answers 200 or the budget runs out — so the agent only starts behind a live gateway. The
    LiteLLM image has a HEAVY import-time boot (~15-20s) and ships NO curl, so the probe uses the image's
    own ``python`` (a Python app — guaranteed present); a generous budget covers a cold start."""
    import time

    url = f"http://localhost:{config.GATEWAY_PORT}/health/liveliness"
    script = f"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('{url}',timeout=2).getcode()==200 else 1)"
    probe = ["docker", "exec", gw, "python", "-c", script]
    for _ in range(attempts):
        if runner._run(probe, timeout=6).returncode == 0:
            return True
        if runner._run(["docker", "inspect", "-f", "{{.State.Running}}", gw]).stdout.strip() != "true":
            return False  # the container exited — stop waiting
        time.sleep(delay)
    return False


_TEARDOWN_TIMEOUT_S = 20


def stop_gateway(slug: str) -> None:
    """Remove just the gateway SIDECAR (best-effort, time-bounded) — on stop. The network is left for the
    still-attached (stopped) agent container; ``up`` recreates the sidecar. No-op for a non-hybrid
    project (rm of a non-existent container). Mirrors ``egress.stop_proxy``."""
    runner._run(["docker", "rm", "-f", config.gateway_container_name(slug)], timeout=_TEARDOWN_TIMEOUT_S)


def check_local_backend(model: str, slug: str) -> str | None:
    """Fail-OPEN pre-flight (on hybrid ``up``): probe host Ollama FROM THE GATEWAY — the exact path the
    local leg uses (``host.docker.internal:11434/api/tags``) — and return an operator warning if it's
    unreachable or the pinned model isn't pulled, else ``None``. Never raises / never blocks start (the
    Claude leg works regardless); the LiteLLM image's own ``python`` runs the probe (it ships no curl).
    The IO half; the verdict is the pure ``local_backend_warning``."""
    gw = config.gateway_container_name(slug)
    host = hostplatform.host_loopback_host()
    url = f"http://{host}:11434/api/tags"
    script = f"import urllib.request,sys; sys.stdout.write(urllib.request.urlopen('{url}',timeout=4).read().decode())"
    cp = runner._run(["docker", "exec", gw, "python", "-c", script], timeout=10)
    if cp.returncode != 0 or not cp.stdout.strip():
        return local_backend_warning(False, None, model)
    try:
        obj = json.loads(cp.stdout)
    except (ValueError, TypeError):
        return local_backend_warning(False, None, model)
    return local_backend_warning(True, obj, model)


def teardown(slug: str) -> None:
    """Remove the gateway sidecar AND its network (best-effort, idempotent) — on delete/unpin, when the
    agent container is already gone so the network rm succeeds."""
    runner._run(["docker", "rm", "-f", config.gateway_container_name(slug)], timeout=_TEARDOWN_TIMEOUT_S)
    runner._run(["docker", "network", "rm", config.gateway_net_name(slug)], timeout=_TEARDOWN_TIMEOUT_S)
