"""Pure view-model for the first-run setup wizard (``screens/setup.py``).

House pattern (``splash.py`` / ``profilesview.py`` / ``packsview.py``): no textual imports, so
the step logic — when the wizard auto-offers, the fixed step order, and every step's body text —
is unit-testable in the dependency-free suite. The screen stays a thin renderer. Body lines use
Rich markup tags (plain strings here; the screen renders them).

First-run signal: the wizard auto-offers only when the machine is COMPLETELY fresh — no
``config.toml``, no profiles, no projects (``should_offer``). Skip/Finish materialise the
canonical ``config.toml`` (``settings_registry.save(load())``), which flips the signal false
forever; there is deliberately no ``setup_done`` settings field — "this machine has been
configured at least once" is the honest signal, and any other first action that materialises the
file (a ``config`` change, a Settings save) legitimately implies the same. The wizard stays
re-runnable from Settings (``,`` -> ``w``).
"""

from __future__ import annotations

from .. import doctor

#: Fixed step order; the screen walks an index over this.
STEPS = ("welcome", "docker", "terminal", "profile", "image", "done")

_GLYPHS = {doctor.OK: "[green]✓[/]", doctor.WARN: "[yellow]![/]", doctor.FAIL: "[red]✗[/]"}


def should_offer(*, config_exists: bool, profile_count: int, project_count: int) -> bool:
    """Auto-show the wizard only on a completely fresh machine (all three absent/empty)."""
    return not config_exists and profile_count == 0 and project_count == 0


def progress_line(step_index: int) -> str:
    name = STEPS[step_index]
    return f"Setup · step {step_index + 1}/{len(STEPS)} — {name}"


def _check_line(check: doctor.CheckResult) -> str:
    return f" {_GLYPHS[check.status]} {check.label} — {check.detail}"


def welcome_lines(report: doctor.Report | None) -> list[str]:
    if report is None:
        return ["Welcome to claude-man.", "", "Checking your system …"]
    lines = ["Welcome to claude-man. Here's what this machine looks like:", ""]
    lines += [_check_line(c) for c in report.checks]
    lines += ["", "The next steps walk through anything that needs attention — every one is "
              "skippable, and the wizard can be re-run later from Settings (, then w)."]
    return lines


def docker_lines(check: doctor.CheckResult | None) -> list[str]:
    if check is None:
        return ["Checking docker …"]
    lines = [_check_line(check)]
    if check.hint:
        lines += ["", f"Fix: {check.hint}", "",
                  "Re-check after fixing — or continue anyway (projects can't start until "
                  "docker works)."]
    else:
        lines += ["", "Docker is ready — containers can be created and started."]
    return lines


def terminal_lines(check: doctor.CheckResult | None) -> list[str]:
    if check is None:
        return ["Checking terminal …"]
    lines = [_check_line(check),
             "",
             "Project shells, claude, and the editor open in detached terminal windows using "
             "this launcher."]
    if check.status == doctor.OK:
        lines.append("Choose… picks a different one (or a custom launcher template).")
        return lines
    if check.hint:
        lines += ["", f"Fix: {check.hint}"]
    lines += ["", "Choose… lists the supported terminals — pick `custom` if yours isn't "
              "in the table."]
    return lines


def profile_lines(claude_check: doctor.CheckResult | None, profile_count: int) -> list[str]:
    """The profile step body. When the host claude CLI is missing the mint flow can't run."""
    if claude_check is None:
        return ["Checking the host claude CLI …"]
    if claude_check.status != doctor.OK:
        return [_check_line(claude_check),
                "",
                "A profile stores a long-lived token minted by the host `claude setup-token`, "
                "so the claude CLI is needed for this step.",
                f"Fix: {claude_check.hint}",
                "",
                "Re-check after installing — or skip this step and create a profile later "
                "(Settings , then w, or `claudemanctl profile add`)."]
    lines = []
    if profile_count:
        lines += [f"[green]✓[/] {profile_count} profile(s) already defined — you can add "
                  "another, or continue.", ""]
    lines += ["A profile is one Claude account (e.g. personal / work). Creating it runs "
              "`claude setup-token`: the TUI pauses back to this terminal, a browser opens to "
              "authorise, and you paste the token when prompted — then the TUI resumes.",
              "",
              "Name the profile and press Create profile:"]
    return lines


def image_lines(check: doctor.CheckResult | None) -> list[str]:
    if check is None:
        return ["Checking the base image …"]
    lines = [_check_line(check)]
    if check.status == doctor.OK:
        lines += ["", "The hardened base image is already built — nothing to do."]
    else:
        lines += ["", "Projects run in a hardened container image. It builds automatically on "
                  "first project create, but building it now (a few minutes, streamed below) "
                  "makes the first project instant."]
    return lines


def done_lines() -> list[str]:
    return ["Setup finished. From here:",
            "",
            " [bold]n[/]  create your first project (a container + git workspace)",
            " [bold],[/]  Settings — ssh keys, git identity, terminal, memory cap (w re-runs "
            "this wizard)",
            " [bold]?[/]  the key bar at the bottom lists everything else",
            "",
            "CLI twin: `claudemanctl doctor` re-checks the host any time; the full walkthrough "
            "lives in docs/TUI-GUIDE.md."]
