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

from . import config, ssh_agent
from .checkout import gitstate, repos
from .docker import images, runner
from .profiles import seed as seed_mod
from .registry import profiles as profiles_registry
from .registry import projects as projects_registry
from .registry import settings as settings_registry
from .registry.schema import EnvMount, Profile, Project, Repo, ValidationError

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
        elif m.kind == "ssh" and not os.environ.get("SSH_AUTH_SOCK"):
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

    cp = runner.create(project, profile_name=profile_name, token=token, created_iso=_now_iso())
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
    suffix = ("  [" + "; ".join(notes) + "]") if notes else ""
    return Result(True, f"created {project.container}{suffix}")


def up(project: Project, *, on_progress: ProgressFn | None = None) -> Result:
    """Create-if-needed, then start."""
    created = ensure_created(project, on_progress=on_progress)
    if not created.ok:
        return created
    cp = runner.start(project.slug)
    if cp.returncode != 0:
        return Result(False, f"docker start failed: {cp.stderr.strip() or cp.stdout.strip()}")
    if _has_ssh_mount(project):
        _seed_ssh(project.slug)  # populate the ~/.ssh tmpfs config/known_hosts (post-start, exec-stdin)
    prefix = created.detail + "; " if "created" in created.detail else ""
    return Result(True, f"{prefix}started {project.container}")


def stop(slug: str) -> Result:
    """Stop the container (never removes it — persistence is the default)."""
    cp = runner.stop(slug)
    if cp.returncode != 0:
        return Result(False, f"docker stop failed: {cp.stderr.strip() or cp.stdout.strip()}")
    return Result(True, f"stopped {config.container_name(slug)}")


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
# Env mounts (ssh + files) — registry mutation; mounts are fixed at container create, so a change
# needs `recreate` to take effect (surfaced in the Result, like the repos label drift).
# ---------------------------------------------------------------------------
def _mount_desc(m: EnvMount) -> str:
    return "ssh (agent-forward)" if m.kind == "ssh" else f"{m.src} → {m.dst}"


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


def remove_mount(slug: str, target: str) -> Result:
    """Drop an env mount from the registry (matched by 'ssh' or a file's container dst)."""
    if not projects_registry.exists(slug):
        return Result(False, f"no project {slug!r}")
    try:
        with _slug_lock(slug):
            _, removed = projects_registry.remove_mount(slug, target)
            if removed is None:
                return Result(True, f"no env mount matching {target!r} in {slug} (nothing to do)")
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
