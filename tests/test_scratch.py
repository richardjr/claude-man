"""Scratch / data-transfer dir: the shared managed-block patcher, the wipe, and the CLAUDE.md note.

Pure stdlib: CLAUDE_MAN_STATE_HOME (the workspace bind) + CLAUDE_MAN_CONFIG_HOME (the registry)
point at tempdirs. No docker/textual."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import claudemd, config, scratch  # noqa: E402
from claudeman.packs import materialize  # noqa: E402
from claudeman.registry import projects  # noqa: E402
from claudeman.registry.schema import Project, ValidationError  # noqa: E402

# Distinct marker pair for the generic patcher tests (independent of the packs/scratch ones).
_A_BEGIN = "<!-- test:a -->"
_A_END = "<!-- /test:a -->"
_B_BEGIN = "<!-- test:b -->"
_B_END = "<!-- /test:b -->"


def _patch_a(text, lines):
    return claudemd.patch_block(text, lines, begin=_A_BEGIN, end=_A_END)


def _patch_b(text, lines):
    return claudemd.patch_block(text, lines, begin=_B_BEGIN, end=_B_END)


class PatchBlockTest(unittest.TestCase):
    def test_appends_when_absent(self) -> None:
        out = _patch_a("# Mine\n\nbody\n", ["x"])
        self.assertTrue(out.startswith("# Mine\n\nbody\n\n" + _A_BEGIN))
        self.assertTrue(out.rstrip().endswith(_A_END))

    def test_idempotent(self) -> None:
        once = _patch_a("# Mine\n", ["x", "y"])
        self.assertEqual(_patch_a(once, ["x", "y"]), once)

    def test_no_block_no_lines_byte_identical(self) -> None:
        self.assertEqual(_patch_a("# Mine\nbody", []), "# Mine\nbody")

    def test_replaces_in_place_preserving_position(self) -> None:
        text = f"# Top\n\n{_A_BEGIN}\nold\n{_A_END}\n\n## After\nkeep me\n"
        out = _patch_a(text, ["new"])
        self.assertNotIn("old", out)
        self.assertIn("new", out)
        # Position-preserving: the trailing operator section stays BELOW the block, not above it.
        self.assertLess(out.index(_A_BEGIN), out.index("## After"))

    def test_removal_collapses_seam(self) -> None:
        text = _patch_a("# Mine\nbody\n", ["x"])
        self.assertEqual(_patch_a(text, []), "# Mine\nbody\n")

    def test_heals_missing_end_marker(self) -> None:
        text = f"# Mine\n\n{_A_BEGIN}\nstale\n"  # no end marker
        out = _patch_a(text, ["x"])
        self.assertNotIn("stale", out)
        self.assertEqual(out.count(_A_BEGIN), 1)
        self.assertEqual(out.count(_A_END), 1)

    def test_distinct_prefixes_do_not_collide(self) -> None:
        # Two managed blocks with different prefixes coexist; patching one never touches the other,
        # and re-patching is a no-op (the stability that keeps asset/bind sync churn-free).
        t = _patch_a("# Mine\n\nbody\n", ["a-content"])
        t = _patch_b(t, ["b-content"])
        self.assertEqual(_patch_a(t, ["a-content"]), t)   # re-patch A: no churn
        self.assertEqual(_patch_b(t, ["b-content"]), t)   # re-patch B: no churn
        self.assertIn("a-content", t)
        self.assertIn("b-content", t)
        self.assertIn("body", t)


class PacksAndScratchCoexistTest(unittest.TestCase):
    """End-to-end: the real packs block and the real scratch block live in one CLAUDE.md without
    fighting (the bug the in-place patcher fixes — two bottom-appending blocks oscillated)."""

    PACKS = ["@.claude-man/guardrails/no-secrets.md"]

    def _patch_scratch(self, text):
        return claudemd.patch_block(text, scratch._NOTE_LINES, begin=scratch._BEGIN,
                                    end=scratch._END, begin_prefix=scratch._BEGIN_PREFIX,
                                    end_prefix=scratch._END_PREFIX)

    def test_stable_across_repeated_patches(self) -> None:
        t = materialize.patch_block("# Mine\n\nbody\n", self.PACKS)
        t = self._patch_scratch(t)
        self.assertEqual(materialize.patch_block(t, self.PACKS), t)  # packs re-patch: no churn
        self.assertEqual(self._patch_scratch(t), t)                  # scratch re-patch: no churn
        self.assertIn(self.PACKS[0], t)
        self.assertIn(scratch._BEGIN, t)
        self.assertIn("body", t)


class ConfigPathTest(unittest.TestCase):
    def test_scratch_under_workspace(self) -> None:
        self.assertTrue(config.CONTAINER_SCRATCH.startswith(config.CONTAINER_WORKSPACE + "/"))
        self.assertEqual(config.CONTAINER_SCRATCH, "/workspace/scratch")

    def test_scratch_dir_under_workspace_dir(self) -> None:
        ws = config.workspace_dir("demo")
        self.assertEqual(config.scratch_dir("demo").parent, ws)


class _StateEnv(unittest.TestCase):
    def setUp(self) -> None:
        self.state = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.cfg.name
        self.slug = "demo"

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        self.state.cleanup()
        self.cfg.cleanup()


class ClearTest(_StateEnv):
    def test_creates_dir_when_absent(self) -> None:
        note = scratch.clear(self.slug)
        self.assertEqual(note, "")
        d = config.scratch_dir(self.slug)
        self.assertTrue(d.is_dir())
        self.assertEqual(list(d.iterdir()), [])

    def test_wipes_contents_but_leaves_sibling_repo(self) -> None:
        ws = config.workspace_dir(self.slug)
        repo = ws / "myrepo"
        repo.mkdir(parents=True)
        (repo / "keep.txt").write_text("important")
        d = config.scratch_dir(self.slug)
        (d / "sub").mkdir(parents=True)
        (d / "sub" / "junk.bin").write_text("temp")
        (d / "top.txt").write_text("temp")

        note = scratch.clear(self.slug)

        self.assertEqual(note, "")
        self.assertTrue(d.is_dir())
        self.assertEqual(list(d.iterdir()), [])            # scratch emptied
        self.assertTrue((repo / "keep.txt").is_file())     # sibling repo untouched
        self.assertEqual((repo / "keep.txt").read_text(), "important")


class EnsureNoteTest(_StateEnv):
    def _claude_md(self) -> Path:
        return config.workspace_dir(self.slug) / "CLAUDE.md"

    def test_creates_claude_md_with_block(self) -> None:
        note = scratch.ensure_note(Project(slug=self.slug))
        self.assertEqual(note, "")
        text = self._claude_md().read_text()
        self.assertIn(scratch._BEGIN, text)
        self.assertIn(scratch._END, text)
        self.assertIn(config.CONTAINER_SCRATCH, text)

    def test_preserves_operator_content(self) -> None:
        self._claude_md().parent.mkdir(parents=True, exist_ok=True)
        self._claude_md().write_text("# My Project\n\nHouse rule: be kind.\n")
        scratch.ensure_note(Project(slug=self.slug))
        text = self._claude_md().read_text()
        self.assertIn("House rule: be kind.", text)
        self.assertIn(scratch._BEGIN, text)

    def test_idempotent_no_rewrite(self) -> None:
        proj = Project(slug=self.slug)
        scratch.ensure_note(proj)
        first = self._claude_md().read_text()
        note = scratch.ensure_note(proj)          # second pass
        self.assertEqual(note, "")
        self.assertEqual(self._claude_md().read_text(), first)  # byte-identical, no churn


class AddRepoGuardTest(_StateEnv):
    def setUp(self) -> None:
        super().setUp()
        projects.save(Project(slug=self.slug))

    def test_rejects_repo_dir_equal_to_scratch(self) -> None:
        with self.assertRaises(ValidationError):
            projects.add_repo(self.slug, "https://github.com/foo/scratch.git")

    def test_rejects_repo_dir_under_scratch(self) -> None:
        with self.assertRaises(ValidationError):
            projects.add_repo(self.slug, "https://example.com/x.git", dir="scratch/nested")

    def test_allows_normal_repo(self) -> None:
        updated = projects.add_repo(self.slug, "https://github.com/foo/bar.git")
        self.assertEqual(updated.repos[-1].resolved_dir(), "bar")

    def test_allows_explicit_dir_workaround(self) -> None:
        updated = projects.add_repo(self.slug, "https://github.com/foo/scratch.git",
                                    dir="scratch-repo")
        self.assertEqual(updated.repos[-1].resolved_dir(), "scratch-repo")


if __name__ == "__main__":
    unittest.main()
