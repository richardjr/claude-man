"""`doctor` — host prerequisite checks (dependency-free: only the pure classifiers and a
mocked-out `cmd_doctor` are exercised; no docker/network/subprocess)."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import cli, doctor  # noqa: E402


class DockerClassifyTest(unittest.TestCase):
    """The three fresh-machine docker states get DISTINCT fix hints (issue #31: they all used to
    surface as the same silent empty table / opaque `docker build exited 1`)."""

    def test_binary_missing_fails_with_install_hint(self) -> None:
        c = doctor.classify_docker(which_found=False, rc=None, stdout="", stderr="")
        self.assertEqual((c.status, c.id), (doctor.FAIL, "docker"))
        self.assertIn("docs.docker.com/engine/install", c.hint)
        self.assertIn("systemctl enable --now docker", c.hint)

    def test_binary_missing_macos_hints_docker_desktop(self) -> None:
        c = doctor.classify_docker(which_found=False, rc=None, stdout="", stderr="", macos=True)
        self.assertIn("Docker Desktop", c.hint)

    def test_socket_permission_denied_hints_docker_group(self) -> None:
        stderr = ("permission denied while trying to connect to the Docker daemon socket at "
                  "unix:///var/run/docker.sock")
        c = doctor.classify_docker(which_found=True, rc=1, stdout="", stderr=stderr)
        self.assertEqual(c.status, doctor.FAIL)
        self.assertIn("sudo usermod -aG docker $USER", c.hint)

    def test_daemon_down_hints_start(self) -> None:
        stderr = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
        c = doctor.classify_docker(which_found=True, rc=1, stdout="", stderr=stderr)
        self.assertEqual(c.status, doctor.FAIL)
        self.assertIn("sudo systemctl start docker", c.hint)

    def test_timeout_is_hung_or_starting_never_start_hint(self) -> None:
        # A timeout = the socket ACCEPTED and nothing answered (a down daemon is a fast rc 1), so the
        # verdict says hung/starting and hints status/restart — never the misleading `start docker`
        # (issue #34).
        c = doctor.classify_docker(which_found=True, rc=None, stdout="", stderr="")
        self.assertEqual(c.status, doctor.FAIL)
        self.assertIn("no answer", c.detail)
        self.assertIn("hung or still starting", c.detail)
        self.assertIn("systemctl status docker", c.hint)
        self.assertIn("systemctl restart docker", c.hint)
        self.assertNotIn("systemctl start docker", c.hint)

    def test_double_timeout_reports_seconds_waited(self) -> None:
        c = doctor.classify_docker(which_found=True, rc=None, stdout="", stderr="",
                                   slow_start_s=36.4)
        self.assertEqual(c.status, doctor.FAIL)
        self.assertIn("no answer in 36s", c.detail)

    def test_timeout_macos_hints_docker_desktop_restart(self) -> None:
        c = doctor.classify_docker(which_found=True, rc=None, stdout="", stderr="", macos=True,
                                   slow_start_s=36.0)
        self.assertEqual(c.status, doctor.FAIL)
        self.assertIn("Docker Desktop", c.hint)

    def test_cold_start_answered_is_ok_and_says_so(self) -> None:
        # The retry waited out a socket-activated dockerd start: OK (nothing to fix), but factual.
        c = doctor.classify_docker(which_found=True, rc=0, stdout="29.7.2\n", stderr="",
                                   slow_start_s=8.2)
        self.assertEqual(c.status, doctor.OK)
        self.assertIn("29.7.2", c.detail)
        self.assertIn("answered after 8s (cold start)", c.detail)
        self.assertEqual(c.hint, "")

    def test_reachable_daemon_is_ok_with_version(self) -> None:
        c = doctor.classify_docker(which_found=True, rc=0, stdout="29.0.1\n", stderr="")
        self.assertEqual(c.status, doctor.OK)
        self.assertIn("29.0.1", c.detail)
        self.assertEqual(c.hint, "")


class DockerProbeRetryTest(unittest.TestCase):
    """`probe_docker` retries ONCE, only on a first-attempt timeout, with the cold-start budget
    (issue #34: a socket-activated dockerd takes ~8 s to answer its first client — the TUI's startup
    probe on a fresh boot — and a lone 6 s attempt false-FAILed). `_run` is mocked; no docker."""

    _ARGV = ["docker", "version", "--format", "{{.Server.Version}}"]

    def _probe(self, results, clock=(0.0, 8.2)):
        with mock.patch.object(doctor.shutil, "which", return_value="/usr/bin/docker"), \
             mock.patch.object(doctor, "_run", side_effect=list(results)) as run, \
             mock.patch.object(doctor, "time") as t, \
             mock.patch.object(doctor.hostplatform, "is_macos", return_value=False), \
             mock.patch.object(doctor.hostplatform, "is_wsl", return_value=False):
            t.monotonic.side_effect = list(clock)
            return doctor.probe_docker(timeout=6.0, cold_start_timeout=30.0), run

    def test_first_attempt_ok_never_retries(self) -> None:
        c, run = self._probe([(0, "29.7.2\n", "")])
        self.assertEqual(c.status, doctor.OK)
        self.assertNotIn("cold start", c.detail)
        self.assertEqual(run.call_args_list, [mock.call(self._ARGV, 6.0)])

    def test_first_attempt_timeout_retries_with_cold_start_budget(self) -> None:
        c, run = self._probe([(None, "", ""), (0, "29.7.2\n", "")])
        self.assertEqual(c.status, doctor.OK)
        self.assertIn("server 29.7.2", c.detail)
        self.assertIn("answered after 8s (cold start)", c.detail)
        self.assertEqual(run.call_args_list,
                         [mock.call(self._ARGV, 6.0), mock.call(self._ARGV, 30.0)])

    def test_both_attempts_timeout_is_fail_with_total_wait(self) -> None:
        c, run = self._probe([(None, "", ""), (None, "", "")], clock=(0.0, 36.4))
        self.assertEqual(c.status, doctor.FAIL)
        self.assertIn("no answer in 36s", c.detail)
        self.assertIn("systemctl restart docker", c.hint)
        self.assertEqual(len(run.call_args_list), 2)

    def test_connect_refused_is_final_on_first_attempt(self) -> None:
        # rc 1 `Cannot connect` = daemon down: a retry would just wait 30 s for the same answer.
        stderr = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
        c, run = self._probe([(1, "", stderr)])
        self.assertEqual(c.status, doctor.FAIL)
        self.assertIn("sudo systemctl start docker", c.hint)
        self.assertEqual(len(run.call_args_list), 1)

    def test_binary_missing_skips_the_probe_entirely(self) -> None:
        with mock.patch.object(doctor.shutil, "which", return_value=None), \
             mock.patch.object(doctor, "_run") as run:
            c = doctor.probe_docker()
        self.assertEqual(c.status, doctor.FAIL)
        self.assertIn("not found on PATH", c.detail)
        run.assert_not_called()


class ClaudeClassifyTest(unittest.TestCase):
    def test_missing_is_warn_not_fail(self) -> None:
        # Host claude is only needed to mint tokens — its absence must not block a working setup.
        c = doctor.classify_claude(which_found=False, rc=None, stdout="")
        self.assertEqual(c.status, doctor.WARN)
        self.assertIn("mint profile tokens", c.hint)

    def test_present_is_ok_with_version(self) -> None:
        c = doctor.classify_claude(which_found=True, rc=0, stdout="2.1.9 (Claude Code)\n")
        self.assertEqual((c.status, c.detail), (doctor.OK, "2.1.9 (Claude Code)"))


class TerminalClassifyTest(unittest.TestCase):
    def test_resolved_is_ok(self) -> None:
        c = doctor.classify_terminal("ptyxis", "")
        self.assertEqual((c.status, c.detail), (doctor.OK, "launcher: ptyxis"))

    def test_unresolved_carries_resolve_error_and_fix_hint(self) -> None:
        c = doctor.classify_terminal(None, "no supported terminal found (…)")
        self.assertEqual(c.status, doctor.FAIL)
        self.assertIn("no supported terminal found", c.detail)
        self.assertIn("config terminal", c.hint)


class ImageClassifyTest(unittest.TestCase):
    def test_docker_down_is_unknown_warn(self) -> None:
        c = doctor.classify_image(docker_ok=False, exists=False, claude_version=None)
        self.assertEqual((c.status, c.detail), (doctor.WARN, "unknown (docker unavailable)"))

    def test_not_built_is_warn_with_autobuild_note(self) -> None:
        c = doctor.classify_image(docker_ok=True, exists=False, claude_version=None)
        self.assertEqual(c.status, doctor.WARN)
        self.assertIn("built automatically on first project create", c.hint)

    def test_built_is_ok_with_claude_version(self) -> None:
        c = doctor.classify_image(docker_ok=True, exists=True, claude_version="2.1.9")
        self.assertEqual(c.status, doctor.OK)
        self.assertIn("claude 2.1.9", c.detail)


class ProfilesClassifyTest(unittest.TestCase):
    def test_none_is_warn_with_wizard_hint(self) -> None:
        c = doctor.classify_profiles(())
        self.assertEqual(c.status, doctor.WARN)
        self.assertIn("profile add", c.hint)

    def test_tokenless_profile_is_warn_naming_it(self) -> None:
        c = doctor.classify_profiles((("home", True), ("work", False)))
        self.assertEqual(c.status, doctor.WARN)
        self.assertIn("work", c.detail)
        self.assertIn("profile renew", c.hint)

    def test_healthy_profiles_are_ok(self) -> None:
        c = doctor.classify_profiles((("home", True),))
        self.assertEqual((c.status, c.hint), (doctor.OK, ""))


class ReportTest(unittest.TestCase):
    @staticmethod
    def _check(status: str) -> doctor.CheckResult:
        return doctor.CheckResult("docker", "Docker", status, "detail", "hint")

    def test_ok_iff_no_fail(self) -> None:
        self.assertTrue(doctor.Report((self._check(doctor.OK), self._check(doctor.WARN))).ok)
        self.assertFalse(doctor.Report((self._check(doctor.OK), self._check(doctor.FAIL))).ok)

    def test_get_by_id(self) -> None:
        rep = doctor.Report((self._check(doctor.OK),))
        self.assertEqual(rep.get("docker").status, doctor.OK)
        self.assertIsNone(rep.get("nope"))


class CmdDoctorTest(unittest.TestCase):
    """`claudemanctl doctor` — rc mapping + hint rendering over a canned report."""

    def _run(self, report: doctor.Report) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(doctor, "run_all", lambda: report), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.cmd_doctor(None)
        return rc, out.getvalue(), err.getvalue()

    def test_healthy_report_rc0(self) -> None:
        rep = doctor.Report((doctor.CheckResult("docker", "Docker", doctor.OK, "reachable"),))
        rc, out, _ = self._run(rep)
        self.assertEqual(rc, 0)
        self.assertIn("[ OK ] Docker: reachable", out)
        self.assertIn("ready", out)

    def test_failing_report_rc1_with_hint(self) -> None:
        rep = doctor.Report((
            doctor.CheckResult("docker", "Docker", doctor.FAIL, "daemon not reachable",
                               "start it"),
            doctor.CheckResult("claude", "Claude CLI", doctor.WARN, "not found", "install it"),
        ))
        rc, out, err = self._run(rep)
        self.assertEqual(rc, 1)
        self.assertIn("[FAIL] Docker: daemon not reachable", out)
        self.assertIn("start it", out)          # the hint renders under its check
        self.assertIn("[warn] Claude CLI", out)
        self.assertIn("1 blocking problem", err)  # WARNs don't count toward the failure total


if __name__ == "__main__":
    unittest.main()
