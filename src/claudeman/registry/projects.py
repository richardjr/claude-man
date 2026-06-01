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

import tomllib
from pathlib import Path

from .. import config
from .schema import Project, Repo, ValidationError


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
    return Project(
        slug=slug,
        profile=proj.get("profile"),
        overlay=proj.get("overlay", config.DEFAULT_OVERLAY),
        egress=egress_tbl.get("mode", config.DEFAULT_EGRESS),
        env=dict(proj.get("env", {}) or {}),
        env_file=proj.get("env_file"),
        extra_apt=tuple(proj.get("extra_apt", []) or ()),
        repos=repos,
        allowlist=tuple(egress_tbl.get("allowlist", []) or ()),
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
        except (ValidationError, FileNotFoundError):
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

    doc["project"] = proj

    path = config.project_toml_path(project.slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc))
    return path


def delete_definition(slug: str) -> bool:
    path = config.project_toml_path(slug)
    if path.exists():
        path.unlink()
        return True
    return False
