"""Terminal-spawn argv: the `docker exec -w <launch_workdir>` wiring (dependency-free — no textual)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.tui import terminals  # noqa: E402


class TerminalWorkdirTest(unittest.TestCase):
    def test_claude_keep_open_includes_workdir(self) -> None:
        argv = terminals.build_ghostty_argv("demo", "claude", workdir="/workspace/svc")
        joined = " ".join(argv)
        self.assertIn("docker exec -it -w /workspace/svc claude-man-demo claude", joined)

    def test_bash_direct_exec_has_w_flag(self) -> None:
        argv = terminals._inner_exec("demo", "bash", keep_open=False, workdir="/workspace/x")
        self.assertEqual(argv[:5], ["docker", "exec", "-it", "-w", "/workspace/x"])

    def test_empty_workdir_emits_no_w(self) -> None:
        argv = terminals._inner_exec("demo", "bash", keep_open=False, workdir="")
        self.assertNotIn("-w", argv)
        self.assertEqual(argv, ["docker", "exec", "-it", "claude-man-demo", "bash"])

    def test_alacritty_claude_workdir_passthrough(self) -> None:
        argv = terminals.build_alacritty_argv("demo", "claude", workdir="/workspace/api")
        self.assertIn("-w /workspace/api", " ".join(argv))


if __name__ == "__main__":
    unittest.main()
