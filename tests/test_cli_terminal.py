"""`claudemanctl project shell|claude|nvim` auto-start-before-spawn ordering (dependency-free — no textual).

Pins the fix that a STOPPED/DEFINED project is started BEFORE the detached `docker exec` terminal is
spawned (previously the terminal opened against a dead container and its exec failed). Pure stdlib:
the docker/lifecycle/terminal calls are monkeypatched, so nothing shells out.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import cli  # noqa: E402
from claudeman import lifecycle  # noqa: E402
from claudeman.docker import runner  # noqa: E402
from claudeman.registry import projects  # noqa: E402
from claudeman.tui import terminals  # noqa: E402


class _Args:
    def __init__(self, slug: str) -> None:
        self.slug = slug


class OpenTerminalOrderingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[str] = []
        # Default stubs — each test overrides what it needs. Every dotted name used by
        # cli._open_terminal is patched so the helper never touches docker/git/the filesystem.
        self._patch(runner, "is_running", lambda slug: False)
        self._patch(runner, "exists", lambda slug: False)
        self._patch(projects, "exists", lambda slug: True)
        self._patch(projects, "load", lambda slug: f"project:{slug}")
        self._patch(lifecycle, "up", self._fake_up_ok)
        self._patch(terminals, "spawn_shell", lambda slug: self.calls.append(f"shell:{slug}"))
        self._patch(terminals, "spawn_claude", lambda slug: self.calls.append(f"claude:{slug}"))
        self._patch(terminals, "spawn_nvim", lambda slug: self.calls.append(f"nvim:{slug}"))

    def _patch(self, module, name, value) -> None:
        original = getattr(module, name)
        self.addCleanup(setattr, module, name, original)
        setattr(module, name, value)

    def _fake_up_ok(self, project, **kwargs):
        self.calls.append(f"up:{project}")
        return lifecycle.Result(True, f"started {project}")

    # -- stopped/defined: must start, THEN spawn -------------------------------
    def test_stopped_project_started_before_shell_spawn(self) -> None:
        rc = cli.cmd_project_shell(_Args("demo"))
        self.assertEqual(rc, 0)
        # The up() must precede the spawn — otherwise the exec lands on a dead container.
        self.assertEqual(self.calls, ["up:project:demo", "shell:demo"])

    def test_stopped_project_started_before_claude_spawn(self) -> None:
        rc = cli.cmd_project_claude(_Args("demo"))
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, ["up:project:demo", "claude:demo"])

    def test_stopped_project_started_before_nvim_spawn(self) -> None:
        rc = cli.cmd_project_nvim(_Args("demo"))
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, ["up:project:demo", "nvim:demo"])

    # -- already running: spawn straight in, no start -------------------------
    def test_running_project_spawns_without_up(self) -> None:
        self._patch(runner, "is_running", lambda slug: True)
        rc = cli.cmd_project_claude(_Args("demo"))
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, ["claude:demo"])  # no up:* — already running

    # -- start failure: do NOT spawn, return non-zero -------------------------
    def test_failed_start_does_not_spawn(self) -> None:
        self._patch(lifecycle, "up",
                    lambda project, **kw: lifecycle.Result(False, "docker start failed"))
        rc = cli.cmd_project_shell(_Args("demo"))
        self.assertEqual(rc, 1)
        self.assertEqual(self.calls, [])  # no spawn after a failed start

    # -- unknown slug (no registry entry, no container) -----------------------
    def test_unknown_project_errors_without_spawn(self) -> None:
        self._patch(projects, "exists", lambda slug: False)
        rc = cli.cmd_project_claude(_Args("ghost"))
        self.assertEqual(rc, 1)
        self.assertEqual(self.calls, [])  # never started, never spawned

    # -- stopped orphan (container exists, no registry entry) -----------------
    def test_stopped_orphan_declined_without_spawn(self) -> None:
        self._patch(projects, "exists", lambda slug: False)
        self._patch(runner, "exists", lambda slug: True)  # an orphan container is present
        rc = cli.cmd_project_shell(_Args("orphan"))
        self.assertEqual(rc, 1)
        self.assertEqual(self.calls, [])  # orphans aren't managed — no up, no spawn

    # -- running orphan: exec straight in (is_running short-circuits) ---------
    def test_running_orphan_spawns(self) -> None:
        self._patch(runner, "is_running", lambda slug: True)
        self._patch(projects, "exists", lambda slug: False)  # orphan, but it IS running
        rc = cli.cmd_project_shell(_Args("orphan"))
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, ["shell:orphan"])

    # -- missing terminal binary surfaces a clean error, not a traceback ------
    def test_spawn_runtimeerror_returns_nonzero(self) -> None:
        self._patch(runner, "is_running", lambda slug: True)

        def _boom(slug):
            raise RuntimeError("no supported terminal found")

        self._patch(terminals, "spawn_claude", _boom)
        rc = cli.cmd_project_claude(_Args("demo"))
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
