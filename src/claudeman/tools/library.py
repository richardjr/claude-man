"""The approved-tool registry — discovery + parsing of ``library/tools/<name>/tool.toml``.

PURE read side (stdlib only, no writes), the ``packs/library.py`` pattern. A **tool** is one
directory carrying a ``tool.toml`` that says HOW it is installed under the hardened floor:

* ``kind = "apt"`` — Debian (trixie) packages, or
* ``kind = "release"`` — a pinned upstream artefact per arch (``[release.amd64]`` /
  ``[release.arm64]``: ``url`` + ``sha256``, verified at build) placed as a bare ``binary``, a
  ``tar`` (listed ``members`` installed to ``/usr/local/bin`` by basename), a ``deb``, or a
  ``bundle`` (a zip carrying one self-contained directory ``tree`` — an embedded-runtime
  distribution like the AWS CLI v2's ``aws/dist`` — installed whole to ``/opt/<name>`` with its
  ``bins`` symlinked into ``/usr/local/bin``; needs ``unzip`` in ``build_deps``);

plus the "custom config" a tool needs to actually WORK under ``--read-only`` (invariant 2):
``[env]`` redirects (image ENV, so a HOME-dotdir write lands on a writable surface), ``[[smoke]]``
probes (its CORE operation exercised by ``image smoke`` as uid 1000 — not just ``--version``),
``requires`` (other tools pulled in automatically), ``build_deps`` (apt packages needed only at
build, purged after), and an ``allowlist`` hint (runtime hosts a LOCKED project must add).

The shipped library is linted by a test (``discover()`` on the real tree must not raise); a
hand-edited entry fails loud with ``LibraryError``. See docs/TOOLS.md.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .. import agents, config

TOOL_META = "tool.toml"
KINDS = ("apt", "release")
INSTALLS = ("binary", "tar", "deb", "bundle")
ARCHES = ("amd64", "arm64")   # dpkg --print-architecture names; both are always required

# Same shape as project slugs — tool names become registry entries + image-name components.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")        # Debian package-name shape
_BIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")   # an installed executable's basename
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]*$")
# A [env] entry must never touch the container's identity/auth/path plumbing — those are the
# runner's (invariant 1 + the baked floor env), not a tool's.
_RESERVED_ENV = frozenset({"HOME", "PATH", "USER", "XDG_CACHE_HOME", "XDG_STATE_HOME"}) | agents.config_dir_envs()


class LibraryError(ValueError):
    """A malformed library — bad name, missing/invalid metadata, unknown selection, dependency
    cycle. The shipped library must never raise this (a lint test imports the real tree)."""


@dataclass(frozen=True)
class ReleaseArch:
    url: str
    sha256: str
    members: tuple[str, ...] = ()   # tar only: archive paths installed to /usr/local/bin by basename


@dataclass(frozen=True)
class SmokeSpec:
    name: str
    argv: tuple[str, ...]
    expect: str = ""
    timeout: int = 15


@dataclass(frozen=True)
class Tool:
    name: str                     # directory name — the registry key
    description: str
    kind: str                     # KINDS
    path: Path                    # the tool directory
    packages: tuple[str, ...] = ()        # apt: Debian packages
    version: str = ""                     # release: the pinned upstream version
    install: str = ""                     # release: INSTALLS
    bin: str = ""                         # release/binary: the installed name under /usr/local/bin
    tree: str = ""                        # release/bundle: the archive dir installed whole to /opt/<name>
    bins: tuple[str, ...] = ()            # release/bundle: tree-relative executables symlinked into /usr/local/bin
    release: dict[str, ReleaseArch] = field(default_factory=dict)  # release: per-ARCHES artefact
    build_deps: tuple[str, ...] = ()      # apt packages needed only at build (purged after)
    requires: tuple[str, ...] = ()        # other tools pulled into the layer automatically
    note: str = ""                        # free-text caveat shown in listings
    allowlist: tuple[str, ...] = ()       # runtime egress hosts a LOCKED project should add
    env: dict[str, str] = field(default_factory=dict)   # image ENV (read-only-floor redirects)
    smoke: tuple[SmokeSpec, ...] = ()     # image-smoke probes (CORE ops under the floor)

    @property
    def summary(self) -> str:
        """Compact provenance cell: ``"apt: jq"`` / ``"release 1.37.0"``."""
        if self.kind == "apt":
            return "apt: " + " ".join(self.packages)
        return f"release {self.version}"


def library_root() -> Path:
    return config.library_tools_dir()


def discover(root: Path | None = None) -> dict[str, Tool]:
    """``name -> Tool`` for the whole library (sorted by name).

    Raises ``LibraryError`` on any malformed entry, an unknown ``requires`` target, or a
    dependency cycle — this doubles as the curation lint. A missing library root returns ``{}``
    (tools are optional; nothing else degrades)."""
    root = library_root() if root is None else root
    out: dict[str, Tool] = {}
    if not root.is_dir():
        return out
    for tool_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        tool = _load_tool(tool_dir)
        out[tool.name] = tool
    for tool in out.values():
        for dep in tool.requires:
            if dep not in out:
                raise LibraryError(f"{tool.name}: requires unknown tool {dep!r}")
    for name in out:
        resolve((name,), out)  # raises on a cycle
    return out


def _str_list(meta: dict, key: str, label: str) -> tuple[str, ...]:
    raw = meta.get(key, []) or []
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise LibraryError(f"{label}: {key} must be a list of strings")
    return tuple(raw)


def _load_tool(tool_dir: Path) -> Tool:
    name = tool_dir.name
    if not _NAME_RE.match(name):
        raise LibraryError(f"invalid tool name {name!r} (must match {_NAME_RE.pattern})")
    meta_path = tool_dir / TOOL_META
    if not meta_path.is_file():
        raise LibraryError(f"{name}: missing {TOOL_META}")
    try:
        meta = tomllib.loads(meta_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise LibraryError(f"{name}: unreadable {TOOL_META}: {exc}") from None

    description = str(meta.get("description", "") or "").strip()
    if not description:
        raise LibraryError(f"{name}: {TOOL_META} must set a description")
    kind = str(meta.get("kind", "") or "")
    if kind not in KINDS:
        raise LibraryError(f"{name}: kind must be one of {KINDS} (got {kind!r})")

    packages = _str_list(meta, "packages", name)
    build_deps = _str_list(meta, "build_deps", name)
    for pkg in packages + build_deps:
        if not _PKG_RE.match(pkg):
            raise LibraryError(f"{name}: invalid apt package name {pkg!r}")
    requires = _str_list(meta, "requires", name)
    for dep in requires:
        if not _NAME_RE.match(dep):
            raise LibraryError(f"{name}: invalid requires entry {dep!r}")
        if dep == name:
            raise LibraryError(f"{name}: requires itself")
    allowlist = _str_list(meta, "allowlist", name)

    version, install, bin_name, tree, bins, release = "", "", "", "", (), {}
    if kind == "apt":
        if not packages:
            raise LibraryError(f"{name}: an apt tool must list packages")
        for key in ("version", "install", "bin", "tree", "bins", "release"):
            if key in meta:
                raise LibraryError(f"{name}: {key!r} is only valid for kind = \"release\"")
    else:
        if packages:
            raise LibraryError(f"{name}: 'packages' is only valid for kind = \"apt\"")
        version = str(meta.get("version", "") or "")
        if not _VERSION_RE.match(version):
            raise LibraryError(f"{name}: a release tool must pin a version")
        install = str(meta.get("install", "") or "")
        if install not in INSTALLS:
            raise LibraryError(f"{name}: install must be one of {INSTALLS} (got {install!r})")
        bin_name = str(meta.get("bin", "") or "")
        if install == "binary":
            if not _BIN_RE.match(bin_name):
                raise LibraryError(f"{name}: install = \"binary\" needs a valid `bin` name")
        elif bin_name:
            raise LibraryError(f"{name}: `bin` is only valid for install = \"binary\"")
        tree = str(meta.get("tree", "") or "")
        bins = _str_list(meta, "bins", name)
        if install == "bundle":
            if not _is_safe_member(tree):
                raise LibraryError(f"{name}: install = \"bundle\" needs a valid archive `tree` dir")
            if not bins:
                raise LibraryError(f"{name}: install = \"bundle\" needs `bins` (tree-relative executables)")
            for b in bins:
                if not _is_safe_member(b):
                    raise LibraryError(f"{name}: bad bundle bin {b!r}")
            if "unzip" not in build_deps:
                raise LibraryError(f"{name}: install = \"bundle\" (a zip) needs build_deps = [\"unzip\"]")
        elif tree or bins:
            raise LibraryError(f"{name}: `tree`/`bins` are only valid for install = \"bundle\"")
        release = _parse_release(name, meta.get("release"), install)

    env: dict[str, str] = {}
    for key, value in (meta.get("env", {}) or {}).items():
        if not _ENV_KEY_RE.match(str(key)):
            raise LibraryError(f"{name}: invalid env key {key!r}")
        if key in _RESERVED_ENV or config.is_forbidden_env_name(key):
            raise LibraryError(f"{name}: env key {key!r} is reserved (auth/identity/floor plumbing)")
        # Empty is legal: set-but-empty is a real env state some tools key on (AWS_PAGER="" = no pager).
        if not isinstance(value, str) or "\n" in value:
            raise LibraryError(f"{name}: env {key} must be a single-line string")
        env[str(key)] = value

    smoke: list[SmokeSpec] = []
    for i, raw in enumerate(meta.get("smoke", []) or []):
        if not isinstance(raw, dict):
            raise LibraryError(f"{name}: smoke[{i}] must be a table")
        argv = _str_list(raw, "argv", f"{name}: smoke[{i}]")
        sname = str(raw.get("name", "") or "").strip()
        if not sname or not argv:
            raise LibraryError(f"{name}: smoke[{i}] needs a name and a non-empty argv")
        timeout = raw.get("timeout", 15)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise LibraryError(f"{name}: smoke[{i}] timeout must be a positive integer")
        smoke.append(SmokeSpec(name=sname, argv=argv, expect=str(raw.get("expect", "") or ""),
                               timeout=timeout))

    return Tool(name=name, description=description, kind=kind, path=tool_dir, packages=packages,
                version=version, install=install, bin=bin_name, tree=tree, bins=bins, release=release,
                build_deps=build_deps, requires=requires,
                note=str(meta.get("note", "") or "").strip(), allowlist=allowlist, env=env,
                smoke=tuple(smoke))


def _is_safe_member(m: str) -> bool:
    """An archive-relative path that can't escape and is shell-safe (quoted into a generated
    ``RUN``): non-empty, relative, no ``..`` component, no metacharacters, a bin-shaped last part."""
    parts = m.split("/")
    return bool(m) and not m.startswith("/") and ".." not in parts \
        and not any(c in m for c in " '\"\\$`\n") and bool(_BIN_RE.match(parts[-1]))


def _parse_release(name: str, raw, install: str) -> dict[str, ReleaseArch]:
    if not isinstance(raw, dict):
        raise LibraryError(f"{name}: a release tool needs [release.<arch>] tables")
    out: dict[str, ReleaseArch] = {}
    for arch in ARCHES:
        entry = raw.get(arch)
        if not isinstance(entry, dict):
            raise LibraryError(f"{name}: missing [release.{arch}] (both {ARCHES} are required)")
        url = str(entry.get("url", "") or "")
        # https only, and shell-safe: the URL is quoted into a generated RUN line.
        if not url.startswith("https://") or any(c in url for c in " '\"\\$`\n"):
            raise LibraryError(f"{name}: release.{arch}.url must be a plain https URL")
        sha = str(entry.get("sha256", "") or "").lower()
        if not _SHA_RE.match(sha):
            raise LibraryError(f"{name}: release.{arch}.sha256 must be a 64-hex sha256")
        members = _str_list(entry, "members", f"{name}: release.{arch}")
        if install == "tar":
            if not members:
                raise LibraryError(f"{name}: release.{arch} needs `members` for install = \"tar\"")
            for m in members:
                if not _is_safe_member(m):
                    raise LibraryError(f"{name}: release.{arch}: bad tar member {m!r}")
        elif members:
            raise LibraryError(f"{name}: release.{arch}: `members` is only valid for install = \"tar\"")
        extra = set(entry) - {"url", "sha256", "members"}
        if extra:
            raise LibraryError(f"{name}: release.{arch}: unknown keys {sorted(extra)}")
        out[arch] = ReleaseArch(url=url, sha256=sha, members=members)
    return out


def resolve(names: tuple[str, ...] | list[str], lib: dict[str, Tool]) -> tuple[Tool, ...]:
    """The tools a selection bakes: every named tool plus the transitive ``requires`` closure, in
    library (name) order — the same input always yields the same layer. Raises ``LibraryError``
    on an unknown name (a selection entry that has outlived the library — the image must not be
    built with a tool silently missing) or a dependency cycle."""
    missing = sorted(set(names) - set(lib))
    if missing:
        raise LibraryError("unknown tool(s): " + ", ".join(missing)
                           + " (see `claudemanctl tools list`)")
    seen: set[str] = set()
    stack: list[tuple[str, tuple[str, ...]]] = [(n, ()) for n in names]
    while stack:
        name, chain = stack.pop()
        if name in chain:
            raise LibraryError("requires cycle: " + " -> ".join(chain + (name,)))
        if name in seen:
            continue
        seen.add(name)
        for dep in lib[name].requires:
            stack.append((dep, chain + (name,)))
    return tuple(lib[n] for n in sorted(seen))


def merged_env(tools: tuple[Tool, ...]) -> dict[str, str]:
    """The union of the tools' ``[env]`` redirects (sorted by key). Two tools setting the same key to
    different values is a curation error (``LibraryError``) — a silent last-wins would break one
    tool's floor redirect."""
    out: dict[str, str] = {}
    owner: dict[str, str] = {}
    for tool in tools:
        for key, value in tool.env.items():
            if key in out and out[key] != value:
                raise LibraryError(f"env {key} set differently by {owner[key]} and {tool.name}")
            out[key] = value
            owner[key] = tool.name
    return dict(sorted(out.items()))
