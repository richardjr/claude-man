"""Project lifecycle orchestration shared by the CLI and the TUI (Phase 1).

Chains the already-tested building blocks into the create / up / stop flows: resolve the effective
profile, seed the ``claude-config`` dir, host-side clone, then the hardened ``docker create`` and
``start``/``stop``. Pure-stdlib (no textual), so the CLI and tests import it without the TUI.

Persistence is the default: ``stop`` never removes a container, and ``up`` is create-if-needed
(idempotent via ``runner.exists`` — review WIRE-7), so restarts leave the binds untouched.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import fcntl
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass

from . import assets, config, env_secrets, gh_token, gitconfig, hostplatform, ssh_agent, updates
from .checkout import gitstate, repos
from .docker import images, runner, status
from .profiles import seed as seed_mod
from .registry import profiles as profiles_registry
from .registry import projects as projects_registry
from .registry import settings as settings_registry
from .registry.schema import EnvMount, PortMapping, Profile, Project, Repo, ValidationError

# A progress sink threaded through create/up/recreate so a long, one-time image build surfaces
# live in the caller (the TUI log pane / the CLI). ``None`` means "no progress wanted".
ProgressFn = Callable[[str], None]


@dataclass(frozen=True)
class Result:
    ok: bool
    detail: str


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def effective_profile(project: Project) -> Profile | None:
    """The project's explicit profile, else the default, else None (no profile defined yet)."""
    if project.profile:
        try:
            return profiles_registry.load(project.profile)
        except FileNotFoundError:
            return None
    return profiles_registry.default_profile()


def _ensure_workspace_owned(slug: str) -> str | None:
    """Ensure ``workspace_dir(slug)`` exists and is operator-owned BEFORE ``docker create``.

    Docker auto-creates a missing bind-mount source as ``root:root``, which then blocks every host-side
    write into ``workspace/`` (clones fail with EACCES — the "could not create work tree dir … Permission
    denied" the operator hit). Creating it operator-owned first avoids that. An *empty* dir Docker
    already root-created is reclaimed without sudo (the parent ``projects/<slug>/`` is operator-owned, so
    ``rmdir`` + ``mkdir`` works); a *non-empty* foreign-owned dir is surfaced for manual repair (we can't
    ``chown`` without root). Returns an error string on the unrecoverable case, else ``None``.
    """
    ws = config.workspace_dir(slug)
    if not ws.exists():
        ws.mkdir(parents=True, exist_ok=True)
        return None
    try:
        foreign = ws.stat().st_uid != os.getuid()
    except OSError:
        return None
    if not foreign:
        return None
    hint = (f"workspace {ws} is owned by another user (Docker likely auto-created it as root) — "
            f"fix with: sudo chown -R {os.getuid()}:{os.getgid()} {ws}")
    try:
        if any(ws.iterdir()):
            return hint            # non-empty: never destroy operator data
        ws.rmdir()                 # empty + foreign: reclaim it (the parent dir is operator-owned)
        ws.mkdir(parents=True)
    except OSError:
        # Can't traverse/reclaim (e.g. a 0700 foreign dir, or a TOCTOU write between the check and
        # rmdir) — surface the same remediation rather than letting it crash the CLI / TUI worker.
        return hint
    return None


def _ensure_workspace_mountpoints(project: Project) -> None:
    """Pre-create (operator-owned) the host mountpoint for any ``file`` env-mount whose dst is under
    ``/workspace``, so Docker binds over an existing operator-owned path instead of root-creating the
    nested mountpoint (which would otherwise leave a root-owned file in the workspace bind). A file src
    gets a touched file mountpoint; a dir src gets a mkdir."""
    ws = config.workspace_dir(project.slug)
    prefix = config.CONTAINER_WORKSPACE + "/"
    for m in project.env_mount:
        if m.error or m.kind != "file" or not m.dst.startswith(prefix):
            continue
        dest = ws / m.dst[len(prefix):]
        if not repos.is_within(dest, ws):  # defence-in-depth (dst is already containment-validated)
            continue
        try:
            if os.path.isdir(m.resolved_src()):
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    dest.touch()
        except OSError:
            continue  # best-effort; docker will still bind (root-create) if we couldn't pre-make it


def _validate_mount_sources(project: Project) -> tuple[list[str], list[str]]:
    """Pre-flight env-mount sources. Returns (errors, warnings).

    A ``file`` mount whose host source is missing/unreadable is an ERROR (Docker would auto-create the
    bind source as root at start, shadowing the file). An ``ssh`` mount with no host agent
    (``SSH_AUTH_SOCK`` unset) is a WARNING (the container builds; ssh just won't authenticate yet).
    """
    errors: list[str] = []
    warnings: list[str] = []
    for m in project.env_mount:
        if m.error:  # a flagged (load-time-invalid) mount — surface it, don't try to use it
            errors.append(f"env mount {m.target!r} is invalid: {m.error} "
                          f"(`project env rm {project.slug} {m.target}` and re-add)")
            continue
        if m.kind == "file":
            src = m.resolved_src()
            if not os.path.exists(src):
                errors.append(f"env mount source missing: {src} → {m.dst} "
                              f"(fix the path or `project env rm {project.slug} {m.dst}`)")
            elif not os.access(src, os.R_OK):
                errors.append(f"env mount source not readable: {src}")
        elif m.kind == "ssh" and not os.environ.get("SSH_AUTH_SOCK") \
                and not hostplatform.is_macos():
            # On macOS the container gets Docker Desktop's forwarded default-agent socket
            # regardless of SSH_AUTH_SOCK here, so the unset-var warning doesn't apply.
            warnings.append("ssh env-mount: no ssh-agent (SSH_AUTH_SOCK unset) — "
                            "`ssh-add` your key then `recreate`")
    return errors, warnings


def _seed_ssh(slug: str) -> None:
    """Seed the host ~/.ssh/{config,known_hosts} into the container's ~/.ssh tmpfs (exec-stdin).

    Only NON-secret material (host aliases + known host keys) is copied so in-container ssh resolves
    IdentityFile aliases and skips host-key prompts; private keys never leave the host agent.
    Best-effort: a stale container with no ~/.ssh tmpfs (ssh mount added without a recreate) just fails
    the write silently — ``project resync`` reports that case.
    """
    ssh_dir = os.path.expanduser("~/.ssh")
    for name in ("config", "known_hosts"):
        path = os.path.join(ssh_dir, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        runner.exec_write_file(slug, f"{config.CONTAINER_SSH_DIR}/{name}", data)


def _has_ssh_mount(project: Project) -> bool:
    return any(m.kind == "ssh" for m in project.env_mount)


def ensure_created(project: Project, *, on_progress: ProgressFn | None = None) -> Result:
    """Create the hardened container if it doesn't exist (idempotent). Seeds config + clones repos.

    Pre-flight: the project's image (``claude-man:<overlay>``) must exist locally or ``docker create``
    fails opaquely. ``ensure_chain`` auto-builds it (base first, then the overlay) when missing, so the
    operator never has to run ``image build`` by hand — progress streams to ``on_progress``.
    """
    if runner.exists(project.slug):
        return Result(True, f"{project.container} already exists")

    img = images.ensure_chain(project.overlay, on_line=on_progress)
    if not img.ok:
        return Result(False, img.detail)

    profile = effective_profile(project)
    profile_name = profile.name if profile else "none"
    token = profiles_registry.load_token(profile.name) if profile else None

    seed_mod.seed_project_config(project, profile)
    # Create workspace/ operator-owned BEFORE docker binds it, or Docker auto-creates it root:root and
    # host-side clones into it fail with EACCES (self-heals an empty root-owned dir from a prior create).
    ws_err = _ensure_workspace_owned(project.slug)
    if ws_err:
        return Result(False, ws_err)
    # Refuse to create with a missing file env-mount source: Docker auto-creates a missing bind source
    # as root at start, shadowing the intended file (the operator must fix the path or `env rm`).
    mount_errors, mount_warnings = _validate_mount_sources(project)
    if mount_errors:
        return Result(False, "; ".join(mount_errors))
    _ensure_workspace_mountpoints(project)  # operator-own the nested /workspace mountpoints before bind
    clone_failures = [r for r in repos.clone_all(project) if not r.ok] if project.repos else []

    # For an ssh-mount project, bootstrap the host agent (load configured keys + set SSH_AUTH_SOCK in
    # os.environ) BEFORE create — so the forwarded socket is live + key-loaded whether we were launched
    # from the TUI or a bare CLI ("load config needs into the environment", not the operator's job).
    if _has_ssh_mount(project):
        key_res = ensure_ssh_keys()
        if on_progress and "no ssh keys configured" not in key_res.detail:
            on_progress(key_res.detail.splitlines()[0])

    # kind="env" env-mount VALUES (0600 state tier) for the current, valid, present names — injected
    # pass-through by runner.create (the registry holds only the names; values never touch config.toml).
    stored = env_secrets.load(project.slug)
    env_vars = {m.name: stored[m.name] for m in project.env_mount
                if m.kind == "env" and not m.error and m.name in stored}
    # Stamp the container's version label with the image's ACTUAL baked claude (the source of truth),
    # not the build-time DEFAULT — so the Version column stays truthful after an on-start image rebuild.
    version = images.image_claude_version(project.overlay) or config.DEFAULT_CLAUDE_VERSION
    # Git author identity (config.toml [git] override, else inherited from the host git config),
    # injected as GIT_CONFIG_* env so in-container `git commit` works under the read-only rootfs.
    cp = runner.create(project, profile_name=profile_name, token=token,
                       gh_token=gh_token.load(), env_secrets=env_vars, created_iso=_now_iso(),
                       git_env=gitconfig.container_env(), version=version)
    if cp.returncode != 0:
        return Result(False, f"docker create failed: {cp.stderr.strip() or cp.stdout.strip()}")

    notes = []
    if not token:
        notes.append("no token — in-container `claude` won't authenticate "
                     "(mint one with `claude setup-token` → "
                     f"{config.profile_token_path(profile_name)})" if profile
                     else "no profile/token — define one with `claudemanctl profile add`")
    if clone_failures:
        notes.append(f"{len(clone_failures)} repo(s) failed to clone")
    notes.extend(mount_warnings)
    missing_env = [m.name for m in project.env_mount
                   if m.kind == "env" and not m.error and m.name not in stored]
    if missing_env:
        notes.append(f"{len(missing_env)} env var(s) have no stored value (re-add with "
                     f"`project env add {project.slug} env <NAME>`): {', '.join(missing_env)}")
    suffix = ("  [" + "; ".join(notes) + "]") if notes else ""
    return Result(True, f"created {project.container}{suffix}")


# ---------------------------------------------------------------------------
# On-start claude-version update — "run update before start" (CLAUDE.md / ROADMAP). claude update
# can't run inside the read-only container (its ~/.local install can't be written), so claude-man
# checks the tracked channel's latest version host-side and, on the operator's confirmation, REBUILDS
# the project's image to it and recreates the container on it. Purely additive: the hardened floor
# (invariant 2) is unchanged — the rebuilt image is the same Dockerfile with a newer CLAUDE_VERSION.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UpdateCheck:
    """The on-start claude-version decision for a project (read-only; computed before ``up``).

    ``current`` is the version baked into the project's image (``""`` if it isn't built yet); ``target``
    is the resolved channel/pin version (``""`` if unresolved — offline/disabled). ``prompt`` is True
    only when an EXISTING image is behind ``target`` (so the operator is asked before the
    potentially-minutes-long rebuild). ``build_to`` is a version to (re)build WITHOUT asking — set for a
    fresh project (no image yet) so its first build tracks the channel directly; ``""`` otherwise.
    ``note`` is a short human reason for the log."""

    current: str = ""
    target: str = ""
    prompt: bool = False
    build_to: str = ""
    note: str = ""


def check_update(project: Project) -> UpdateCheck:
    """Decide whether a newer claude than the project's image is available and whether ``up`` should
    prompt-then-rebuild, silently build, or do nothing. A host-side GET; NEVER raises and never blocks
    meaningfully (a GET failure, docker-absence, OR an unreadable/malformed ``config.toml`` folds into
    a no-action result — fail open: start on the existing image). The actual rebuild is gated on the
    operator's confirmation in the caller (``prompt``), per the chosen 'prompt before rebuild'
    behaviour."""
    try:
        settings = settings_registry.load()
    except (OSError, ValueError):
        # A malformed/unreadable config.toml must not turn a routine start into a crash — the update
        # check fails OPEN (other commands still surface the broken config to the operator). Both
        # tomllib.TOMLDecodeError and our ValidationError are ValueError subclasses; OSError covers a
        # read fault. Honours the 'NEVER raises' contract for EVERY caller — the CLI's `project up`
        # path doesn't wrap this call (only the TUI worker did).
        return UpdateCheck(note="config unreadable — skipping update check")
    if not settings.image_update_check:
        return UpdateCheck(note="update check disabled")
    current = images.image_claude_version(project.overlay) or ""
    # Resolve the target: a per-project pin wins, then the global pin, else the tracked channel.
    pin = (project.claude_version or settings.claude_version_pin or "").strip()
    if pin:
        if not current:
            return UpdateCheck(target=pin, build_to=pin, note=f"pinned {pin} (first build)")
        if current == pin:
            return UpdateCheck(current=current, target=pin, note=f"pinned {pin} (image current)")
        # Image drifted off the pin — offer to rebuild to it (an explicit pin wins, up or down).
        return UpdateCheck(current=current, target=pin, prompt=True, note=f"pinned {pin}")
    rc = updates.resolve_channel(settings.claude_channel)
    if not rc.version:
        return UpdateCheck(current=current, note=rc.note or "offline")  # fail open
    target = rc.version
    if not current:
        return UpdateCheck(target=target, build_to=target,
                           note=f"{settings.claude_channel} {target} (first build)")
    if updates.is_newer(target, current):
        return UpdateCheck(current=current, target=target, prompt=True,
                           note=f"{settings.claude_channel} {target} > image {current}")
    return UpdateCheck(current=current, target=target, note=f"image {current} up to date")


def _maybe_rebuild_for_update(project: Project, version: str, *, on_progress: ProgressFn | None) -> None:
    """Force-rebuild the project's image to ``version`` and remove the existing (stopped) container so
    ``ensure_created`` recreates it on the fresh image. Best-effort + fail-open: a RUNNING container or
    a FAILED rebuild leaves the existing image/container in place (start then proceeds on it). Never
    raises — the update must not turn a routine start into a hard failure."""
    if runner.is_running(project.slug):
        if on_progress:
            on_progress(f"[update] {project.slug} is running — not rebuilding "
                        f"(stop it, then start to update to claude {version})")
        return
    rb = images.rebuild_chain(project.overlay, claude_version=version, on_line=on_progress)
    if not rb.ok:
        if on_progress:
            on_progress(f"[update] rebuild failed: {rb.detail}; starting on the existing image")
        return
    if runner.exists(project.slug):
        runner.remove(project.slug)  # recreate on the freshly-built image (ensure_created rebuilds it)
        if on_progress:
            on_progress(f"[update] recreating {project.container} on the rebuilt image")


def up(project: Project, *, on_progress: ProgressFn | None = None, rebuild_to: str = "") -> Result:
    """Create-if-needed, then start (syncing per-project assets IN before start).

    ``rebuild_to`` (set by the caller after ``check_update`` + the operator's confirmation) force-
    rebuilds the project's image to that claude version and recreates the container on it BEFORE the
    normal create/start — the 'run update before start' path. Empty ``rebuild_to`` is the unchanged
    flow. The rebuild is skipped for a running container and fails open (see
    ``_maybe_rebuild_for_update``)."""
    if rebuild_to:
        _maybe_rebuild_for_update(project, rebuild_to, on_progress=on_progress)
    created = ensure_created(project, on_progress=on_progress)
    if not created.ok:
        return created
    # Sync assets IN after ensure_created (which seeds profile defaults) so per-project assets layer
    # on top, and BEFORE start so the container sees them. Best-effort — a sync fault never blocks start.
    sync_note = _sync_in(project, on_progress=on_progress)
    cp = runner.start(project.slug)
    if cp.returncode != 0:
        return Result(False, f"docker start failed: {cp.stderr.strip() or cp.stdout.strip()}")
    if _has_ssh_mount(project):
        _seed_ssh(project.slug)  # populate the ~/.ssh tmpfs config/known_hosts (post-start, exec-stdin)
    prefix = created.detail + "; " if "created" in created.detail else ""
    return Result(True, f"{prefix}started {project.container}{sync_note}")


def stop(slug: str, *, on_progress: ProgressFn | None = None) -> Result:
    """Stop the container (never removes it — persistence is the default), then sync assets OUT."""
    cp = runner.stop(slug)
    if cp.returncode != 0:
        return Result(False, f"docker stop failed: {cp.stderr.strip() or cp.stdout.strip()}")
    sync_note = _sync_out(slug, on_progress=on_progress)  # binds -> asset source; best-effort
    return Result(True, f"stopped {config.container_name(slug)}{sync_note}")


def stop_all(
    *, on_progress: ProgressFn | None = None,
    on_each: Callable[[str, Result], None] | None = None,
) -> list[tuple[str, Result]]:
    """Stop + sync-out every RUNNING claude-man container (the end-of-day batch). Best-effort: a
    single failure never aborts the batch. Running set is read fresh from ``docker ps`` (the registry
    is the project source of truth, but liveness is never cached — invariant 4); this is "stop
    everything claude-man", so orphan containers (no registry entry) are stopped too — their inner
    ``_sync_out`` no-ops. ``on_each(slug, res)`` fires after each stop (for streaming progress).
    Returns ``[(slug, Result), …]`` in stop order."""
    running = sorted(
        slug for slug, cs in status.query_containers().items() if cs.state == "running"
    )
    results: list[tuple[str, Result]] = []
    for slug in running:
        try:
            res = stop(slug, on_progress=on_progress)
        except Exception as exc:  # noqa: BLE001 - one bad container must not abort the batch
            res = Result(False, f"stop failed for {slug!r}: {exc!r}")
        results.append((slug, res))
        if on_each:
            on_each(slug, res)
    return results


def _sync_in(project: Project, *, on_progress: ProgressFn | None) -> str:
    """Asset sync-in → a ``'; <detail>'`` suffix for the Result (or ''). Never raises — a sync fault
    must not prevent a container start."""
    if not project.sync.enabled:
        return ""
    try:
        rep = assets.sync_in(project, on_progress=on_progress)
    except Exception as exc:  # noqa: BLE001 - never block start on a sync fault
        if on_progress:
            on_progress(f"asset sync-in failed: {exc!r}")
        return f"; sync-in error: {exc}"
    return ("; " + rep.detail) if rep.detail else ""


def _sync_out(slug: str, *, on_progress: ProgressFn | None) -> str:
    """Asset sync-out for a stopped project → a ``'; <detail>'`` suffix (or ''). Skips orphan
    containers (no registry) and disabled projects; never raises (a fault mustn't fail the stop)."""
    if not projects_registry.exists(slug):
        return ""  # orphan container — no registry/sync config
    try:
        project = projects_registry.load(slug)
        if not project.sync.enabled:
            return ""
        rep = assets.sync_out(project, on_progress=on_progress)
    except Exception as exc:  # noqa: BLE001 - a sync-out fault must not make a stop look failed
        if on_progress:
            on_progress(f"asset sync-out failed: {exc!r}")
        return f"; sync-out error: {exc}"
    return ("; " + rep.detail) if rep.detail else ""


def sync(slug: str, *, direction: str, on_progress: ProgressFn | None = None) -> Result:
    """Manually sync a project's assets in/out (the CLI ``project sync`` verb). Explicit operator
    action — runs even when ``project.sync.enabled`` is False."""
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    project = projects_registry.load(slug)
    rep = (assets.sync_in if direction == "in" else assets.sync_out)(project, on_progress=on_progress)
    return Result(rep.ok, rep.detail or f"{slug}: nothing to sync-{direction}")


def account_mismatch(project: Project, profile: Profile | None) -> str | None:
    """Return the existing seeded email if it conflicts with ``profile``'s account, else None.

    The switch-time guard against work/home cross-contamination (review 2.2): a project's config
    dir already belongs to whatever account first seeded it; pointing it at a profile for a
    *different* account would mix identities + session state.
    """
    existing = seed_mod.read_seeded_email(project.slug)
    target = profile.account_email if profile else ""
    if existing and target and existing != target:
        return existing
    return None


def recreate(
    slug: str,
    *,
    profile_name: str | None = None,
    force: bool = False,
    on_progress: ProgressFn | None = None,
) -> Result:
    """Tear down + rebuild a project's container (keeping workspace + config binds).

    With ``profile_name`` the project is switched to that profile (persisted). If the config dir
    already belongs to a different account, the guard refuses unless ``force`` — which re-seeds the
    identity for the new account (note: the old account's session history stays in the config dir).
    """
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    project = projects_registry.load(slug)
    switching = bool(profile_name and profile_name != (project.profile or ""))
    if profile_name:
        try:
            profiles_registry.load(profile_name)
        except FileNotFoundError:
            return Result(False, f"no profile {profile_name!r}; `claudemanctl profile add {profile_name}`")
        project = dataclasses.replace(project, profile=profile_name)

    profile = effective_profile(project)
    conflict = account_mismatch(project, profile)
    if conflict and not force:
        target = profile.account_email if profile else "?"
        return Result(
            False,
            f"account mismatch: {slug}'s config belongs to {conflict!r} but profile "
            f"{(profile.name if profile else '?')!r} is {target!r}. Re-run with --force to re-seed "
            f"(the old account's session history stays in the config dir).",
        )

    if profile_name:
        try:
            with _slug_lock(slug):  # serialise the switch write with add_repo/remove_repo
                projects_registry.save(project)  # persist the switch only once past the guard
        except OSError as exc:
            return _lock_error(slug, exc)

    runner.remove(slug)  # rm -f; idempotent if the container is absent
    seed_mod.seed_project_config(
        project, profile, overwrite_identity=bool(switching or conflict or force)
    )
    result = up(project, on_progress=on_progress)
    if not result.ok:
        return result
    return Result(True, f"recreated {project.container}"
                  + (f" on profile {profile_name!r}" if profile_name else ""))


def create_project(
    slug: str,
    *,
    profile: str | None = None,
    overlay: str | None = None,
    egress: str | None = None,
    on_progress: ProgressFn | None = None,
) -> Result:
    """Write (or load) the project definition, then create the container."""
    if projects_registry.exists(slug):
        project = projects_registry.load(slug)
    else:
        project = Project(
            slug=slug,
            profile=profile,
            overlay=overlay or config.DEFAULT_OVERLAY,
            egress=egress or config.DEFAULT_EGRESS,
        )
        try:
            with _slug_lock(slug):  # serialise with concurrent add_repo/remove_repo on this slug
                projects_registry.save(project)
        except OSError as exc:
            return _lock_error(slug, exc)
    return ensure_created(project, on_progress=on_progress)


# ---------------------------------------------------------------------------
# Repo add / remove (Phase 3) — registry mutation + host-side clone, no recreate
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _slug_lock(slug: str):
    """Per-slug advisory lock around a registry read-modify-write + clone.

    The TUI ``_busy`` set only serialises workers within one process; two ``claudemanctl`` invocations
    (or an add racing a recreate) are separate processes with no shared state. A non-blocking ``flock``
    on a sibling lockfile fails fast on contention rather than silently losing an update —
    ``_atomic_write`` prevents torn files, not lost read-modify-writes. Linux-only (the target
    platform); ``fcntl`` is stdlib. Raises ``BlockingIOError`` when another holder has the lock, or
    ``OSError`` if the state dir / lockfile can't be created (callers map both to a red ``Result``).
    """
    lockdir = config.project_state_dir(slug)
    lockdir.mkdir(parents=True, exist_ok=True)
    handle = open(lockdir / ".repos.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def _lock_error(slug: str, exc: OSError) -> Result:
    """Map a lock/filesystem failure to a Result (``BlockingIOError`` = contention, else fs fault)."""
    if isinstance(exc, BlockingIOError):
        return Result(False, f"another claude-man operation is modifying {slug!r}; retry")
    return Result(False, f"could not lock {slug!r}: {exc}")


def _purge_checkout(slug: str, repo: Repo) -> str:
    """Containment-checked ``rm -rf`` of a removed repo's on-disk checkout (``--purge`` only)."""
    ws = config.workspace_dir(slug)
    dest = ws / repo.resolved_dir()
    if not repos.is_within(dest, ws):
        return f"refused to purge {dest} (outside workspace/)"
    if not dest.exists():
        return f"no checkout to purge at {dest}"
    shutil.rmtree(dest)
    return f"purged checkout at {dest}"


def add_repo(
    slug: str, url: str, *, branch: str = "main", dir: str = "", clone: bool = True
) -> Result:
    """Register a repo and (default) clone it live into ``/workspace``. NO container recreate.

    Identical whether the project is DEFINED / STOPPED / UP: the clone targets
    ``workspace_dir(slug)``, a writable bind that exists regardless of container state, so a host-side
    clone appears inside a running container immediately. The repos COUNT label drifts on a live
    container; ``status.join`` prefers the registry value + a drift marker (BUG-5) rather than forcing a
    recreate (invariant 4). Registry is written FIRST, so a clone failure still leaves the definition
    correct: ``project sync-repos`` re-clones any not-yet-present repo idempotently before fetching.
    """
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    try:
        with _slug_lock(slug):
            try:
                updated = projects_registry.add_repo(slug, url, branch=branch, dir=dir)
            except ValidationError as exc:
                return Result(False, str(exc))
            new_repo = updated.repos[-1]
            if not clone:
                return Result(True, f"registered {repos.mask_url_creds(url)} in {slug} (not cloned)")
            res = repos.clone_one(slug, new_repo)
            if res.ok:
                return Result(True, f"added {res.dir} to {slug}")
            return Result(False, f"registry updated; clone failed: {res.detail} "
                          f"(retry with `project sync-repos {slug}`)")
    except OSError as exc:
        return _lock_error(slug, exc)


def remove_repo(slug: str, target: str, *, purge: bool = False) -> Result:
    """Drop a repo from the registry. Default: leave the checkout on disk; ``--purge`` deletes it.

    Registry-only by default because a ``/workspace`` checkout can hold unsynced/uncommitted agent
    work (architecture: "persistence is the default, deletion is explicit"). ``--purge`` is the
    explicit, containment-checked opt-in.
    """
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    try:
        with _slug_lock(slug):
            try:
                _, removed = projects_registry.remove_repo(slug, target)
            except ValidationError as exc:  # a hand-edited TOML can fail re-validation on load
                return Result(False, str(exc))
            if removed is None:
                return Result(True, f"no repo matching {target!r} in {slug} (nothing to do)")
            detail = f"removed {removed.resolved_dir()} from {slug}'s registry"
            if purge:
                detail += "; " + _purge_checkout(slug, removed)
            else:
                dest = config.workspace_dir(slug) / removed.resolved_dir()
                detail += f" (checkout left on disk at {dest}; pass --purge to delete it)"
            return Result(True, detail)
    except OSError as exc:
        return _lock_error(slug, exc)


# ---------------------------------------------------------------------------
# Project delete (Phase 3) — full teardown: rm -f the container, rm -rf the state dir, rm the toml.
# Two phases mirror the pull flow so the TUI can preview sync risk before the irreversible delete:
# delete_plan (read-only: scan each repo for unsynced work) -> delete_project (flocked teardown).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DeleteRiskItem:
    """One repo's delete-time sync assessment: its dir, whether deleting risks losing work, and the
    human reason (the string from ``gitstate.delete_risk``)."""
    dir: str
    risky: bool
    reason: str


@dataclass(frozen=True)
class DeletePlan:
    """The previewable outcome of a delete: per-repo sync risk + container liveness. ``risky`` is the
    aggregate the confirm UI gates its loud 'delete anyway' warning on."""
    slug: str
    items: tuple[DeleteRiskItem, ...] = ()
    running: bool = False

    @property
    def risky(self) -> bool:
        return any(it.risky for it in self.items)


def delete_plan(slug: str) -> DeletePlan:
    """Scan every repo's live (fetch-less) git state and assess whether deleting would lose work.

    Read-only — mutates nothing (the preview the TUI confirm modal renders before any teardown). Does
    shell out though: per-repo ``git status`` (via ``gitstate.project_states``) plus one ``docker
    inspect`` for liveness (``runner.is_running``). NOT flocked: the scan only reads. Fetch-less by
    design — a teardown shouldn't block on the network, and
    a stale remote-tracking ref only ever *over*-warns (flags as unpushed something a peer already has),
    which is the safe direction for an irreversible op. The operator can `pull`/`sync-repos` first to
    refresh ahead/behind if they want certainty.
    """
    project = projects_registry.load(slug)
    items = tuple(
        DeleteRiskItem(dir=s.dir, risky=risky, reason=reason)
        for s in gitstate.project_states(project)
        for risky, reason in (gitstate.delete_risk(s),)
    )
    return DeletePlan(slug=slug, items=items, running=runner.is_running(slug))


def _remove_state(slug: str, *, keep_workspace: bool) -> str:
    """Remove a project's state dir (``rm -rf``), or — with ``keep_workspace`` — only the claude-side
    state (config dir, backups, baseline), leaving the ``/workspace`` checkout on disk. Best-effort and
    idempotent (a missing path is fine); returns a human note for the Result detail."""
    state_dir = config.project_state_dir(slug)
    if not state_dir.exists():
        return "no state dir to remove"
    if not keep_workspace:
        shutil.rmtree(state_dir, ignore_errors=True)
        return "removed state dir (workspace + claude-config + backups)"
    removed: list[str] = []
    for path in (config.claude_config_dir(slug), config.backups_dir(slug),
                 config.baseline_path(slug), config.project_env_path(slug)):
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
                removed.append(path.name)
            elif path.exists():
                path.unlink()
                removed.append(path.name)
        except OSError:
            continue  # best-effort — never abort the delete on a single stubborn path
    return (f"kept workspace checkout at {config.workspace_dir(slug)}; removed "
            + (", ".join(removed) or "no other state"))


def delete_project(slug: str, *, keep_workspace: bool = False) -> Result:
    """Tear a project down: ``rm -f`` its container, remove its state dir, delete its registry toml.

    Idempotent and recoverable-by-ordering: the container goes first, the state dir next, and the
    registry toml LAST — so if any step fails the project is still listed and ``delete`` can be retried
    (a leftover container then shows as an orphan row, never silently billed; invariant 4: the toml is
    the source of truth — while it exists the project is "known"). With ``keep_workspace`` the on-disk
    ``/workspace`` checkout is preserved (container + claude-config + backups + registry still go) — the
    non-destructive exit for when the operator has unsynced work but wants the project out of
    claude-man's management. Holds the per-slug flock so it can't interleave with
    add_repo/remove_repo/recreate; the lock file lives under the state dir being removed, which is fine
    on Linux (the open fd outlives the unlink — the documented target platform).
    """
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    try:
        with _slug_lock(slug):
            runner.remove(slug)  # docker rm -f; idempotent — a non-zero "No such container" is fine
            state_note = _remove_state(slug, keep_workspace=keep_workspace)
            projects_registry.delete_definition(slug)  # LAST: the source-of-truth removal
    except OSError as exc:
        return _lock_error(slug, exc)
    return Result(True, f"deleted {slug}: removed container + registry entry; {state_note}")


# ---------------------------------------------------------------------------
# Env mounts (ssh + files) — registry mutation; mounts are fixed at container create, so a change
# needs `recreate` to take effect (surfaced in the Result, like the repos label drift).
# ---------------------------------------------------------------------------
def _mount_desc(m: EnvMount) -> str:
    if m.kind == "ssh":
        return "ssh (agent-forward)"
    if m.kind == "env":
        return f"env {m.name} (value hidden)"
    return f"{m.src} → {m.dst}"


def add_mount(slug: str, mount: EnvMount) -> Result:
    """Register an env mount (the ``EnvMount`` is already validated by its ``__post_init__``)."""
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    try:
        with _slug_lock(slug):
            try:
                projects_registry.add_mount(slug, mount)
            except ValidationError as exc:
                return Result(False, str(exc))
            return Result(True, f"added {_mount_desc(mount)} to {slug} — "
                          f"`recreate` to apply (mounts are fixed at container create)")
    except OSError as exc:
        return _lock_error(slug, exc)


def add_env_var(slug: str, name: str, value: str) -> Result:
    """Register a ``kind="env"`` mount and store its VALUE 0600 in the state tier (never config.toml).

    The value is injected pass-through (``-e NAME``) at the next ``recreate``, like GH_TOKEN. The name
    is validated (and forbidden names rejected) by ``EnvMount``'s ``__post_init__``; the registry write
    + value store happen under the per-slug flock so they can't interleave with another mutator."""
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    try:
        mount = EnvMount(kind="env", name=name)  # validates the name (+ rejects forbidden ones)
    except ValidationError as exc:
        return Result(False, str(exc))
    try:
        with _slug_lock(slug):
            try:
                projects_registry.add_mount(slug, mount)  # cross-entry guard: no duplicate name
            except ValidationError as exc:
                return Result(False, str(exc))
            try:
                env_secrets.set(slug, mount.name, value)  # value to the 0600 state store, under the lock
            except OSError as exc:
                # Roll back the just-added mount so we never leave a value-less orphan (which would
                # render a no-op `-e NAME` at recreate). Both writes succeed together or neither does.
                with contextlib.suppress(Exception):
                    projects_registry.remove_mount(slug, mount.name)
                return Result(False, f"failed to store value for {mount.name}: {exc}")
            return Result(True, f"set env var {mount.name} for {slug} — `recreate` to apply")
    except OSError as exc:
        return _lock_error(slug, exc)


def _port_desc(p: PortMapping) -> str:
    where = "host-only (localhost)" if not p.exposed else f"EXPOSED on {p.bind}"
    return f"{p.host_eff}->{p.container}/{p.proto} [{where}]"


def add_port(slug: str, mapping: PortMapping) -> Result:
    """Publish a container service port (the ``project ports add`` verb). Registry-only; ports are fixed
    at container create, so the Result reminds to ``recreate``. The ``PortMapping`` is already validated
    (container ≥1024, proto, bind IP) by its ``__post_init__``; the cross-entry host-port collision guard
    fires in ``projects.add_port`` under the per-slug flock."""
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    try:
        with _slug_lock(slug):
            try:
                projects_registry.add_port(slug, mapping)
            except ValidationError as exc:
                return Result(False, str(exc))
            return Result(True, f"published {_port_desc(mapping)} for {slug} — `recreate` to apply "
                          f"(ports are fixed at container create)")
    except OSError as exc:
        return _lock_error(slug, exc)


def remove_port(slug: str, target: str) -> Result:
    """Unpublish a port from the registry (matched by ``<host>[/proto]``). `recreate` to apply."""
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    try:
        with _slug_lock(slug):
            try:
                _, removed = projects_registry.remove_port(slug, target)
            except ValidationError as exc:
                return Result(False, str(exc))
            if removed is None:
                return Result(True, f"no published port matching {target!r} in {slug} (nothing to do)")
            return Result(True, f"unpublished {_port_desc(removed)} from {slug} — `recreate` to apply")
    except OSError as exc:
        return _lock_error(slug, exc)


def remove_mount(slug: str, target: str) -> Result:
    """Drop an env mount from the registry (matched by 'ssh', a file's dst, or an env var's name)."""
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    try:
        with _slug_lock(slug):
            _, removed = projects_registry.remove_mount(slug, target)
            if removed is None:
                return Result(True, f"no env mount matching {target!r} in {slug} (nothing to do)")
            if removed.kind == "env":
                env_secrets.remove(slug, removed.name)  # drop the stored value too (no orphan secret)
            return Result(True, f"removed {_mount_desc(removed)} from {slug} — `recreate` to apply")
    except OSError as exc:
        return _lock_error(slug, exc)


def resync(slug: str) -> Result:
    """Re-validate env-mount sources and re-seed the ssh ~/.ssh tmpfs from the host (no recreate).

    File binds are live (their content already reflects the host), so resync mainly (a) revalidates
    that every source still exists/readable, and (b) re-seeds ssh config/known_hosts into a running
    container. Adding/removing a mount still needs `recreate` — resync does not change the mount set.
    """
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    project = projects_registry.load(slug)
    if not project.env_mount:
        return Result(True, f"{slug}: no env mounts configured")
    errors, warnings = _validate_mount_sources(project)
    notes: list[str] = list(warnings)
    if _has_ssh_mount(project):
        if runner.is_running(slug):
            _seed_ssh(slug)
            notes.append("re-seeded ssh config/known_hosts")
        else:
            notes.append("container not running — `up` it to seed ssh")
    detail = f"{slug}: revalidated {len(project.env_mount)} env mount(s)"
    if errors:
        detail += "; ERRORS: " + "; ".join(errors)
    if notes:
        detail += "; " + "; ".join(notes)
    return Result(not errors, detail)


# ---------------------------------------------------------------------------
# Repo pull (ff-only) — bring the live /workspace bind up to date so a running container sees the
# latest commits without a recreate. Host-side, fast-forward-ONLY, safe-by-default: the working tree
# is the gh-PAT/ssh-stays-host-side surface (invariant 1 family), exactly like clone_all/fetch_all.
# Two phases so the TUI can preview before mutating: pull_plan (read-only: fetch + scan + decide) ->
# pull_apply (flocked: re-decide + ff-merge the chosen repos). See ROADMAP / REVIEW.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PullItem:
    """One repo's entry in a pull plan: its dir, whether it's ff-eligible, and the human reason (the
    string from ``gitstate.pull_decision``, which already embeds the behind-count for an eligible repo)."""
    dir: str
    eligible: bool
    reason: str


@dataclass(frozen=True)
class PullPlan:
    """The previewable outcome of a fetch: per-repo ff plan + any fetch failures + container liveness.

    ``running`` lets the confirm UI warn that a live ``claude`` may be editing these files (ff-only
    skips any repo it has dirtied, so the warning is advisory, not a blocker)."""
    slug: str
    items: tuple[PullItem, ...] = ()
    fetch_errors: tuple[str, ...] = ()
    running: bool = False

    @property
    def eligible(self) -> list[str]:
        return [it.dir for it in self.items if it.eligible]


def pull_plan(slug: str) -> PullPlan:
    """Fetch every repo (network, read-only) and compute the per-repo fast-forward plan. Mutates NO
    working tree — this is the preview the TUI confirm modal renders before any pull is applied.

    NOT flocked: a ``git fetch`` is read-only and races nothing — the *apply* phase holds the per-slug
    lock. ``fetch_all`` updates the remote-tracking refs first so the subsequent ``project_states``
    ahead/behind (and thus the ff decision) reflects the remote, not a stale local view.
    """
    project = projects_registry.load(slug)
    fetch_results = repos.fetch_all(project) if project.repos else []
    states = gitstate.project_states(project)
    present = {s.dir for s in states if s.present}
    # Only surface fetch failures for repos that ARE cloned — an uncloned repo's "not cloned" fetch
    # result is already reflected in its per-repo skip reason, so listing it twice is just noise.
    fetch_errors = tuple(
        f"{r.dir}: {r.detail}" for r in fetch_results if not r.ok and r.dir in present
    )
    items = tuple(
        PullItem(dir=s.dir, eligible=ok, reason=reason)
        for s in states
        for ok, reason in (gitstate.pull_decision(s),)
    )
    return PullPlan(slug=slug, items=items, fetch_errors=fetch_errors, running=runner.is_running(slug))


def pull_apply(slug: str, dirs: list[str], *, on_progress: ProgressFn | None = None) -> Result:
    """Fast-forward-only pull of the named repo dirs (host-side; NO recreate — ``/workspace`` is a live
    bind, so the new commits appear in a running container immediately).

    Holds the per-slug flock so it can't interleave with add_repo/remove_repo/recreate. Re-scans and
    re-decides each repo *under the lock* (TOCTOU: a live ``claude`` may have dirtied a tree since the
    plan was shown), and ``git merge --ff-only`` is the in-git backstop if it did. A safety SKIP is not
    a failure — the Result is ``ok`` unless an *eligible* repo's merge hard-failed. Refuses outright on
    a host/container uid mismatch: a host-side merge would write wrong-owner files and trip
    "dubious ownership" (claude-man never auto-writes ``safe.directory`` — invariant family).
    """
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    if not gitstate.host_uid_matches_container():
        huid = getattr(os, "getuid", lambda: "?")()  # getuid-absence-safe, like host_uid_matches_container
        return Result(
            False,
            f"refusing to pull: host uid {huid} != container uid {config.CONTAINER_UID} "
            f"(a host-side pull would write wrong-owner files and trip 'dubious ownership'). "
            f"Run claude-man as uid {config.CONTAINER_UID}.",
        )
    try:
        with _slug_lock(slug):  # serialise vs add_repo/remove_repo/recreate on this slug
            project = projects_registry.load(slug)
            by_dir = {r.resolved_dir(): r for r in project.repos}
            states = {s.dir: s for s in gitstate.project_states(project)}
            pulled: list[str] = []
            skipped: list[str] = []
            failed: list[str] = []
            for d in dirs:
                repo = by_dir.get(d)
                if repo is None or d not in states:
                    failed.append(f"{d}: not in registry")
                    continue
                ok, reason = gitstate.pull_decision(states[d])
                if not ok:
                    skipped.append(f"{d}: {reason}")  # re-decided under the lock; state moved on us
                    continue
                res = repos.ff_merge_one(slug, repo)
                if on_progress:
                    on_progress(f"{d}: {res.detail}")
                (pulled if res.ok else failed).append(f"{d}: {res.detail}")
    except OSError as exc:
        return _lock_error(slug, exc)

    parts = [f"pulled {len(pulled)}"]
    if skipped:
        parts.append(f"skipped {len(skipped)}")
    if failed:
        parts.append(f"failed {len(failed)}")
    detail = f"{slug}: " + ", ".join(parts) + " (ff-only; live in the container — no recreate needed)"
    for line in pulled + skipped + failed:
        detail += f"\n  {line}"
    return Result(not failed, detail)


# ---------------------------------------------------------------------------
# Global settings — host-environment bootstrap (ssh keys, "general features"). claude-man loads the
# configured keys into the ssh-agent on startup AND on add, so the operator never has to ssh-add by
# hand before using a container. Keys stay host-side (only the agent socket is forwarded). See
# ssh_agent.py + registry/settings.py.
# ---------------------------------------------------------------------------
def _fold_key_results(results: list[ssh_agent.KeyResult], *, header: str) -> Result:
    loaded = sum(1 for r in results if r.ok)
    detail = f"{header}: {loaded}/{len(results)} key(s) loaded" if results else f"{header}: none"
    for r in results:
        detail += f"\n  {r.path}: {r.detail}"
    return Result(all(r.ok for r in results), detail)


def ensure_ssh_keys(*, force: bool = False) -> Result:
    """Load the configured ssh keys into the agent (the host-environment bootstrap).

    Called on TUI/CLI startup so the operator never has to ``ssh-add`` by hand. Respects
    ``ssh_auto_load`` unless ``force``. Via ``ssh_agent.ensure_agent`` this also sets
    ``SSH_AUTH_SOCK`` in ``os.environ`` so subsequent container creates forward the right agent. Keys
    never enter a container — only the agent socket is forwarded by a project's ``ssh`` env-mount.
    """
    settings = settings_registry.load()
    if not settings.ssh_keys:
        return Result(True, "no ssh keys configured")
    if not settings.ssh_auto_load and not force:
        return Result(True, "ssh auto-load disabled (config ssh load to force)")
    return _fold_key_results(ssh_agent.ensure_keys(list(settings.ssh_keys)), header="ssh keys")


def add_ssh_key(path: str) -> Result:
    """Add a key to the global settings AND load it into the agent now (so add == available)."""
    try:
        _, added = settings_registry.add_ssh_key(path)
    except ValidationError as exc:
        return Result(False, str(exc))
    results = ssh_agent.ensure_keys([path])
    note = f"added {path} to config" if added else f"{path} already in config"
    detail = note + (f"; {results[0].detail}" if results else "")
    return Result(results[0].ok if results else True, detail)


def remove_ssh_key(path: str) -> Result:
    """Stop auto-loading a key. Does NOT ``ssh-add -d`` it (it may still be wanted this session)."""
    _, removed = settings_registry.remove_ssh_key(path)
    if not removed:
        return Result(True, f"{path} was not in config")
    return Result(True, f"removed {path} from config (still loaded this session until you `ssh-add -d`)")
