"""Packs-screen view model — the pack_states freshness map, grouping, selection marks, drift
threading, toggle semantics.

Pure (no textual import — ``tui/packsview.py`` follows the splash/rowfx no-textual pattern, so
the suite stays dependency-free). The ``pack_states`` tests point CLAUDE_MAN_CONFIG_HOME /
CLAUDE_MAN_STATE_HOME at tempdirs and pass a tempdir library via ``root=``, mirroring
test_packs_materialize."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402
from claudeman.packs import library, materialize  # noqa: E402
from claudeman.registry.schema import Project  # noqa: E402
from claudeman.tui import packsview  # noqa: E402


def _pack(name: str, tier: str, *, default: bool = False,
          fragments: tuple[str, ...] = ("rule.md",), skills: tuple[str, ...] = ()) -> library.Pack:
    return library.Pack(name=name, tier=tier, description=f"{name} pack", default=default,
                        path=Path("/nonexistent") / tier / name,
                        fragments=fragments, skills=skills)


def _lib(*packs: library.Pack) -> dict[str, library.Pack]:
    return {p.name: p for p in packs}


def _mk_pack(root: Path, tier: str, name: str, *, default: bool = False,
             fragments: tuple[str, ...] = ("rule.md",), skills: tuple[str, ...] = ()) -> Path:
    """Minimal tmp-library pack (mirrors test_packs_materialize's builder; kept local — tests/
    is a package, so a cross-test import would depend on the discovery mode)."""
    pack = root / tier / name
    pack.mkdir(parents=True)
    (pack / library.PACK_META).write_text(
        f'description = "a pack"\ndefault = {"true" if default else "false"}\n')
    for frag in fragments:
        (pack / library.CLAUDE_MD_DIR).mkdir(exist_ok=True)
        (pack / library.CLAUDE_MD_DIR / frag).write_text(f"# {frag}\n")
    for skill in skills:
        d = pack / library.SKILLS_DIR / skill
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {skill}\n")
    return pack


class PackStatesTest(unittest.TestCase):
    """``pack_states`` — the read-only freshness map behind the State column."""

    def setUp(self) -> None:
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.libdir = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.cfg.name
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name
        self.root = Path(self.libdir.name)
        _mk_pack(self.root, "common", "guardrails", default=True,
                 fragments=("no-autocommit.md", "no-secrets.md"))
        _mk_pack(self.root, "common", "review", fragments=(), skills=("rev",))
        self.slug = "demo"

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        for tmp in (self.cfg, self.state, self.libdir):
            tmp.cleanup()

    def _project(self, *packs: str) -> Project:
        return Project(slug=self.slug, packs=tuple(packs))

    def _asset(self, *p: str) -> Path:
        return config.project_assets_dir(self.slug).joinpath(*p)

    def test_unmaterialized_is_stale(self) -> None:
        states = packsview.pack_states(self._project("guardrails"), root=self.root)
        self.assertEqual(states, {"guardrails": packsview.STATE_STALE})

    def test_in_sync_after_refresh(self) -> None:
        materialize.refresh(self._project("guardrails", "review"), root=self.root)
        states = packsview.pack_states(self._project("guardrails", "review"), root=self.root)
        self.assertEqual(states, {"guardrails": packsview.STATE_OK,
                                  "review": packsview.STATE_OK})

    def test_library_edit_is_stale(self) -> None:
        materialize.refresh(self._project("guardrails"), root=self.root)
        (self.root / "common" / "guardrails" / "claude-md" / "no-secrets.md").write_text("# v2\n")
        states = packsview.pack_states(self._project("guardrails"), root=self.root)
        self.assertEqual(states["guardrails"], packsview.STATE_STALE)

    def test_managed_copy_edit_is_drifted(self) -> None:
        materialize.refresh(self._project("guardrails"), root=self.root)
        self._asset("workspace", ".claude-man", "guardrails", "no-secrets.md").write_text("# edit\n")
        states = packsview.pack_states(self._project("guardrails"), root=self.root)
        self.assertEqual(states["guardrails"], packsview.STATE_DRIFTED)

    def test_unmanifested_collision_is_operator(self) -> None:
        mine = self._asset("claude", "skills", "rev", "SKILL.md")
        mine.parent.mkdir(parents=True)
        mine.write_text("# operator's own skill\n")
        states = packsview.pack_states(self._project("review"), root=self.root)
        self.assertEqual(states["review"], packsview.STATE_OPERATOR)

    def test_selection_outliving_the_library_is_unknown(self) -> None:
        states = packsview.pack_states(self._project("ghost"), root=self.root)
        self.assertEqual(states, {"ghost": packsview.STATE_UNKNOWN})

    def test_drift_outranks_stale_within_a_pack(self) -> None:
        materialize.refresh(self._project("guardrails"), root=self.root)
        # One file edited locally (drift), the other moved on upstream (stale) — drift wins.
        self._asset("workspace", ".claude-man", "guardrails", "no-autocommit.md").write_text("# edit\n")
        (self.root / "common" / "guardrails" / "claude-md" / "no-secrets.md").write_text("# v2\n")
        states = packsview.pack_states(self._project("guardrails"), root=self.root)
        self.assertEqual(states["guardrails"], packsview.STATE_DRIFTED)

    def test_unselected_packs_are_absent(self) -> None:
        states = packsview.pack_states(self._project("guardrails"), root=self.root)
        self.assertNotIn("review", states)

    def test_content_equal_unmanifested_is_ok_not_operator(self) -> None:
        # Content-equality is checked BEFORE manifest membership — the manifest-loss/new-host
        # case (state tier is never synced) must read in-sync, not "operator file wins".
        src = self.root / "common" / "guardrails" / "claude-md" / "no-secrets.md"
        dst = self._asset("workspace", ".claude-man", "guardrails", "no-secrets.md")
        dst.parent.mkdir(parents=True)
        dst.write_text(src.read_text())
        states = packsview.pack_states(self._project("guardrails"), root=self.root)
        self.assertNotEqual(states["guardrails"], packsview.STATE_OPERATOR)

    def test_adopted_file_edit_is_drifted_not_operator(self) -> None:
        # Once refresh adopts a content-equal file into the manifest, the next edit crosses the
        # ours/theirs boundary as DRIFTED (curated-wins) — not operator-protected.
        src = self.root / "common" / "guardrails" / "claude-md" / "no-secrets.md"
        dst = self._asset("workspace", ".claude-man", "guardrails", "no-secrets.md")
        dst.parent.mkdir(parents=True)
        dst.write_text(src.read_text())
        materialize.refresh(self._project("guardrails"), root=self.root)  # adopts
        dst.write_text("# operator edit after adoption\n")
        states = packsview.pack_states(self._project("guardrails"), root=self.root)
        self.assertEqual(states["guardrails"], packsview.STATE_DRIFTED)

    def _patched_hash(self, unreadable: Path):
        """library.file_hash that raises OSError for paths under ``unreadable``."""
        orig = library.file_hash

        def fake(path: Path) -> str:
            if unreadable in path.parents or path == unreadable:
                raise OSError(13, "Permission denied", str(path))
            return orig(path)

        return mock.patch.object(library, "file_hash", fake)

    def test_unreadable_library_source_is_stale(self) -> None:
        with self._patched_hash(self.root):
            states = packsview.pack_states(self._project("guardrails"), root=self.root)
        self.assertEqual(states["guardrails"], packsview.STATE_STALE)

    def test_unreadable_manifested_asset_is_drifted_not_a_raise(self) -> None:
        # The read-only probe must never be more fragile than refresh — an unreadable ASSET
        # copy degrades to a state instead of crashing the screen's on_mount.
        materialize.refresh(self._project("guardrails"), root=self.root)
        with self._patched_hash(config.project_assets_dir(self.slug)):
            states = packsview.pack_states(self._project("guardrails"), root=self.root)
        self.assertEqual(states["guardrails"], packsview.STATE_DRIFTED)

    def test_unreadable_foreign_asset_is_operator(self) -> None:
        dst = self._asset("workspace", ".claude-man", "guardrails", "no-secrets.md")
        dst.parent.mkdir(parents=True)
        dst.write_text("# unreadable operator file\n")  # exists, never manifested
        with self._patched_hash(config.project_assets_dir(self.slug)):
            states = packsview.pack_states(self._project("guardrails"), root=self.root)
        self.assertEqual(states["guardrails"], packsview.STATE_OPERATOR)


class RowsTest(unittest.TestCase):
    LIB = _lib(
        _pack("guardrails", "common", default=True),
        _pack("workflow", "common"),
        _pack("node-conventions", "node", default=True),
        _pack("python-uv", "python", default=True),
    )

    def test_grouped_common_then_language(self) -> None:
        rows = packsview.rows(self.LIB, ("guardrails",), "node", {})
        headers = [r.title for r in rows if r.is_header]
        self.assertEqual(headers, ["Common", "node"])
        names = [r.name for r in rows if not r.is_header]
        self.assertEqual(names, ["guardrails", "workflow", "node-conventions"])
        self.assertNotIn("python-uv", names)  # other tiers stay hidden unless selected

    def test_no_language_is_common_only(self) -> None:
        rows = packsview.rows(self.LIB, (), "", {})
        self.assertEqual([r.title for r in rows if r.is_header], ["Common"])

    def test_common_language_degrades_to_common_only(self) -> None:
        # language="common" is storable (the registry shape-validates only; the CLI accepts it).
        # A duplicate Common section would repeat pack names — the screen's DataTable row keys —
        # and crash PacksScreen with DuplicateKey on open.
        rows = packsview.rows(self.LIB, ("guardrails",), "common", {})
        self.assertEqual([r.title for r in rows if r.is_header], ["Common"])
        names = [r.name for r in rows if not r.is_header]
        self.assertEqual(names, ["guardrails", "workflow"])

    def test_row_keys_are_always_unique(self) -> None:
        # Row.key becomes the DataTable row key — a duplicate is a hard crash, so pin uniqueness
        # across the degenerate inputs too.
        for language in ("", "common", "node", "python", "absent-tier"):
            for selection in ((), ("guardrails",), ("python-uv", "ghost"), ("guardrails", "python-uv")):
                keys = [r.key for r in packsview.rows(self.LIB, selection, language, {})]
                self.assertEqual(len(keys), len(set(keys)), f"dup keys: {language=} {selection=}")

    def test_selection_marks_and_states_thread_through(self) -> None:
        rows = packsview.rows(self.LIB, ("workflow",), "",
                              {"workflow": packsview.STATE_DRIFTED})
        by_name = {r.name: r for r in rows if not r.is_header}
        self.assertTrue(by_name["workflow"].selected)
        self.assertFalse(by_name["guardrails"].selected)
        self.assertEqual(by_name["workflow"].state, packsview.STATE_DRIFTED)
        self.assertEqual(by_name["guardrails"].state, "")

    def test_default_packs_are_marked(self) -> None:
        rows = packsview.rows(self.LIB, (), "node", {})
        by_name = {r.name: r for r in rows if not r.is_header}
        self.assertTrue(by_name["guardrails"].description.startswith("(default) "))
        self.assertFalse(by_name["workflow"].description.startswith("(default) "))

    def test_cross_tier_selection_lands_in_other(self) -> None:
        rows = packsview.rows(self.LIB, ("python-uv",), "node", {})
        self.assertIn("Other (selected)", [r.title for r in rows if r.is_header])
        other = next(r for r in rows if r.name == "python-uv")
        self.assertTrue(other.selected)
        self.assertIn("[python]", other.description)

    def test_unknown_selection_is_visible_and_flagged(self) -> None:
        rows = packsview.rows(self.LIB, ("ghost",), "", {"ghost": packsview.STATE_UNKNOWN})
        ghost = next(r for r in rows if r.name == "ghost")
        self.assertTrue(ghost.selected)
        self.assertEqual(ghost.state, packsview.STATE_UNKNOWN)
        self.assertIn("not in the library", ghost.description)

    def test_unknown_selection_defaults_to_unknown_state(self) -> None:
        rows = packsview.rows(self.LIB, ("ghost",), "", {})  # states fail-soft empty
        ghost = next(r for r in rows if r.name == "ghost")
        self.assertEqual(ghost.state, packsview.STATE_UNKNOWN)

    def test_empty_library_renders_selection_only(self) -> None:
        rows = packsview.rows({}, ("guardrails",), "node", {})
        self.assertEqual([r.title for r in rows if r.is_header], ["Other (selected)"])
        self.assertEqual([r.name for r in rows if not r.is_header], ["guardrails"])

    def test_empty_library_empty_selection_is_empty(self) -> None:
        self.assertEqual(packsview.rows({}, (), "", {}), [])

    def test_header_keys_are_marked(self) -> None:
        rows = packsview.rows(self.LIB, (), "node", {})
        for row in rows:
            self.assertEqual(row.is_header, row.key.startswith(packsview.HEADER_KEY_PREFIX))


class ToggledTest(unittest.TestCase):
    def test_toggle_on_appends(self) -> None:
        self.assertEqual(packsview.toggled(("a", "b"), "c"), ("a", "b", "c"))

    def test_toggle_off_preserves_order(self) -> None:
        self.assertEqual(packsview.toggled(("a", "b", "c"), "b"), ("a", "c"))

    def test_roundtrip_appends_to_the_end(self) -> None:
        sel = packsview.toggled(packsview.toggled(("a", "b"), "a"), "a")
        self.assertEqual(sel, ("b", "a"))


if __name__ == "__main__":
    unittest.main()
