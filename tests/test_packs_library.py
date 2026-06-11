"""Pack-library discovery — parsing, defaults resolution, the curation lint (pure, tmp dirs)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.packs import library  # noqa: E402


def _mk_pack(root: Path, tier: str, name: str, *, description: str = "a pack",
             default: bool = False, fragments: tuple[str, ...] = ("rule.md",),
             skills: tuple[str, ...] = (), meta: str | None = None) -> Path:
    pack = root / tier / name
    pack.mkdir(parents=True)
    if meta is None:
        meta = f'description = "{description}"\ndefault = {"true" if default else "false"}\n'
    (pack / library.PACK_META).write_text(meta)
    for frag in fragments:
        (pack / library.CLAUDE_MD_DIR).mkdir(exist_ok=True)
        (pack / library.CLAUDE_MD_DIR / frag).write_text(f"# {frag}\n")
    for skill in skills:
        d = pack / library.SKILLS_DIR / skill
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {skill}\n")
    return pack


class DiscoverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_root_is_empty(self) -> None:
        self.assertEqual(library.discover(self.root / "nope"), {})

    def test_parses_packs_across_tiers(self) -> None:
        _mk_pack(self.root, "common", "guardrails", default=True, fragments=("a.md", "b.md"))
        _mk_pack(self.root, "node", "node-conventions", default=True, skills=("rev",),
                 fragments=())
        lib = library.discover(self.root)
        self.assertEqual(set(lib), {"guardrails", "node-conventions"})
        g = lib["guardrails"]
        self.assertEqual((g.tier, g.default, g.fragments), ("common", True, ("a.md", "b.md")))
        n = lib["node-conventions"]
        self.assertEqual((n.tier, n.skills, n.fragments), ("node", ("rev",), ()))
        self.assertEqual(g.summary, "2 md")
        self.assertEqual(n.summary, "1 skill")

    def test_duplicate_name_across_tiers_raises(self) -> None:
        _mk_pack(self.root, "common", "guardrails")
        _mk_pack(self.root, "node", "guardrails")
        with self.assertRaisesRegex(library.LibraryError, "duplicate"):
            library.discover(self.root)

    def test_missing_description_raises(self) -> None:
        _mk_pack(self.root, "common", "bad", meta="default = true\n")
        with self.assertRaisesRegex(library.LibraryError, "description"):
            library.discover(self.root)

    def test_missing_meta_raises(self) -> None:
        pack = self.root / "common" / "bare"
        (pack / library.CLAUDE_MD_DIR).mkdir(parents=True)
        (pack / library.CLAUDE_MD_DIR / "x.md").write_text("x")
        with self.assertRaisesRegex(library.LibraryError, "missing"):
            library.discover(self.root)

    def test_empty_pack_raises(self) -> None:
        pack = self.root / "common" / "hollow"
        pack.mkdir(parents=True)
        (pack / library.PACK_META).write_text('description = "nothing inside"\n')
        with self.assertRaisesRegex(library.LibraryError, "empty"):
            library.discover(self.root)

    def test_bad_names_raise(self) -> None:
        _mk_pack(self.root, "common", "Bad_Name")
        with self.assertRaisesRegex(library.LibraryError, "invalid pack name"):
            library.discover(self.root)

    def test_bad_skill_name_raises(self) -> None:
        _mk_pack(self.root, "common", "p", fragments=(), skills=("Bad Skill",))
        with self.assertRaisesRegex(library.LibraryError, "invalid skill name"):
            library.discover(self.root)


class DefaultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _mk_pack(self.root, "common", "guardrails", default=True)
        _mk_pack(self.root, "common", "workflow", default=False)
        _mk_pack(self.root, "node", "node-conventions", default=True)
        _mk_pack(self.root, "python", "python-uv", default=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_common_only_without_language(self) -> None:
        self.assertEqual(library.defaults_for("", self.root), ("guardrails",))

    def test_language_tier_adds_its_defaults(self) -> None:
        self.assertEqual(library.defaults_for("node", self.root),
                         ("guardrails", "node-conventions"))

    def test_unknown_language_is_common_only(self) -> None:
        self.assertEqual(library.defaults_for("haskell", self.root), ("guardrails",))

    def test_tiers_lists_common_first(self) -> None:
        self.assertEqual(library.tiers(self.root), ("common", "node", "python"))


class ShippedLibraryLintTest(unittest.TestCase):
    """The curated library in THIS repo must always parse — discover() doubles as the lint."""

    def test_shipped_library_is_well_formed(self) -> None:
        lib = library.discover()  # raises LibraryError on any curation mistake
        self.assertIn("guardrails", lib)
        for pack in lib.values():
            for frag in pack.fragments:  # every fragment file is real and non-empty
                body = (pack.path / library.CLAUDE_MD_DIR / frag).read_text(encoding="utf-8")
                self.assertTrue(body.strip(), f"{pack.tier}/{pack.name}/{frag} is empty")

    def test_shipped_defaults_resolve(self) -> None:
        self.assertIn("guardrails", library.defaults_for(""))
        for lang in ("node", "python", "rust"):
            defaults = library.defaults_for(lang)
            self.assertTrue(any(library.discover()[n].tier == lang for n in defaults),
                            f"language {lang!r} has no default pack")


if __name__ == "__main__":
    unittest.main()
