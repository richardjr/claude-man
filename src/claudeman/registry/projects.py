"""Load / list / write project definitions (``~/.config/claude-man/projects/<slug>.toml``).

Canonical TOML shape (see templates/project.toml.example)::

    [project]
    slug = "landarna"
    profile = "work"            # optional; omit to inherit the default profile
    overlay = "node"
    extra_apt = ["jq"]          # optional
    # env_file = "~/Work/landarna_environment_local"   # alternative to [project.env]

    [project.egress]
    mode = "open"               # "open" | "strict"
    allowlist = ["registry.yarnpkg.com"]   # consulted only in strict mode

    [project.env]
    NODE_ENV = "development"

    [[project.repos]]
    url = "git@github.com:3ADAPT/landarna-backend.git"
    branch = "main"
    dir = "landarna-backend"

Reading uses stdlib ``tomllib``; writing preserves comments via ``tomlkit`` when installed.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from pathlib import Path

from .. import config
from .schema import (
    DEFAULT_SYNC_CLAUDE,
    DEFAULT_SYNC_WORKSPACE,
    EnvMount,
    Project,
    Repo,
    Sync,
    ValidationError,
)


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` so a concurrent reader never observes a torn file.

    The registry can be read on the TUI thread (the 2s projects poll) while it is written on
    another (the create worker), so a plain ``write_text`` truncate-then-write would expose a
    partial file mid-write. Writing a sibling temp file then ``os.replace`` (atomic on the same
    filesystem) closes that window. The temp name includes the target name so distinct slugs
    never collide; same-slug writers produce identical bytes, so a double replace is harmless.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _env_str(value) -> str:
    """Coerce a TOML scalar to an env-var string (review BUG-1).

    TOML allows int/float/bool in a table; docker env values must be strings. Bools become
    lowercase ``true``/``false`` (not Python's ``True``/``False``) to match shell expectations.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _parse(data: dict, slug_hint: str | None = None) -> Project:
    proj = data.get("project")
    if not isinstance(proj, dict):
        raise ValidationError("missing [project] table")
    slug = proj.get("slug", slug_hint)
    if not slug:
        raise ValidationError("project.slug is required")

    egress_tbl = proj.get("egress", {}) or {}
    repos = tuple(
        Repo(url=r["url"], branch=r.get("branch", "main"), dir=r.get("dir", ""))
        for r in proj.get("repos", [])
    )
    mounts = tuple(
        EnvMount.lenient(kind=m.get("kind", ""), src=m.get("src", ""), dst=m.get("dst", ""),
                         ro=bool(m.get("ro", True)))
        for m in proj.get("env_mount", [])
    )
    sync_tbl = proj.get("sync", {}) or {}
    sync = Sync(
        enabled=bool(sync_tbl.get("enabled", True)),
        workspace=tuple(sync_tbl.get("workspace", DEFAULT_SYNC_WORKSPACE)),
        claude=tuple(sync_tbl.get("claude", DEFAULT_SYNC_CLAUDE)),
    )
    return Project(
        slug=slug,
        profile=proj.get("profile"),
        overlay=proj.get("overlay", config.DEFAULT_OVERLAY),
        egress=egress_tbl.get("mode", config.DEFAULT_EGRESS),
        env={k: _env_str(v) for k, v in (proj.get("env", {}) or {}).items()},
        env_file=proj.get("env_file"),
        workdir=proj.get("workdir", ""),
        extra_apt=tuple(proj.get("extra_apt", []) or ()),
        repos=repos,
        env_mount=mounts,
        allowlist=tuple(egress_tbl.get("allowlist", []) or ()),
        sync=sync,
    )


def load(slug: str) -> Project:
    path = config.project_toml_path(slug)
    if not path.exists():
        raise FileNotFoundError(f"no project {slug!r} at {path}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return _parse(data, slug_hint=slug)


def load_path(path: Path) -> Project:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return _parse(data, slug_hint=path.stem)


def list_slugs() -> list[str]:
    d = config.projects_config_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.toml"))


def list_projects() -> list[Project]:
    out: list[Project] = []
    for slug in list_slugs():
        try:
            out.append(load(slug))
        except (ValidationError, tomllib.TOMLDecodeError, FileNotFoundError):
            # Skip malformed/half-written TOML (e.g. a hand-broken file, or — with atomic writes
            # now in place — a defensive guard) rather than crashing the caller's listing.
            continue
    return out


def exists(slug: str) -> bool:
    return config.project_toml_path(slug).exists()


def save(project: Project) -> Path:
    """Write a project definition, preserving comments where possible (needs tomlkit)."""
    try:
        import tomlkit
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on env
        raise RuntimeError("writing project TOML requires the 'tomlkit' dependency") from exc

    doc = tomlkit.document()
    proj = tomlkit.table()
    proj["slug"] = project.slug
    if project.profile:
        proj["profile"] = project.profile
    proj["overlay"] = project.overlay
    if project.workdir:
        proj["workdir"] = project.workdir
    if project.extra_apt:
        proj["extra_apt"] = list(project.extra_apt)
    if project.env_file:
        proj["env_file"] = project.env_file

    egress = tomlkit.table()
    egress["mode"] = project.egress
    if project.allowlist:
        egress["allowlist"] = list(project.allowlist)
    proj["egress"] = egress

    if project.env:
        env = tomlkit.table()
        for k, v in project.env.items():
            env[k] = v
        proj["env"] = env

    if project.repos:
        arr = tomlkit.aot()
        for r in project.repos:
            t = tomlkit.table()
            t["url"] = r.url
            t["branch"] = r.branch
            if r.dir:
                t["dir"] = r.dir
            arr.append(t)
        proj["repos"] = arr

    if project.env_mount:
        marr = tomlkit.aot()
        for m in project.env_mount:
            t = tomlkit.table()
            t["kind"] = m.kind
            if m.src:
                t["src"] = m.src
            if m.dst:
                t["dst"] = m.dst
            if not m.ro:
                t["ro"] = False
            marr.append(t)
        proj["env_mount"] = marr

    # Emit [project.sync] only when it diverges from the defaults (keep copied templates clean).
    default_sync = Sync()
    if project.sync != default_sync:
        synct = tomlkit.table()
        if not project.sync.enabled:
            synct["enabled"] = False
        if project.sync.workspace != default_sync.workspace:
            synct["workspace"] = list(project.sync.workspace)
        if project.sync.claude != default_sync.claude:
            synct["claude"] = list(project.sync.claude)
        proj["sync"] = synct

    doc["project"] = proj

    path = config.project_toml_path(project.slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, tomlkit.dumps(doc))
    return path


def _rewrite_array(slug: str, key: str, rows: list[dict]) -> None:
    """Patch only the ``[[project.<key>]]`` AOT of ``<slug>.toml``, preserving operator comments.

    Unlike ``save()`` (which rebuilds the whole document from the dataclass and loses comments),
    this parses the existing file with tomlkit and replaces just one array of tables, so the heavily
    commented template an operator copied from keeps its prose. Atomic write closes the torn-read
    window against the 2 s TUI poll. ``rows`` is a list of ordered dicts (key->value); an empty list
    deletes the array.
    """
    try:
        import tomlkit
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on env
        raise RuntimeError("writing project TOML requires the 'tomlkit' dependency") from exc

    path = config.project_toml_path(slug)
    doc = tomlkit.parse(path.read_text())
    proj = doc["project"]
    if rows:
        arr = tomlkit.aot()
        for row in rows:
            t = tomlkit.table()
            for k, v in row.items():
                t[k] = v
            arr.append(t)
        proj[key] = arr
    elif key in proj:
        del proj[key]
    _atomic_write(path, tomlkit.dumps(doc))


def _rewrite_repos(slug: str, repos: tuple[Repo, ...]) -> None:
    _rewrite_array(slug, "repos", [
        {"url": r.url, "branch": r.branch, **({"dir": r.dir} if r.dir else {})} for r in repos
    ])


def _rewrite_mounts(slug: str, mounts: tuple[EnvMount, ...]) -> None:
    rows: list[dict] = []
    for m in mounts:
        row: dict = {"kind": m.kind}
        if m.src:
            row["src"] = m.src
        if m.dst:
            row["dst"] = m.dst
        if not m.ro:
            row["ro"] = False
        rows.append(row)
    _rewrite_array(slug, "env_mount", rows)


def add_repo(slug: str, url: str, *, branch: str = "main", dir: str = "") -> Project:
    """Append a ``[[repos]]`` entry to ``<slug>.toml`` and atomically rewrite it (comments kept).

    Pure registry edit — does NOT clone or touch docker (see ``lifecycle.add_repo`` for the
    clone-wired flow). Returns the updated ``Project`` with the new repo appended last. Per-``Repo``
    validation (url required, dir containment) lives in ``schema.Repo.__post_init__``; the only
    *cross-repo* guards a single ``Repo`` can't see — duplicate url, resolved-dir collision — are
    enforced here. Raises ``ValidationError`` on a collision so the caller can surface it verbatim.
    """
    project = load(slug)  # FileNotFoundError if undefined — let it propagate
    new = Repo(url=url.strip(), branch=(branch or "main"), dir=(dir or "").strip())
    resolved = new.resolved_dir()
    for existing in project.repos:
        if existing.url == new.url:
            raise ValidationError(f"repo {new.url!r} is already in project {slug!r}")
        if existing.resolved_dir() == resolved:
            raise ValidationError(
                f"dir collision: {new.url!r} resolves to workspace subdir {resolved!r}, "
                f"already used by {existing.url!r} — pass an explicit dir="
            )
    updated = dataclasses.replace(project, repos=project.repos + (new,))
    _rewrite_repos(slug, updated.repos)
    return updated


def remove_repo(slug: str, target: str) -> tuple[Project, Repo | None]:
    """Drop a ``[[repos]]`` entry matched by resolved dir OR exact url. Idempotent.

    Returns ``(updated_project, removed_repo|None)``. ``removed`` is ``None`` when nothing matched
    (idempotent no-op — no write, no error). Pure registry edit; deleting the on-disk checkout is the
    caller's explicit opt-in (``lifecycle.remove_repo(..., purge=True)``).
    """
    project = load(slug)
    target = target.strip()
    kept: list[Repo] = []
    removed: Repo | None = None
    for r in project.repos:
        if removed is None and (r.resolved_dir() == target or r.url == target):
            removed = r
        else:
            kept.append(r)
    if removed is None:
        return project, None
    updated = dataclasses.replace(project, repos=tuple(kept))
    _rewrite_repos(slug, updated.repos)
    return updated, removed


def add_mount(slug: str, mount: EnvMount) -> Project:
    """Append an ``[[env_mount]]`` entry (comment-preserving). ``mount`` is already validated by
    ``EnvMount.__post_init__``; this adds the cross-entry guards (at most one ssh; no dst collision).
    Raises ``ValidationError`` on a collision.
    """
    project = load(slug)
    for existing in project.env_mount:
        if mount.kind == "ssh" and existing.kind == "ssh":
            raise ValidationError(f"project {slug!r} already has an ssh env mount")
        if mount.kind == "file" and existing.kind == "file" and existing.dst == mount.dst:
            raise ValidationError(f"a file mount already targets {mount.dst!r} in {slug!r}")
    updated = dataclasses.replace(project, env_mount=project.env_mount + (mount,))
    _rewrite_mounts(slug, updated.env_mount)
    return updated


def remove_mount(slug: str, target: str) -> tuple[Project, EnvMount | None]:
    """Drop an ``[[env_mount]]`` entry matched by its unambiguous target — ``'ssh'`` or a file's
    container dst. (Host ``src`` is intentionally NOT a match key: a literal src of ``'ssh'`` would
    collide with the ssh target, and two files can share a src.) Idempotent: returns
    ``(updated_project, removed|None)`` (``None`` = no match, no write)."""
    project = load(slug)
    target = target.strip()
    kept: list[EnvMount] = []
    removed: EnvMount | None = None
    for m in project.env_mount:
        if removed is None and m.target == target:
            removed = m
        else:
            kept.append(m)
    if removed is None:
        return project, None
    updated = dataclasses.replace(project, env_mount=tuple(kept))
    _rewrite_mounts(slug, updated.env_mount)
    return updated, removed


def delete_definition(slug: str) -> bool:
    path = config.project_toml_path(slug)
    if path.exists():
        path.unlink()
        return True
    return False
