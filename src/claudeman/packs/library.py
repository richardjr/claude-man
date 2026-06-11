"""The curated pack library — discovery + parsing of ``library/packs/<tier>/<pack>/``.

PURE read side (stdlib only, no writes): a **pack** is a bundle directory carrying any mix of
CLAUDE.md fragments (``claude-md/*.md``) and skills (``skills/<name>/``) that travel together.
Tiers are the first-level directories — ``common/`` plus per-language dirs (``node/``,
``python/``, …) discovered from the layout, never a hardcoded list. Pack names must be unique
across tiers (``discover`` raises, and a shipped-library lint test pins it), so a project's
selection stays a flat list and tier is purely a curation/defaults concept. See docs/PACKS.md.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .. import config

COMMON_TIER = "common"
PACK_META = "pack.toml"
CLAUDE_MD_DIR = "claude-md"
SKILLS_DIR = "skills"

# Same shape as project slugs — pack/tier/skill names become directory names in the container.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class LibraryError(ValueError):
    """A malformed library — bad name, missing metadata, duplicate pack. The shipped library
    must never raise this (a lint test imports the real tree); a hand-edited one fails loud."""


@dataclass(frozen=True)
class Pack:
    name: str                    # directory name — unique across tiers
    tier: str                    # COMMON_TIER or a language name
    description: str
    default: bool                # selected by default for projects in this tier's scope
    path: Path                   # the pack directory
    fragments: tuple[str, ...]   # claude-md/<file>.md basenames (sorted)
    skills: tuple[str, ...]      # skills/<name>/ dir names (sorted)

    @property
    def summary(self) -> str:
        """Compact contents cell: ``"4 md"`` / ``"1 skill"`` / ``"2 md + 1 skill"``."""
        parts = []
        if self.fragments:
            parts.append(f"{len(self.fragments)} md")
        if self.skills:
            parts.append(f"{len(self.skills)} skill" + ("s" if len(self.skills) > 1 else ""))
        return " + ".join(parts)


def library_root() -> Path:
    return config.library_packs_dir()


def discover(root: Path | None = None) -> dict[str, Pack]:
    """``name -> Pack`` for the whole library (insertion order: tier, then pack name).

    Raises ``LibraryError`` on a duplicate pack name across tiers, a bad/missing ``pack.toml``,
    an invalid name, or an empty pack — this doubles as the curation lint. A missing library
    root returns ``{}`` (packs are optional; nothing else degrades)."""
    root = library_root() if root is None else root
    out: dict[str, Pack] = {}
    if not root.is_dir():
        return out
    for tier_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if not _NAME_RE.match(tier_dir.name):
            raise LibraryError(f"invalid tier name {tier_dir.name!r} (must match {_NAME_RE.pattern})")
        for pack_dir in sorted(p for p in tier_dir.iterdir() if p.is_dir()):
            pack = _load_pack(pack_dir, tier_dir.name)
            if pack.name in out:
                raise LibraryError(
                    f"duplicate pack name {pack.name!r} (in tiers "
                    f"{out[pack.name].tier!r} and {pack.tier!r}) — names must be library-unique"
                )
            out[pack.name] = pack
    return out


def _load_pack(pack_dir: Path, tier: str) -> Pack:
    name = pack_dir.name
    label = f"{tier}/{name}"
    if not _NAME_RE.match(name):
        raise LibraryError(f"invalid pack name {name!r} (must match {_NAME_RE.pattern})")
    meta_path = pack_dir / PACK_META
    if not meta_path.is_file():
        raise LibraryError(f"{label}: missing {PACK_META}")
    try:
        meta = tomllib.loads(meta_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise LibraryError(f"{label}: unreadable {PACK_META}: {exc}") from None
    description = str(meta.get("description", "") or "").strip()
    if not description:
        raise LibraryError(f"{label}: {PACK_META} must set a description")

    md_dir = pack_dir / CLAUDE_MD_DIR
    fragments = tuple(sorted(p.name for p in md_dir.glob("*.md") if p.is_file())) \
        if md_dir.is_dir() else ()
    sk_dir = pack_dir / SKILLS_DIR
    skills = tuple(sorted(p.name for p in sk_dir.iterdir() if p.is_dir())) \
        if sk_dir.is_dir() else ()
    for skill in skills:  # skill dirs land verbatim under ~/.claude/skills/<name>
        if not _NAME_RE.match(skill):
            raise LibraryError(f"{label}: invalid skill name {skill!r}")
    if not fragments and not skills:
        raise LibraryError(f"{label}: empty pack (no {CLAUDE_MD_DIR}/*.md and no {SKILLS_DIR}/)")
    return Pack(name=name, tier=tier, description=description,
                default=bool(meta.get("default", False)), path=pack_dir,
                fragments=fragments, skills=skills)


def tiers(root: Path | None = None) -> tuple[str, ...]:
    """All tier names present in the library, ``common`` first then languages sorted."""
    seen = {p.tier for p in discover(root).values()}
    langs = sorted(seen - {COMMON_TIER})
    return ((COMMON_TIER,) if COMMON_TIER in seen else ()) + tuple(langs)


def defaults_for(language: str, root: Path | None = None) -> tuple[str, ...]:
    """Pack names selected by default for a project: every ``default = true`` pack in ``common/``
    plus the ``<language>/`` tier (empty language -> common only). Resolution happens at project
    CREATE and is written explicitly into the registry — never re-resolved implicitly."""
    want = {COMMON_TIER} | ({language} if language else set())
    return tuple(p.name for p in discover(root).values() if p.default and p.tier in want)


def file_hash(path: Path) -> str:
    """sha256 hex of one file's bytes — the manifest's freshness identity (no version bumps)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
