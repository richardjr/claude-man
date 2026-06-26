"""Render and execute the hardened ``docker create`` for a project container.

``build_create_argv`` is a PURE function (no daemon, no filesystem) so the exact
hardened flag set can be unit-tested. The thin ``create``/``start``/``stop``/``rm``
wrappers shell out to ``docker`` via ``subprocess`` with explicit argv (never
``shell=True``).

Security notes (see CLAUDE.md invariants 1 & 2):
  * The OAuth token is injected via docker's env PASS-THROUGH form (``-e NAME``
    with no ``=value``) so the secret value is read from the ``docker`` process's
    environment and never appears in the host process argv (``ps aux``).
  * ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` are never rendered.
  * No ``.credentials.json`` is mounted; auth is the env token only.
"""

from __future__ import annotations

import os
import subprocess

from .. import config, hostplatform
from ..registry.schema import Project
from . import labels

OAUTH_TOKEN_ENV = config.OAUTH_TOKEN_ENV
GH_TOKEN_ENV = config.GH_TOKEN_ENV  # optional GitHub token — injected pass-through, sole-sourced from gh_token.py
# Phase 9 hybrid gateway: the agent presents the LiteLLM master key to the sidecar via this header
# (x-litellm-api-key) so its Authorization stays free for the forwarded claude.ai OAuth. Pass-through —
# the value (carrying the key) rides the child env in create(), never argv.
HYBRID_HEADER_ENV = "ANTHROPIC_CUSTOM_HEADERS"

# The fixed hardened flags (CLAUDE.md invariant 2 — the floor, not a suggestion).
_HARDENING = [
    "--read-only",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--user", f"{config.CONTAINER_UID}:{config.CONTAINER_GID}",
    "--pids-limit", "1024",
    # /tmp: Docker mounts a bare tmpfs sticky world-writable (1777), so the agent can write it.
    "--tmpfs", "/tmp:rw,exec,nosuid,size=512m",
    # .cache: a bare tmpfs defaults to root:root mode=755 (NOT agent-writable) — so it must be pinned
    # agent-owned, or node/corepack (`mkdir ~/.cache/node`) and claude's XDG_STATE_HOME (~/.cache/state)
    # fail with EACCES under --read-only --user 1000. The smoke gate probes this write.
    "--tmpfs", f"{config.CONTAINER_CACHE}:rw,exec,nosuid,size=256m,"
               f"uid={config.CONTAINER_UID},gid={config.CONTAINER_GID},mode=0700",
]

# Baked-but-also-explicit env (matches images/base/Dockerfile).
_BAKED_ENV = {
    "HOME": config.CONTAINER_HOME,
    "CLAUDE_CONFIG_DIR": config.CONTAINER_CLAUDE_CONFIG,
    "XDG_CACHE_HOME": config.CONTAINER_CACHE,
    "XDG_STATE_HOME": config.CONTAINER_STATE,
    # Redirect git/gh config onto the writable .cache tmpfs (rootfs is read-only). Identity itself is
    # injected via the dynamic GIT_CONFIG_COUNT env (build_create_argv's git_env); these make
    # `git config --global` and `gh` writes land somewhere writable instead of erroring on EROFS.
    "GIT_CONFIG_GLOBAL": config.CONTAINER_GITCONFIG,
    "GH_CONFIG_DIR": config.CONTAINER_GH_CONFIG,
    # Yarn caches/configs must land on a WRITABLE surface (the rootfs is read-only; the .cache tmpfs is
    # size-capped). Berry: its small global folder -> the .cache tmpfs (YARN_GLOBAL_FOLDER), no shared
    # global cache (YARN_ENABLE_GLOBAL_CACHE=false). The package CACHE (Berry AND Yarn Classic v1) ->
    # the disk-backed, persistent /workspace bind via YARN_CACHE_FOLDER — a 256m tmpfs cache OOM'd a
    # large install with ENOSPC. Yarn v1 ignores the two Berry vars but DOES honour YARN_CACHE_FOLDER;
    # its ~/.yarnrc write (EROFS on the read-only rootfs) is handled by an image symlink onto the .cache
    # tmpfs. Same redirect philosophy as GIT_CONFIG_GLOBAL/GH_CONFIG_DIR — no new writable surface (the
    # cache rides the existing /workspace bind), so the hardened floor is unchanged.
    "YARN_GLOBAL_FOLDER": config.CONTAINER_YARN_GLOBAL,
    "YARN_ENABLE_GLOBAL_CACHE": "false",
    "YARN_CACHE_FOLDER": config.CONTAINER_YARN_CACHE,
    # pip/uv write to HOME dotdirs on the read-only rootfs: caches under ~/.cache (the size-capped tmpfs
    # that ENOSPC'd a large yarn install) AND uv's downloaded interpreters + tool venvs under
    # ~/.local/share/uv (read-only -> `uv sync` dies EROFS creating ~/.local/share/uv/python). Redirect
    # every pip/uv dir onto the disk-backed /workspace bind, like YARN_CACHE_FOLDER — no new writable
    # surface, floor byte-identical. Created lazily only when a project uses pip/uv. NOTE: runner-injected
    # only — the YARN equivalents are ALSO baked in the base Dockerfile ENV; baking pip/uv there for
    # bare-`docker run` parity is a follow-up (every claude-man container + `image smoke` go through this
    # argv, so the fix lands without an image rebuild).
    "PIP_CACHE_DIR": config.CONTAINER_PIP_CACHE,
    "UV_CACHE_DIR": config.CONTAINER_UV_CACHE,
    "UV_PYTHON_INSTALL_DIR": config.CONTAINER_UV_PYTHON,  # uv-managed interpreter downloads (uv sync)
    "UV_PYTHON_BIN_DIR": config.CONTAINER_UV_BIN,         # `uv python install` exe links (else ~/.local/bin EROFS)
    "UV_TOOL_DIR": config.CONTAINER_UV_TOOLS,             # `uv tool` venvs
    "UV_TOOL_BIN_DIR": config.CONTAINER_UV_BIN,           # `uv tool` exe links
    "USE_BUILTIN_RIPGREP": "0",
    "DISABLE_AUTOUPDATER": "1",
}


def _render_env_mounts(project: Project, *, ssh_auth_sock: str | None) -> list[str]:
    """Render the project's env-mounts as docker args. PURELY ADDITIVE — never emits a ``_HARDENING``
    flag, so the hardened floor is byte-identical with or without env-mounts (invariant 2).

    ``file`` → a ``-v src:dst[:ro]`` bind (read-only by default). ``ssh`` → a ``0700`` writable
    ``~/.ssh`` tmpfs (the one ssh-conditional writable surface) plus, when ``ssh_auth_sock`` is set,
    a read-only bind of the host agent socket + ``SSH_AUTH_SOCK`` pointing at it. Private keys never
    enter the container — the host agent signs (the socket path is a path, not a secret). ``env`` → a
    ``-e NAME`` pass-through (value supplied via the child env in ``create`` from the 0600 state tier,
    never argv).
    """
    args: list[str] = []
    for m in project.env_mount:
        if m.error:  # a load-time-invalid (flagged) mount never reaches docker
            continue
        if m.kind == "file":
            suffix = ":ro" if m.ro else ""
            args += ["-v", f"{m.resolved_src()}:{m.dst}{suffix}"]
        elif m.kind == "env":
            # Pass-through name only; the value is supplied via the child env in create() from the
            # 0600 state-tier store, so it never reaches argv. Schema rejects forbidden names; this
            # is belt-and-braces so one can never be rendered even if a flagged mount slips through.
            if m.name and not config.is_forbidden_env_name(m.name):
                args += ["-e", m.name]
        elif m.kind == "ssh":
            args += ["--tmpfs",
                     f"{config.CONTAINER_SSH_DIR}:rw,nosuid,mode=0700,"
                     f"uid={config.CONTAINER_UID},gid={config.CONTAINER_GID},size=1m"]
            if ssh_auth_sock:
                args += ["-v", f"{ssh_auth_sock}:{config.CONTAINER_SSH_AGENT_SOCK}:ro"]
                args += ["-e", f"SSH_AUTH_SOCK={config.CONTAINER_SSH_AGENT_SOCK}"]
    return args


def _render_ports(project: Project) -> list[str]:
    """Render ``-p <bind>:<host>:<container>/<proto>`` for each valid published port. PURELY ADDITIVE —
    like ``_render_env_mounts``, never emits a ``_HARDENING`` flag, so the hardened floor is
    byte-identical with or without ports (invariant 2; a unit test pins this). Skips load-time-invalid
    (flagged) entries so a bad mapping never reaches docker. Port publishing is set up by the docker
    DAEMON on the host, so it works under ``--cap-drop ALL`` (the container's dropped caps don't gate
    the host-side ``-p`` mapping); the ≥1024 container-port rule (schema) keeps the in-container service
    bindable. Ingress only — orthogonal to the egress firewall (invariant 3)."""
    args: list[str] = []
    for p in project.ports:
        if p.error:  # a flagged (load-time-invalid) mapping never reaches docker
            continue
        args += ["-p", p.publish_arg()]
    return args


def _render_egress(project: Project) -> list[str]:
    """Render the strict-egress agent flags. PURELY ADDITIVE — like ``_render_ports`` it never emits a
    ``_HARDENING`` flag, so the hardened floor is byte-identical whether a project is open or strict
    (invariant 2; a unit test pins this). For an ``open`` project this is empty and the container keeps
    docker's default bridge (open egress) — unchanged from before Phase 4.

    For a ``strict`` project: attach the agent to its per-project ``--internal`` network (no direct
    route out) and point ``HTTP(S)_PROXY`` at the squid sidecar by its in-network DNS name. That is the
    whole agent side of the firewall — the network has no gateway, so the only path out is the proxy,
    which enforces the allowlist (CLAUDE.md invariant 3). The network + sidecar themselves are created
    by ``network/egress.py`` before the agent starts.
    """
    if project.egress != "strict":
        return []
    proxy = config.proxy_container_name(project.slug)
    proxy_url = f"http://{proxy}:{config.PROXY_PORT}"
    no_proxy = f"localhost,127.0.0.1,::1,{proxy}"
    args = ["--network", config.egress_net_name(project.slug)]
    # Lower- and upper-case forms — different tools read different casings (apt/curl use lower; many
    # Node/Python libs use upper). NO_PROXY excludes the proxy itself + loopback so the agent reaches
    # the proxy directly (and never tries to proxy a connection TO the proxy).
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        args += ["-e", f"{key}={proxy_url}"]
    for key in ("NO_PROXY", "no_proxy"):
        args += ["-e", f"{key}={no_proxy}"]
    return args


def _render_hybrid(project: Project) -> list[str]:
    """Render the hybrid-mode (Phase 9) agent flags. PURELY ADDITIVE — like ``_render_egress`` it never
    emits a ``_HARDENING`` flag, so the hardened floor is byte-identical whether a project is hybrid or
    not (invariant 2; a unit test pins this). Empty unless a local model is pinned.

    Points the agent at its per-project LiteLLM gateway sidecar (``ANTHROPIC_BASE_URL``) and adds the
    local model as an EXPLICIT ``/model`` picker row (``ANTHROPIC_CUSTOM_MODEL_OPTION``). The built-in
    Claude tiers still work — selecting one sends its ``claude-*`` id, which the gateway's wildcard route
    passes through to Anthropic on the SUBSCRIPTION. (Gateway model-discovery is deliberately NOT relied
    on: it silently fails on several auth shapes and a routing wildcard pollutes ``/v1/models`` — the
    explicit custom-model env path is reliable.) The proxy auth rides ``ANTHROPIC_CUSTOM_HEADERS``
    (``x-litellm-api-key`` — pass-through, value supplied via the child env in ``create`` so the key
    never reaches argv) so the agent's ``Authorization`` stays FREE to carry the forwarded claude.ai
    OAuth — the SUBSCRIPTION is preserved and the OAuth token is STILL injected (invariant 1: no
    ``ANTHROPIC_*`` key, no console key). The background/Haiku tier is left on Claude Haiku (passthrough —
    fast, on the subscription); a configurable local background tier is a follow-on.

    First cut is OPEN-egress only: a hybrid project joins its gateway bridge network (which keeps general
    egress + resolves the sidecar). Strict + hybrid (the two-sidecar air-gapped wiring) is deferred —
    ``lifecycle.up`` refuses it — so this renders nothing for a strict project, keeping the create argv
    valid (no double ``--network``).
    """
    if not project.model or project.egress == "strict":
        return []
    gw = config.gateway_container_name(project.slug)
    base_url = f"http://{gw}:{config.GATEWAY_PORT}"
    local_id = config.gateway_local_id(project.model)
    return [
        "--network", config.gateway_net_name(project.slug),
        "-e", f"ANTHROPIC_BASE_URL={base_url}",
        # The local model as an explicit picker row (reliable; built-in Claude tiers route via the
        # gateway's claude-* passthrough). The id MUST equal the gateway model_list name.
        "-e", f"ANTHROPIC_CUSTOM_MODEL_OPTION={local_id}",
        "-e", f"ANTHROPIC_CUSTOM_MODEL_OPTION_NAME=Local: {project.model}",
        "-e", "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION=Local model via the claude-man hybrid gateway",
        "-e", HYBRID_HEADER_ENV,   # value (x-litellm-api-key: <master key>) supplied via the child env
    ]


def _render_shell_history(host_dir: str | None) -> list[str]:
    """Render the opt-in persistent shell-history bind + its ``CLAUDEMAN_HISTFILE`` env. PURELY
    ADDITIVE — like ``_render_ports``/``_render_egress`` it never emits a ``_HARDENING`` flag, so with
    persistence OFF (``host_dir is None``, the default) this is empty and the hardened floor is
    byte-identical (invariant 2; unit-pinned). With it ON: a read-WRITE bind of the per-project state
    dir at ``CONTAINER_SHELL_HISTORY_DIR`` + ``CLAUDEMAN_HISTFILE`` so the baked rc points ``$HISTFILE``
    at the bind (history survives recreate). The ONE opt-in writable surface beyond the floor — only
    present when the operator runs ``config shell-history on``."""
    if not host_dir:
        return []
    histfile = f"{config.CONTAINER_SHELL_HISTORY_DIR}/bash_history"
    return ["-v", f"{host_dir}:{config.CONTAINER_SHELL_HISTORY_DIR}",
            "-e", f"CLAUDEMAN_HISTFILE={histfile}"]


def build_create_argv(
    project: Project,
    *,
    profile_name: str,
    version: str = config.DEFAULT_CLAUDE_VERSION,
    created_iso: str,
    claude_config_path: str | None = None,
    workspace_path: str | None = None,
    inject_token: bool = True,
    inject_gh_token: bool = False,
    file_env: dict[str, str] | None = None,
    ssh_auth_sock: str | None = None,
    git_env: dict[str, str] | None = None,
    shell_history_host_dir: str | None = None,
) -> list[str]:
    """Render the full ``docker create`` argv for a project's hardened container.

    Never includes the token *value* or any scrubbed env key. ``claude_config_path``
    / ``workspace_path`` default to the project's host state dirs. ``file_env`` is the
    already-parsed-and-scrubbed contents of ``project.env_file`` (resolved host-side by
    ``create``); its keys are injected as docker env PASS-THROUGH (``-e KEY`` with no
    value) so secrets never appear in the host argv (``ps aux``).
    """
    cfg_path = claude_config_path or str(config.claude_config_dir(project.slug))
    ws_path = workspace_path or str(config.workspace_dir(project.slug))

    argv: list[str] = ["docker", "create", "--name", project.container]
    argv += labels.to_args(
        labels.build(project, profile=profile_name, version=version, created_iso=created_iso)
    )
    argv += _HARDENING

    # Baked env (explicit for clarity even though the image bakes it).
    for key, value in _BAKED_ENV.items():
        argv += ["-e", f"{key}={value}"]

    # OAuth token: pass-through form, value stays out of argv.
    if inject_token:
        argv += ["-e", OAUTH_TOKEN_ENV]
    # Optional GitHub token (opt-in): same pass-through form so the value never reaches argv.
    if inject_gh_token:
        argv += ["-e", GH_TOKEN_ENV]

    # Project env vars (declared config), with auth/token keys removed defensively. GH_TOKEN is
    # ALWAYS skipped here (like the OAuth token): its only legitimate source is the configured
    # state-tier token injected pass-through above — never a value in argv / the secret-free config.
    for key, value in project.env.items():
        if key in config.SCRUBBED_ENV_KEYS or key == OAUTH_TOKEN_ENV or key == GH_TOKEN_ENV:
            continue
        argv += ["-e", f"{key}={value}"]
    # env_file vars: injected as pass-through (-e KEY, value supplied via the subprocess
    # env in create()) so secrets stay out of argv. The scrub is enforced here too, so a
    # forbidden key can never be rendered even if a caller hands us an un-scrubbed dict.
    # NOTE: docker is NOT given --env-file directly — that path bypassed the ANTHROPIC_*
    # scrub and could silently outrank the OAuth token (review SEC-2).
    for key in file_env or {}:
        # GH_TOKEN is sole-sourced from the configured token (above), never an env_file — so it's
        # always skipped here too (read_env_file already scrubs it; this is belt-and-braces).
        if (key in config.SCRUBBED_ENV_KEYS or key == OAUTH_TOKEN_ENV or key == GH_TOKEN_ENV):
            continue
        argv += ["-e", key]
    # Git author identity (GIT_CONFIG_COUNT/KEY_n/VALUE_n) — non-secret name/email, so rendered as a
    # direct value. Lets `git commit` work under the read-only rootfs without a writable ~/.gitconfig.
    for key, value in (git_env or {}).items():
        argv += ["-e", f"{key}={value}"]

    # Writable persistent binds + read-only rootfs everywhere else.
    argv += ["-v", f"{cfg_path}:{config.CONTAINER_CLAUDE_CONFIG}"]
    argv += ["-v", f"{ws_path}:{config.CONTAINER_WORKSPACE}"]
    # Env-mounts (ssh + files) + published ports + strict-egress wiring — additive only; the hardened
    # floor above is untouched (each renderer is unit-pinned to add only its own value tokens).
    argv += _render_env_mounts(project, ssh_auth_sock=ssh_auth_sock)
    argv += _render_ports(project)
    argv += _render_egress(project)
    argv += _render_hybrid(project)
    argv += _render_shell_history(shell_history_host_dir)
    argv += ["-w", config.CONTAINER_WORKSPACE]

    argv += [project.image, "sleep", "infinity"]
    return argv


# ---------------------------------------------------------------------------
# Thin execution wrappers (need a docker daemon; not unit-tested)
# ---------------------------------------------------------------------------
def _run(argv: list[str], *, env: dict[str, str] | None = None,
         timeout: float | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, env=env, capture_output=True, text=True, check=False,
                              timeout=timeout)
    except FileNotFoundError:
        # The binary (docker/git) isn't on PATH. Surface it as a non-zero result — like a 127
        # "command not found" — so every caller (CLI, the synchronous UI actions, and the TUI
        # create worker) logs a red line instead of an uncaught FileNotFoundError tearing down
        # the process. 127 is the conventional shell exit code for a missing command.
        return subprocess.CompletedProcess(
            argv, 127, stdout="", stderr=f"{argv[0]!r} not found on PATH"
        )
    except subprocess.TimeoutExpired:
        # A wedged docker daemon must never hang a caller forever (e.g. the quit stop-all worker).
        # 124 is the conventional `timeout(1)` exit code; callers map non-zero to a red Result.
        return subprocess.CompletedProcess(
            argv, 124, stdout="", stderr=f"timed out after {timeout}s: {' '.join(argv)}"
        )


def read_env_file(path: str) -> dict[str, str]:
    """Parse a ``KEY=VAL`` env file host-side, dropping scrubbed/auth keys.

    Blank lines and ``#`` comments are ignored; a leading ``export`` is stripped; surrounding
    single/double quotes on the value are removed. ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN``
    / ``CLAUDE_CODE_OAUTH_TOKEN`` are never returned (review SEC-2), so they can neither reach the
    container nor outrank the injected OAuth token.
    """
    out: dict[str, str] = {}
    with open(os.path.expanduser(path), encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if (not key or key in config.SCRUBBED_ENV_KEYS
                    or key == OAUTH_TOKEN_ENV or key == GH_TOKEN_ENV):
                continue  # GH_TOKEN never comes from an env_file — only the configured state-tier token
            out[key] = value
    return out


def exists(slug: str) -> bool:
    cp = _run(["docker", "container", "inspect", config.container_name(slug)])
    return cp.returncode == 0


def create(
    project: Project,
    *,
    profile_name: str,
    token: str | None = None,
    gh_token: str | None = None,
    env_secrets: dict[str, str] | None = None,
    version: str = config.DEFAULT_CLAUDE_VERSION,
    created_iso: str,
    git_env: dict[str, str] | None = None,
    shell_history_host_dir: str | None = None,
    hybrid_header: str | None = None,
) -> subprocess.CompletedProcess:
    """Create the container, passing the token(s) + env_file values through the subprocess env.

    Secrets (the OAuth token, the optional ``gh_token``, and any ``env_file`` values) are supplied via
    the child process environment and rendered into argv only as pass-through names, so they never
    appear in ``ps aux``. ``env_file`` is parsed + scrubbed host-side (review SEC-2). ``token`` may be
    ``None`` (e.g. before any profile token is minted) — the container still builds and a shell
    works, but in-container ``claude`` won't authenticate. ``gh_token`` is ``None`` unless the operator
    configured one (``config gh-token``); only then is ``GH_TOKEN`` injected.
    """
    file_env = read_env_file(project.env_file) if project.env_file else {}
    # Resolve the host ssh-agent socket only if an ssh env-mount is configured (the socket PATH is not
    # a secret; the private keys stay in the host agent — agent-forward). On macOS this resolves to
    # Docker Desktop's forwarded default-agent socket — an arbitrary host socket can't cross the VM.
    ssh_sock = (
        hostplatform.docker_ssh_auth_sock(os.environ.get("SSH_AUTH_SOCK"))
        if any(m.kind == "ssh" for m in project.env_mount) else None
    )
    argv = build_create_argv(
        project, profile_name=profile_name, version=version, created_iso=created_iso,
        file_env=file_env, inject_token=bool(token), inject_gh_token=bool(gh_token),
        ssh_auth_sock=ssh_sock, git_env=git_env, shell_history_host_dir=shell_history_host_dir,
    )
    env = dict(os.environ)
    for key in config.SCRUBBED_ENV_KEYS:
        env.pop(key, None)
    env.pop(GH_TOKEN_ENV, None)  # never inherit a host GH_TOKEN — only the configured token is injected
    if token:
        env[OAUTH_TOKEN_ENV] = token
    env.update(file_env)
    if gh_token:
        env[GH_TOKEN_ENV] = gh_token  # the sole source; argv has the pass-through `-e GH_TOKEN`
    # Hybrid gateway (Phase 9): the x-litellm-api-key header value carries the master key — supplied via
    # the child env so it never reaches argv (argv has the pass-through `-e ANTHROPIC_CUSTOM_HEADERS`).
    if hybrid_header:
        env[HYBRID_HEADER_ENV] = hybrid_header
    # kind="env" env-mount values (pass-through; argv has the `-e NAME` from _render_env_mounts).
    # Forbidden names are filtered so an operator env var can never shadow the scrubbed/auth keys.
    for key, value in (env_secrets or {}).items():
        if not config.is_forbidden_env_name(key):
            env[key] = value
    return _run(argv, env=env)


def is_running(slug: str) -> bool:
    cp = _run(["docker", "inspect", "-f", "{{.State.Running}}", config.container_name(slug)])
    return cp.returncode == 0 and cp.stdout.strip() == "true"


def exec_write_file(slug: str, container_path: str, data: bytes, *, mode: str = "600"):
    """Write ``data`` into ``container_path`` inside the running container via ``docker exec -i`` stdin.

    Used to seed the ssh ~/.ssh tmpfs (config/known_hosts): ``docker cp`` into a ``--read-only``
    container fails ("container rootfs is marked read-only" — verified), but an exec writing to a
    writable tmpfs as the container uid works. Bytes mode (no ``text=``) so binary content is exact.
    """
    try:
        return subprocess.run(
            ["docker", "exec", "-i", config.container_name(slug), "sh", "-c",
             f"cat > {container_path} && chmod {mode} {container_path}"],
            input=data, capture_output=True, check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess([], 127, stdout=b"", stderr=b"docker not found")


def build_logs_argv(slug: str, *, tail: int = 200, follow: bool = True) -> list[str]:
    """Pure argv for streaming a container's logs (unit-testable without a daemon).

    ``--timestamps`` keeps interleaved stdout/stderr ordered; ``--tail`` bounds the initial backfill
    so a long-lived container doesn't flood the viewer. ``follow`` adds ``-f`` to stream new lines
    (the TUI Logs screen reaps the process on dismiss, so no follower leaks). On a STOPPED container
    ``docker logs`` prints what's there and exits even with ``-f``; on a missing one it errors to
    stderr (the viewer merges it in)."""
    argv = ["docker", "logs", "--tail", str(tail), "--timestamps"]
    if follow:
        argv.append("-f")
    argv.append(config.container_name(slug))
    return argv


def start(slug: str) -> subprocess.CompletedProcess:
    return _run(["docker", "start", config.container_name(slug)])


def stop(slug: str) -> subprocess.CompletedProcess:
    # Short grace (the baked `sleep infinity` PID 1 ignores SIGTERM, so the grace is always spent
    # then SIGKILL); a subprocess timeout above the grace guards against a wedged daemon hanging us.
    return _run(["docker", "stop", "-t", str(config.DOCKER_STOP_GRACE_S), config.container_name(slug)],
                timeout=config.DOCKER_STOP_GRACE_S + 15)


def remove(slug: str) -> subprocess.CompletedProcess:
    return _run(["docker", "rm", "-f", config.container_name(slug)])
