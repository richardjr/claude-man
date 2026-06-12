"""Pack materialization — asset-source writes, manifest ours/theirs boundary, fenced-block patch.

Pure stdlib: CLAUDE_MAN_CONFIG_HOME (asset source) + CLAUDE_MAN_STATE_HOME (binds + manifest +
backups) point at tempdirs, and the library root is a tempdir fixture passed via ``root=``."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402
from claudeman.packs import library, materialize  # noqa: E402
from claudeman.registry.schema import Project, Sync  # noqa: E402


def _mk_pack(root: Path, tier: str, name: str, *, default: bool = False,
             fragments: tuple[str, ...] = ("rule.md",), skills: tuple[str, ...] = ()) -> Path:
    """Minimal tmp-library pack (mirrors test_packs_library's builder; kept local — tests/ is a
    package, so a cross-test import would depend on the discovery mode)."""
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


class PatchBlockTest(unittest.TestCase):
    LINES = ["@.claude-man/guardrails/no-autocommit.md", "@.claude-man/quality/code.md"]

    def test_appends_when_absent(self) -> None:
        out = materialize.patch_block("# Mine\n\nbody\n", self.LINES)
        self.assertTrue(out.startswith("# Mine\n\nbody\n\n" + materialize.BLOCK_START))
        self.assertTrue(out.rstrip().endswith(materialize.BLOCK_END))

    def test_idempotent(self) -> None:
        once = materialize.patch_block("# Mine\n", self.LINES)
        self.assertEqual(materialize.patch_block(once, self.LINES), once)

    def test_replaces_existing_block_only(self) -> None:
        text = ("# Mine\n\n" + materialize.BLOCK_START + "\n@old/line.md\n"
                + materialize.BLOCK_END + "\n\n## After\nkeep me\n")
        out = materialize.patch_block(text, self.LINES)
        self.assertNotIn("@old/line.md", out)
        self.assertIn("## After\nkeep me", out)
        self.assertIn(self.LINES[0], out)

    def test_empty_selection_removes_block(self) -> None:
        text = materialize.patch_block("# Mine\nbody\n", self.LINES)
        out = materialize.patch_block(text, [])
        self.assertNotIn("claude-man:packs", out)
        self.assertEqual(out, "# Mine\nbody\n")

    def test_heals_missing_end_marker(self) -> None:
        text = "# Mine\n\n" + materialize.BLOCK_START + "\n@old/a.md\n"  # truncated block
        out = materialize.patch_block(text, self.LINES)
        self.assertNotIn("@old/a.md", out)
        self.assertEqual(out.count(materialize.BLOCK_START), 1)
        self.assertEqual(out.count(materialize.BLOCK_END), 1)

    def test_no_block_no_lines_is_byte_identical(self) -> None:
        self.assertEqual(materialize.patch_block("# Mine\nbody", []), "# Mine\nbody")


class RefreshTest(unittest.TestCase):
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

    def test_fresh_refresh_materializes_and_links(self) -> None:
        rep = materialize.refresh(self._project("guardrails", "review"), root=self.root)
        self.assertTrue(rep.ok)
        self.assertTrue(self._asset("workspace", ".claude-man", "guardrails", "no-autocommit.md").is_file())
        self.assertTrue(self._asset("claude", "skills", "rev", "SKILL.md").is_file())
        claude_md = self._asset("workspace", "CLAUDE.md").read_text()
        self.assertIn("@.claude-man/guardrails/no-autocommit.md", claude_md)
        self.assertIn(materialize.BLOCK_END, claude_md)
        manifest = materialize.load_manifest(self.slug)
        self.assertIn("workspace/.claude-man/guardrails/no-secrets.md", manifest)
        self.assertIn("claude/skills/rev/SKILL.md", manifest)

    def test_second_refresh_is_a_noop(self) -> None:
        materialize.refresh(self._project("guardrails"), root=self.root)
        rep = materialize.refresh(self._project("guardrails"), root=self.root)
        self.assertEqual((rep.refreshed, rep.removed, rep.notes), ((), (), ()))
        self.assertEqual(rep.detail, "")

    def test_library_edit_restamps(self) -> None:
        materialize.refresh(self._project("guardrails"), root=self.root)
        (self.root / "common" / "guardrails" / "claude-md" / "no-secrets.md").write_text("# v2\n")
        rep = materialize.refresh(self._project("guardrails"), root=self.root)
        self.assertEqual(rep.refreshed, ("workspace/.claude-man/guardrails/no-secrets.md",))
        self.assertEqual(self._asset("workspace", ".claude-man", "guardrails", "no-secrets.md").read_text(),
                         "# v2\n")

    def test_drift_is_curated_wins_with_backup(self) -> None:
        materialize.refresh(self._project("guardrails"), root=self.root)
        target = self._asset("workspace", ".claude-man", "guardrails", "no-secrets.md")
        target.write_text("# agent edit\n")
        rep = materialize.refresh(self._project("guardrails"), root=self.root)
        self.assertIn("# no-secrets.md", target.read_text())  # curated content restored
        self.assertTrue(any("drifted" in n for n in rep.notes))
        backups = list(config.backups_dir(self.slug).rglob("no-secrets.md"))
        self.assertTrue(backups and backups[0].read_text() == "# agent edit\n")

    def test_operator_file_wins_collision(self) -> None:
        mine = self._asset("claude", "skills", "rev", "SKILL.md")
        mine.parent.mkdir(parents=True)
        mine.write_text("# operator's own skill\n")
        rep = materialize.refresh(self._project("review"), root=self.root)
        self.assertEqual(mine.read_text(), "# operator's own skill\n")
        self.assertTrue(any("operator file wins" in n for n in rep.notes))
        self.assertNotIn("claude/skills/rev/SKILL.md", materialize.load_manifest(self.slug))

    def test_deselect_removes_from_source_and_bind(self) -> None:
        materialize.refresh(self._project("guardrails"), root=self.root)
        # Simulate sync_in: the fragment also lives in the workspace bind.
        bind = config.workspace_dir(self.slug) / ".claude-man" / "guardrails" / "no-autocommit.md"
        bind.parent.mkdir(parents=True)
        bind.write_text(self._asset("workspace", ".claude-man", "guardrails",
                                    "no-autocommit.md").read_text())
        rep = materialize.refresh(self._project(), root=self.root)
        self.assertEqual(len(rep.removed), 2)
        self.assertFalse(self._asset("workspace", ".claude-man").exists())  # pruned empty dirs
        self.assertFalse(bind.exists())
        self.assertEqual(materialize.load_manifest(self.slug), {})
        self.assertNotIn("claude-man:packs", self._asset("workspace", "CLAUDE.md").read_text())

    def test_unknown_pack_is_a_note(self) -> None:
        rep = materialize.refresh(self._project("ghost"), root=self.root)
        self.assertTrue(rep.ok)
        self.assertTrue(any("unknown pack 'ghost'" in n for n in rep.notes))

    def test_adopts_bind_claude_md_before_patching(self) -> None:
        # Operator authored CLAUDE.md in-container (bind only) — refresh must adopt it, not stub it.
        bind = config.workspace_dir(self.slug) / "CLAUDE.md"
        bind.parent.mkdir(parents=True)
        bind.write_text("# operator memory\n")
        materialize.refresh(self._project("guardrails"), root=self.root)
        asset = self._asset("workspace", "CLAUDE.md").read_text()
        self.assertIn("# operator memory", asset)
        self.assertIn("@.claude-man/guardrails/no-autocommit.md", asset)

    def test_sync_misconfig_is_noted(self) -> None:
        proj = Project(slug=self.slug, packs=("guardrails",),
                       sync=Sync(workspace=("CLAUDE.md",)))  # .claude-man missing
        rep = materialize.refresh(proj, root=self.root)
        self.assertTrue(any(".claude-man" in n and "won't sync" in n for n in rep.notes))

    def test_desired_files_walks_skill_trees(self) -> None:
        deep = self.root / "common" / "review" / "skills" / "rev" / "ref"
        deep.mkdir()
        (deep / "data.md").write_text("x")
        files, notes = materialize.desired_files(("review",), library.discover(self.root))
        self.assertIn("claude/skills/rev/ref/data.md", files)
        self.assertEqual(notes, ())

    def test_adopts_content_equal_unmanifested_file(self) -> None:
        # The manifest self-repair arm: a content-equal file that isn't manifested (manifest
        # lost/corrupt — load_manifest fails soft to {}; or a new host, since the manifest is
        # state-tier and never synced) is adopted as ours, NOT skipped as an operator collision.
        src = self.root / "common" / "guardrails" / "claude-md" / "no-secrets.md"
        dst = self._asset("workspace", ".claude-man", "guardrails", "no-secrets.md")
        dst.parent.mkdir(parents=True)
        dst.write_text(src.read_text())
        rep = materialize.refresh(self._project("guardrails"), root=self.root)
        self.assertTrue(rep.ok)
        self.assertFalse(any("operator file wins" in n for n in rep.notes))
        self.assertIn("workspace/.claude-man/guardrails/no-secrets.md",
                      materialize.load_manifest(self.slug))

    def test_block_lines_in_selection_order(self) -> None:
        # The @-import block follows SELECTION order (this is why the TUI toggle appends on
        # re-add), with fragments sorted within a pack and unknown names contributing nothing.
        _mk_pack(self.root, "common", "zeta")
        lib = library.discover(self.root)
        lines = materialize.block_lines(("zeta", "guardrails", "ghost"), lib)
        self.assertEqual(lines, ["@.claude-man/zeta/rule.md",
                                 "@.claude-man/guardrails/no-autocommit.md",
                                 "@.claude-man/guardrails/no-secrets.md"])


if __name__ == "__main__":
    unittest.main()
