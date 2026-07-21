"""SEC-3 — one `claude` per container, enforced at `terminals.spawn_claude` (dependency-free).

A second claude in the same container races on `.claude.json`/session writes (invariant 6), so the
spawn path probes the container for a live `claude` process first and refuses to start another.
The probe FAILS OPEN: a wedged docker daemon must not lock the operator out of their own project.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.registry.schema import ValidationError  # noqa: E402
from claudeman.tui import terminals  # noqa: E402


class ProbeArgvTest(unittest.TestCase):
    def test_probe_argv_shape(self) -> None:
        argv = terminals.build_claude_probe_argv("demo")
        self.assertEqual(argv[:3], ["docker", "exec", "claude-man-demo"])
        self.assertEqual(argv[3:5], ["sh", "-c"])
        self.assertIn("/proc/[0-9]*/comm", argv[5])  # no procps (pgrep) dependency in the image

    def test_probe_rejects_malformed_slug(self) -> None:
        with self.assertRaises(ValidationError):
            terminals.build_claude_probe_argv("demo; rm -rf /")


class SpawnGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        # spawn() must never actually Popen in these tests.
        self._spawned: list[tuple] = []
        patcher = mock.patch.object(
            terminals, "spawn",
            lambda slug, program, **kw: self._spawned.append((slug, program)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_refuses_second_claude(self) -> None:
        with mock.patch.object(terminals, "claude_already_running", lambda slug: True):
            with self.assertRaises(RuntimeError) as ctx:
                terminals.spawn_claude("demo")
        self.assertIn("already running", str(ctx.exception))
        self.assertEqual(self._spawned, [])  # never reached the spawn layer

    def test_spawns_when_no_claude_live(self) -> None:
        with mock.patch.object(terminals, "claude_already_running", lambda slug: False), \
                mock.patch.object(terminals, "launch_workdir", lambda slug: "/workspace/x"), \
                mock.patch.object(terminals, "claude_model_args", lambda slug: ()):
            terminals.spawn_claude("demo")
        self.assertEqual(self._spawned, [("demo", "claude")])

    def test_shell_spawn_is_not_guarded(self) -> None:
        # "A second shell is fine" (invariant 6) — only claude is single-instance.
        with mock.patch.object(terminals, "claude_already_running",
                               mock.Mock(side_effect=AssertionError("shell must not probe"))), \
                mock.patch.object(terminals, "launch_workdir", lambda slug: ""):
            terminals.spawn_shell("demo")
        self.assertEqual(self._spawned, [("demo", "bash")])


class ClaudeModelPinTest(unittest.TestCase):
    """spawn_claude carries the project's claude-model pin as ``--model`` argv (fail-open)."""

    def test_pin_becomes_model_flag(self) -> None:
        with mock.patch.object(terminals.projects, "load",
                               lambda slug: mock.Mock(claude_model="claude-fable-5")):
            self.assertEqual(terminals.claude_model_args("demo"),
                             ("--model", "claude-fable-5"))

    def test_no_pin_no_flag(self) -> None:
        with mock.patch.object(terminals.projects, "load",
                               lambda slug: mock.Mock(claude_model="")):
            self.assertEqual(terminals.claude_model_args("demo"), ())

    def test_unknown_project_fails_open(self) -> None:
        # A registry hiccup must never block opening claude — no pin, claude's own default.
        def _raise(slug):
            raise FileNotFoundError(slug)
        with mock.patch.object(terminals.projects, "load", _raise):
            self.assertEqual(terminals.claude_model_args("demo"), ())

    def test_spawn_claude_passes_pin_args(self) -> None:
        calls: list[tuple] = []
        with mock.patch.object(terminals, "spawn",
                               lambda slug, program, **kw: calls.append((slug, program, kw))), \
                mock.patch.object(terminals, "claude_already_running", lambda slug: False), \
                mock.patch.object(terminals, "launch_workdir", lambda slug: "/workspace"), \
                mock.patch.object(terminals, "claude_model_args",
                                  lambda slug: ("--model", "opus")):
            terminals.spawn_claude("demo")
        self.assertEqual(calls, [("demo", "claude",
                                  {"workdir": "/workspace", "args": ("--model", "opus")})])


class ProbeFailOpenTest(unittest.TestCase):
    def _probe_with(self, runner) -> bool:
        with mock.patch.object(terminals.subprocess, "run", runner):
            return terminals.claude_already_running("demo")

    def test_rc0_means_running(self) -> None:
        self.assertTrue(self._probe_with(
            lambda *a, **kw: mock.Mock(returncode=0)))

    def test_rc1_means_not_running(self) -> None:
        self.assertFalse(self._probe_with(
            lambda *a, **kw: mock.Mock(returncode=1)))

    def test_docker_missing_fails_open(self) -> None:
        def _raise(*a, **kw):
            raise FileNotFoundError("docker not found")
        self.assertFalse(self._probe_with(_raise))

    def test_timeout_fails_open(self) -> None:
        def _raise(*a, **kw):
            raise terminals.subprocess.TimeoutExpired(cmd="docker", timeout=10)
        self.assertFalse(self._probe_with(_raise))


if __name__ == "__main__":
    unittest.main()
