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

    def test_timeout_is_daemon_not_responding(self) -> None:
        c = doctor.classify_docker(which_found=True, rc=None, stdout="", stderr="")
        self.assertEqual(c.status, doctor.FAIL)
        self.assertIn("timed out", c.detail)

    def test_reachable_daemon_is_ok_with_version(self) -> None:
        c = doctor.classify_docker(which_found=True, rc=0, stdout="29.0.1\n", stderr="")
        self.assertEqual(c.status, doctor.OK)
        self.assertIn("29.0.1", c.detail)
        self.assertEqual(c.hint, "")


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
