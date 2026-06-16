"""Image build/exists helpers: the pure argv renderer, the base→overlay chain, and the
auto-build pre-flight's docker-missing handling (no daemon needed)."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import cli, config  # noqa: E402
from claudeman.docker import images  # noqa: E402


class BuildChainTest(unittest.TestCase):
    def test_base_needs_only_itself(self) -> None:
        self.assertEqual(images.build_chain("base"), ["base"])

    def test_overlay_needs_base_first(self) -> None:
        # FROM claude-man:base — base must precede the overlay in the chain.
        self.assertEqual(images.build_chain("node"), ["base", "node"])
        self.assertEqual(images.build_chain("python"), ["base", "python"])

    def test_combo_overlay_chain(self) -> None:
        # The python-node combo is an ordinary FROM-base overlay — base-first, no chaining.
        self.assertEqual(images.build_chain("python-node"), ["base", "python-node"])


class BuildArgvTest(unittest.TestCase):
    def test_base_argv(self) -> None:
        argv = images.build_argv("base", claude_version="2.1.159")
        self.assertEqual(argv[:2], ["docker", "build"])
        self.assertIn("-t", argv)
        self.assertIn("claude-man:base", argv)
        self.assertIn("--build-arg", argv)
        self.assertIn("CLAUDE_VERSION=2.1.159", argv)
        # -f points at the real base Dockerfile, by absolute package-relative path.
        dfile = argv[argv.index("-f") + 1]
        self.assertTrue(dfile.endswith("images/base/Dockerfile"))
        self.assertTrue(Path(dfile).is_absolute())

    def test_overlay_argv_targets_overlay_dockerfile(self) -> None:
        argv = images.build_argv("node")
        self.assertIn("claude-man:node", argv)
        dfile = argv[argv.index("-f") + 1]
        self.assertTrue(dfile.endswith("images/overlays/node.Dockerfile"))

    def test_combo_overlay_argv_targets_combo_dockerfile(self) -> None:
        argv = images.build_argv("python-node")
        self.assertIn("claude-man:python-node", argv)
        dfile = argv[argv.index("-f") + 1]
        self.assertTrue(dfile.endswith("images/overlays/python-node.Dockerfile"))
        self.assertTrue(config.image_dockerfile("python-node").is_file())

    def test_default_claude_version(self) -> None:
        argv = images.build_argv("base")
        self.assertIn(f"CLAUDE_VERSION={config.DEFAULT_CLAUDE_VERSION}", argv)


class ConfigPathsTest(unittest.TestCase):
    def test_repo_root_holds_images_and_src(self) -> None:
        root = config.repo_root()
        self.assertTrue((root / "images").is_dir())
        self.assertTrue((root / "src" / "claudeman").is_dir())

    def test_dockerfiles_resolve_to_real_files(self) -> None:
        # Guards the parents[2] resolution against a layout drift.
        self.assertTrue(config.image_dockerfile("base").is_file())
        self.assertTrue(config.image_dockerfile("node").is_file())

    def test_image_tag(self) -> None:
        self.assertEqual(config.image_tag("node"), "claude-man:node")


class DryRunTest(unittest.TestCase):
    def test_dry_run_emits_argv_and_skips_docker(self) -> None:
        lines: list[str] = []
        # dry_run returns before any docker invocation, so this is daemon-free.
        rc = images.build_one("base", dry_run=True, on_line=lines.append)
        self.assertEqual(rc, 0)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("+ docker build"))
        self.assertIn("claude-man:base", lines[0])


class DockerMissingTest(unittest.TestCase):
    def test_image_exists_false_without_docker(self) -> None:
        with mock.patch.object(images.shutil, "which", return_value=None):
            self.assertFalse(images.image_exists("base"))

    def test_ensure_chain_fails_clearly_without_docker(self) -> None:
        with mock.patch.object(images.shutil, "which", return_value=None):
            res = images.ensure_chain("node")
        self.assertFalse(res.ok)
        self.assertEqual(res.built, [])
        self.assertIn("docker not found", res.detail)

    def test_build_one_returns_127_without_docker(self) -> None:
        lines: list[str] = []
        with mock.patch.object(images.shutil, "which", return_value=None):
            rc = images.build_one("base", on_line=lines.append)
        self.assertEqual(rc, 127)
        self.assertTrue(any("docker not found" in line for line in lines))


class EnsureChainSkipsExistingTest(unittest.TestCase):
    def test_present_images_are_not_rebuilt(self) -> None:
        # docker present, both images already built -> nothing built, ok.
        with mock.patch.object(images.shutil, "which", return_value="/usr/bin/docker"), \
             mock.patch.object(images, "image_exists", return_value=True), \
             mock.patch.object(images, "build_one") as build:
            res = images.ensure_chain("node")
        self.assertTrue(res.ok)
        self.assertEqual(res.built, [])
        build.assert_not_called()

    def test_missing_images_built_base_first(self) -> None:
        order: list[str] = []
        with mock.patch.object(images.shutil, "which", return_value="/usr/bin/docker"), \
             mock.patch.object(images, "image_exists", return_value=False), \
             mock.patch.object(images, "build_one",
                               side_effect=lambda ov, **_: order.append(ov) or 0):
            res = images.ensure_chain("node")
        self.assertTrue(res.ok)
        self.assertEqual(order, ["base", "node"])  # base before overlay
        self.assertEqual(res.built, ["base", "node"])

    def test_build_failure_aborts_chain(self) -> None:
        # base build fails -> overlay is never attempted, result is not ok.
        with mock.patch.object(images.shutil, "which", return_value="/usr/bin/docker"), \
             mock.patch.object(images, "image_exists", return_value=False), \
             mock.patch.object(images, "build_one", return_value=1) as build:
            res = images.ensure_chain("node")
        self.assertFalse(res.ok)
        self.assertEqual(build.call_count, 1)  # stopped after base failed
        self.assertIn("failed to build claude-man:base", res.detail)

    def test_overlay_failure_keeps_base_in_built(self) -> None:
        # base builds, overlay fails: chain aborts after base, and the already-built base is
        # reported in `.built` (build_one appends per success before the failure return).
        with mock.patch.object(images.shutil, "which", return_value="/usr/bin/docker"), \
             mock.patch.object(images, "image_exists", return_value=False), \
             mock.patch.object(images, "build_one",
                               side_effect=lambda ov, **_: 0 if ov == "base" else 1):
            res = images.ensure_chain("node")
        self.assertFalse(res.ok)
        self.assertEqual(res.built, ["base"])  # partial: base succeeded, node did not
        self.assertIn("failed to build claude-man:node", res.detail)


class ImageClaudeVersionTest(unittest.TestCase):
    """Reading the baked claude-man.claude-version label off an image."""

    def test_none_without_docker(self) -> None:
        with mock.patch.object(images.shutil, "which", return_value=None):
            self.assertIsNone(images.image_claude_version("base"))

    def test_parses_label(self) -> None:
        cp = SimpleNamespace(returncode=0, stdout="2.1.169\n", stderr="")
        with mock.patch.object(images.shutil, "which", return_value="/usr/bin/docker"), \
             mock.patch.object(images.subprocess, "run", return_value=cp):
            self.assertEqual(images.image_claude_version("node"), "2.1.169")

    def test_missing_label_no_value(self) -> None:
        # Go's `index` prints "<no value>" for a missing key -> treated as unknown.
        cp = SimpleNamespace(returncode=0, stdout="<no value>\n", stderr="")
        with mock.patch.object(images.shutil, "which", return_value="/usr/bin/docker"), \
             mock.patch.object(images.subprocess, "run", return_value=cp):
            self.assertIsNone(images.image_claude_version("base"))

    def test_missing_image_nonzero(self) -> None:
        cp = SimpleNamespace(returncode=1, stdout="", stderr="No such image")
        with mock.patch.object(images.shutil, "which", return_value="/usr/bin/docker"), \
             mock.patch.object(images.subprocess, "run", return_value=cp):
            self.assertIsNone(images.image_claude_version("base"))


class RebuildChainTest(unittest.TestCase):
    """rebuild_chain force-rebuilds the WHOLE chain (unlike ensure_chain) at the target version."""

    def test_force_rebuilds_present_images_base_first(self) -> None:
        calls: list[tuple[str, str]] = []
        # image_exists would say True for both, but rebuild_chain must NOT consult it — it rebuilds
        # regardless, base before overlay, pinned to the target version.
        with mock.patch.object(images.shutil, "which", return_value="/usr/bin/docker"), \
             mock.patch.object(images, "build_one",
                               side_effect=lambda ov, *, claude_version, **_:
                                   calls.append((ov, claude_version)) or 0):
            res = images.rebuild_chain("node", claude_version="2.1.169")
        self.assertTrue(res.ok)
        self.assertEqual(calls, [("base", "2.1.169"), ("node", "2.1.169")])
        self.assertEqual(res.built, ["base", "node"])
        self.assertIn("2.1.169", res.detail)

    def test_failure_aborts_and_reports(self) -> None:
        with mock.patch.object(images.shutil, "which", return_value="/usr/bin/docker"), \
             mock.patch.object(images, "build_one",
                               side_effect=lambda ov, **_: 0 if ov == "base" else 2):
            res = images.rebuild_chain("python", claude_version="2.1.169")
        self.assertFalse(res.ok)
        self.assertEqual(res.built, ["base"])  # base succeeded, overlay failed
        self.assertIn("failed to rebuild claude-man:python", res.detail)

    def test_no_docker(self) -> None:
        with mock.patch.object(images.shutil, "which", return_value=None):
            res = images.rebuild_chain("base", claude_version="2.1.169")
        self.assertFalse(res.ok)
        self.assertIn("docker not found", res.detail)


class CliImageBuildTest(unittest.TestCase):
    """cmd_image_build orchestration: force the requested image, auto-build base for an overlay."""

    @staticmethod
    def _build(overlay: str | None, *, dry_run: bool = False) -> int:
        """Run cmd_image_build with the CLI's stdout (the '+ docker build …' echo) suppressed."""
        args = SimpleNamespace(overlay=overlay, claude_version="2.1.160", dry_run=dry_run)
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.cmd_image_build(args)

    def test_overlay_builds_base_first_when_missing(self) -> None:
        order: list[str] = []
        with mock.patch.object(images, "image_exists", return_value=False), \
             mock.patch.object(images, "build_one",
                               side_effect=lambda ov, **_: order.append(ov) or 0):
            rc = self._build("node")
        self.assertEqual(rc, 0)
        self.assertEqual(order, ["base", "node"])  # base layer built before the overlay

    def test_overlay_skips_base_when_present(self) -> None:
        order: list[str] = []
        with mock.patch.object(images, "image_exists", return_value=True), \
             mock.patch.object(images, "build_one",
                               side_effect=lambda ov, **_: order.append(ov) or 0):
            self._build("node")
        self.assertEqual(order, ["node"])  # base present -> only the overlay is built

    def test_base_is_force_built_even_when_present(self) -> None:
        # `image build base` must ALWAYS rebuild base (explicit force semantics), never skip it.
        order: list[str] = []
        with mock.patch.object(images, "image_exists", return_value=True), \
             mock.patch.object(images, "build_one",
                               side_effect=lambda ov, **_: order.append(ov) or 0):
            self._build("base")
        self.assertEqual(order, ["base"])

    def test_dry_run_overlay_does_not_probe_docker(self) -> None:
        # dry-run renders only the requested overlay; it must not inspect images or build base.
        with mock.patch.object(images, "image_exists") as exists, \
             mock.patch.object(images, "build_one", return_value=0) as build:
            rc = self._build("node", dry_run=True)
        self.assertEqual(rc, 0)
        exists.assert_not_called()
        build.assert_called_once()
        self.assertEqual(build.call_args.args[0], "node")

    def test_base_build_failure_aborts_overlay(self) -> None:
        # If the auto base build fails, the overlay must not be attempted.
        with mock.patch.object(images, "image_exists", return_value=False), \
             mock.patch.object(images, "build_one", return_value=2) as build:
            rc = self._build("node")
        self.assertEqual(rc, 2)
        build.assert_called_once()  # base failed -> overlay skipped


if __name__ == "__main__":
    unittest.main()
