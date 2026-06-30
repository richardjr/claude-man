"""The image-smoke verdict logic is the runtime invariant-2 gate — pin its classification."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.docker.smoke import Probe, classify  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def test_required_pass(self) -> None:
        p = Probe("version", ["claude", "--version"], required=True)
        failed, mark, _ = classify(p, 0, "2.1.160 (Claude Code)")
        self.assertFalse(failed)
        self.assertEqual(mark, "ok  ")

    def test_required_nonzero_fails(self) -> None:
        p = Probe("version", ["claude", "--version"], required=True)
        failed, mark, _ = classify(p, 1, "boom")
        self.assertTrue(failed)
        self.assertEqual(mark, "FAIL")

    def test_required_missing_expect_fails(self) -> None:
        p = Probe("rg", ["sh", "-lc", "command -v rg"], required=True, expect="/usr/bin/rg")
        failed, _, _ = classify(p, 0, "/tmp/extracted/rg")
        self.assertTrue(failed)

    def test_forbidden_marker_fails_even_on_zero_exit(self) -> None:
        # An EROFS in the output fails the probe even if the command returned 0.
        p = Probe("doctor", ["claude", "doctor"], required=False)
        failed, mark, detail = classify(p, 0, "wrote ok\nEROFS: read-only file system")
        self.assertTrue(failed)
        self.assertIn("EROFS", detail)

    def test_getpwuid_marker_fails(self) -> None:
        p = Probe("whoami", ["whoami"], required=True, expect="agent")
        failed, _, _ = classify(p, 1, "whoami: cannot find name for user ID 1000")
        self.assertTrue(failed)

    def test_best_effort_nonzero_only_warns(self) -> None:
        # A best-effort probe with a clean (no-forbidden) nonzero exit warns, does not fail.
        p = Probe("doctor", ["claude", "doctor"], required=False)
        failed, mark, _ = classify(p, 124, "(timed out)")
        self.assertFalse(failed)
        self.assertEqual(mark, "warn")


class BaseProbesTest(unittest.TestCase):
    """The base gate must keep exercising the baked neovim (TS+Markdown) under the hardened profile."""

    def test_includes_nvim_gates(self) -> None:
        from claudeman.docker.smoke import _base_probes
        names = [p.name for p in _base_probes()]
        self.assertIn("nvim --version", names)
        self.assertTrue(any("LSP servers" in n for n in names), names)
        self.assertTrue(any("treesitter" in n for n in names), names)


class OverlayProbesTest(unittest.TestCase):
    """Overlay-specific probes must exercise the overlay's tools' CORE ops under the floor."""

    def test_terraform_overlay_adds_core_op_probes(self) -> None:
        from claudeman.docker.smoke import _overlay_probes
        probes = _overlay_probes("terraform")
        names = [p.name for p in probes]
        # Not just --version: an actual `terraform init` write to /workspace + packer plugin-path write
        # + an aws config write proving the AWS_CONFIG_FILE redirect off the read-only HOME.
        self.assertTrue(any("terraform init" in n for n in names), names)
        self.assertTrue(any("packer plugin path" in n for n in names), names)
        self.assertTrue(any("aws config writes" in n for n in names), names)
        # The aws write probe must actually exercise a write (`aws configure set`), not just --version.
        aws_write = next(p for p in probes if "aws config writes" in p.name)
        self.assertIn("configure set", " ".join(aws_write.argv))

    def test_overlays_without_extra_probes_return_empty(self) -> None:
        # node/python/base tools are already covered by the base battery — no extra probes.
        from claudeman.docker.smoke import _overlay_probes
        self.assertEqual(_overlay_probes("base"), [])
        self.assertEqual(_overlay_probes("node"), [])
        self.assertEqual(_overlay_probes("python-node"), [])


if __name__ == "__main__":
    unittest.main()
