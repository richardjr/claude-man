"""`tui/setupview` — the setup wizard's pure view-model (dependency-free — no textual: the
step logic and body copy are plain functions; the screen is a thin renderer)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config, doctor  # noqa: E402
from claudeman.registry import settings as settings_registry  # noqa: E402
from claudeman.tui import setupview  # noqa: E402


def _check(status: str, check_id: str = "docker", label: str = "Docker",
           detail: str = "detail", hint: str = "hint") -> doctor.CheckResult:
    return doctor.CheckResult(check_id, label, status, detail, hint)


class ShouldOfferTest(unittest.TestCase):
    def test_only_a_completely_fresh_machine_offers(self) -> None:
        self.assertTrue(setupview.should_offer(
            config_exists=False, profile_count=0, project_count=0))

    def test_any_prior_state_suppresses(self) -> None:
        # config.toml alone (a Skip, or any past `config` change), a profile alone, or a
        # project alone — each means "not a fresh machine", never re-ambush the operator.
        for kwargs in (dict(config_exists=True, profile_count=0, project_count=0),
                       dict(config_exists=False, profile_count=1, project_count=0),
                       dict(config_exists=False, profile_count=0, project_count=2)):
            self.assertFalse(setupview.should_offer(**kwargs), kwargs)


class MaterialiseRoundTripTest(unittest.TestCase):
    """Skip/Finish persist via settings save — that must flip should_offer false."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        self.tmp.cleanup()

    def test_settings_save_materialises_and_suppresses(self) -> None:
        self.assertTrue(setupview.should_offer(
            config_exists=config.settings_toml_path().exists(),
            profile_count=0, project_count=0))
        settings_registry.save(settings_registry.load())  # what the wizard's Skip/Finish does
        self.assertTrue(config.settings_toml_path().exists())
        self.assertFalse(setupview.should_offer(
            config_exists=config.settings_toml_path().exists(),
            profile_count=0, project_count=0))


class StepsTest(unittest.TestCase):
    def test_step_order_pinned(self) -> None:
        self.assertEqual(setupview.STEPS,
                         ("welcome", "docker", "terminal", "profile", "image", "done"))

    def test_progress_line_counts_from_one(self) -> None:
        self.assertEqual(setupview.progress_line(0), "Setup · step 1/6 — welcome")
        self.assertEqual(setupview.progress_line(5), "Setup · step 6/6 — done")


class BodyLinesTest(unittest.TestCase):
    """Each step body renders its load-bearing strings (fix hints, key pointers)."""

    def test_welcome_pending_and_report(self) -> None:
        self.assertIn("Checking your system …", "\n".join(setupview.welcome_lines(None)))
        report = doctor.Report((_check(doctor.OK, detail="daemon reachable"),))
        text = "\n".join(setupview.welcome_lines(report))
        self.assertIn("Docker — daemon reachable", text)
        self.assertIn("skippable", text)

    def test_docker_fail_carries_hint_through(self) -> None:
        text = "\n".join(setupview.docker_lines(
            _check(doctor.FAIL, hint="sudo usermod -aG docker $USER")))
        self.assertIn("sudo usermod -aG docker $USER", text)
        self.assertIn("Re-check", text)

    def test_docker_ok_is_ready(self) -> None:
        text = "\n".join(setupview.docker_lines(_check(doctor.OK, hint="")))
        self.assertIn("Docker is ready", text)

    def test_terminal_fail_points_at_custom(self) -> None:
        text = "\n".join(setupview.terminal_lines(
            _check(doctor.FAIL, "terminal", "Terminal", "no supported terminal found")))
        self.assertIn("no supported terminal found", text)
        self.assertIn("custom", text)

    def test_profile_needs_the_host_claude(self) -> None:
        text = "\n".join(setupview.profile_lines(
            _check(doctor.WARN, "claude", "Claude CLI", "claude not found on PATH",
                   "install Claude Code"), 0))
        self.assertIn("install Claude Code", text)
        self.assertIn("skip this step", text)

    def test_profile_ready_explains_the_suspend_flow(self) -> None:
        text = "\n".join(setupview.profile_lines(
            _check(doctor.OK, "claude", "Claude CLI", "2.1.9"), 0))
        self.assertIn("setup-token", text)
        self.assertIn("browser", text)

    def test_image_not_built_offers_the_build(self) -> None:
        text = "\n".join(setupview.image_lines(
            _check(doctor.WARN, "image", "Base image", "claude-man:base not built")))
        self.assertIn("builds automatically", text)

    def test_done_points_at_new_project_and_doctor(self) -> None:
        text = "\n".join(setupview.done_lines())
        self.assertIn("n[/]", text)          # the new-project key
        self.assertIn("claudemanctl doctor", text)


if __name__ == "__main__":
    unittest.main()
