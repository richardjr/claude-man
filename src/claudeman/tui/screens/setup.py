"""First-run setup wizard — checks the host, then walks terminal / profile / image setup.

Auto-offered on a completely fresh machine (``setupview.should_offer``: no config.toml, no
profiles, no projects — pushed UNDER the boot splash, which scrolls off to reveal it) and
re-runnable any time from Settings (``,`` -> ``w``). One self-updating ModalScreen walking an
internal index over ``setupview.STEPS`` (the ``ShutdownScreen`` shape, not six stacked screens);
all step copy comes from the pure ``setupview`` so it stays unit-testable.

The profile step is the one genuinely interactive piece: ``claude setup-token`` needs a browser
+ a pasted token, so Create profile SUSPENDS the TUI (``app.suspend()`` — the tty is restored,
the existing ``setup_token.mint`` flow runs with inherited stdio exactly as the CLI does it),
then resumes and repaints. Deliberately on the UI thread — the flow is blocking by design.

Skip and Finish both materialise the canonical ``config.toml``
(``settings_registry.save(load())``) so the wizard never auto-offers again — see the
``setupview`` docstring for why that's the signal rather than a settings field.
"""

from __future__ import annotations

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ItemGrid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RichLog, Static

from ... import config, doctor
from ...docker import images
from ...profiles import setup_token
from ...registry import profiles as profiles_registry
from ...registry import settings as settings_registry
from ...registry.schema import ValidationError, validate_slug
from .. import setupview, terminals
from .terminal_custom import CustomTerminalScreen
from .terminal_select import TerminalSelectScreen


class SetupWizardScreen(ModalScreen[None]):
    """Guided host setup: welcome/checks -> docker -> terminal -> profile -> image -> done."""

    BINDINGS = [Binding("escape", "skip", "Skip setup")]
    CSS = """
    SetupWizardScreen { align: center middle; }
    #dialog {
        width: 78; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #wizard-title { text-style: bold; padding-bottom: 1; }
    #step-body { height: auto; }
    #profile-name { margin-top: 1; }
    #wizard-error { color: $error; height: auto; }
    #build-log { height: 10; margin-top: 1; }
    /* ItemGrid wraps the action buttons into rows instead of cropping them off the
       dialog's right edge — see CLAUDE.md "TUI dialog button rows" (reflow, no crop). */
    #buttons { height: auto; padding-top: 1; grid-gutter: 0 1; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._step = 0
        self._report: doctor.Report | None = None
        self._mint_msg = ""     # outcome line from the last Create profile attempt
        self._building = False

    # -- layout -----------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("", id="wizard-title")
            yield Static("", id="step-body")
            yield Input(placeholder="personal", id="profile-name")
            yield Label("", id="wizard-error")
            yield RichLog(id="build-log", markup=False, wrap=True)
            # The FULL button set, composed once; _render_buttons toggles display/label/variant
            # per step (a remove_children + mount rebuild races textual's async removal and
            # duplicates IDs when a worker re-render lands mid-flight).
            with ItemGrid(id="buttons", min_column_width=16):
                yield Button("Re-check", id="recheck")
                yield Button("Choose…", id="choose")
                yield Button("Create profile", id="create")
                yield Button("Build now", id="build")
                yield Button("Continue", id="continue")
                yield Button("Skip setup", id="skip")
                yield Button("Finish", id="finish")

    def on_mount(self) -> None:
        self._render_step()
        self._doctor_worker()

    # -- doctor -----------------------------------------------------------
    @work(thread=True, group="wizard-doctor", exclusive=True)
    def _doctor_worker(self) -> None:
        report = doctor.run_all()
        self.app.call_from_thread(self._set_report, report)

    def _set_report(self, report: doctor.Report) -> None:
        self._report = report
        self._render_step()

    def _check(self, check_id: str) -> doctor.CheckResult | None:
        return self._report.get(check_id) if self._report else None

    # -- step rendering ---------------------------------------------------
    def _step_name(self) -> str:
        return setupview.STEPS[self._step]

    def _render_step(self) -> None:
        name = self._step_name()
        self.query_one("#wizard-title", Label).update(setupview.progress_line(self._step))
        self.query_one("#wizard-error", Label).update("")
        self.query_one("#step-body", Static).update("\n".join(self._body_lines(name)))
        name_input = self.query_one("#profile-name", Input)
        name_input.display = (
            name == "profile" and self._claude_ok() and not self._mint_msg.startswith("[green]"))
        self.query_one("#build-log", RichLog).display = name == "image" and self._building
        self._render_buttons(name)
        if name_input.display:
            name_input.focus()

    def _claude_ok(self) -> bool:
        check = self._check("claude")
        return check is not None and check.status == doctor.OK

    def _body_lines(self, name: str) -> list[str]:
        if name == "welcome":
            return setupview.welcome_lines(self._report)
        if name == "docker":
            return setupview.docker_lines(self._check("docker"))
        if name == "terminal":
            # Probed fresh (cheap — no subprocess): the choice may just have changed via Choose….
            return setupview.terminal_lines(doctor.probe_terminal() if self._report else None)
        if name == "profile":
            lines = setupview.profile_lines(self._check("claude"),
                                            len(profiles_registry.list_names()))
            if self._mint_msg:
                lines += ["", self._mint_msg]
            return lines
        if name == "image":
            return setupview.image_lines(self._check("image"))
        return setupview.done_lines()

    def _render_buttons(self, name: str) -> None:
        show: dict[str, str] = {}  # button id -> label for this step
        primary = "continue"       # the success-styled, focused button
        if name == "welcome":
            show = {"continue": "Continue", "skip": "Skip setup"}
        elif name == "docker":
            check = self._check("docker")
            if check is not None and check.status == doctor.OK:
                show = {"continue": "Continue"}
            else:
                show = {"recheck": "Re-check", "continue": "Continue anyway",
                        "skip": "Skip setup"}
                primary = "recheck"
        elif name == "terminal":
            show = {"choose": "Choose…", "continue": "Continue"}
        elif name == "profile":
            if self._claude_ok():
                show = {"create": "Create profile", "continue": "Continue"}
                primary = "create"
            else:
                show = {"recheck": "Re-check", "continue": "Continue"}
                primary = "recheck"
        elif name == "image":
            check = self._check("image")
            if self._building:
                show = {}  # the build worker owns the screen until it reports back
            elif check is not None and check.status == doctor.OK:
                show = {"continue": "Continue"}
            else:
                show = {"build": "Build now", "continue": "Skip step"}
                primary = "build"
        else:  # done
            show = {"finish": "Finish"}
            primary = "finish"
        for btn in self.query_one("#buttons", ItemGrid).query(Button):
            bid = btn.id or ""
            btn.display = bid in show
            if bid in show:
                btn.label = show[bid]
                btn.variant = "success" if bid == primary else "default"
        if primary in show:
            self.query_one(f"#{primary}", Button).focus()

    # -- button dispatch ---------------------------------------------------
    @on(Button.Pressed)
    def _dispatch(self, event: Button.Pressed) -> None:
        handler = {
            "continue": self._advance,
            "skip": self.action_skip,
            "finish": self._finish,
            "recheck": self._recheck,
            "choose": self._choose_terminal,
            "create": self._create_profile,
            "build": self._start_build,
        }.get(event.button.id or "")
        if handler:
            handler()

    def _advance(self) -> None:
        self._step += 1
        self._render_step()

    def action_skip(self) -> None:
        if self._building:
            return  # the build worker owns the screen until it reports back
        self._finish()

    def _finish(self) -> None:
        # Materialise config.toml (pure defaults if nothing was chosen) so should_offer flips
        # false — the wizard never auto-offers again on this machine (re-run: Settings , -> w).
        try:
            settings_registry.save(settings_registry.load())
        except Exception:  # noqa: BLE001 - a bad config must not trap the operator in the wizard
            pass
        self.dismiss(None)

    def _recheck(self) -> None:
        self._report = None
        self._render_step()
        self._doctor_worker()

    # -- terminal step -----------------------------------------------------
    def _choose_terminal(self) -> None:
        s = settings_registry.load()
        self.app.push_screen(
            TerminalSelectScreen(s.terminal_program, s.terminal_command), self._on_terminal)

    def _on_terminal(self, choice) -> None:
        if choice is None:
            return
        if choice == terminals.CUSTOM_PROGRAM:
            self.app.push_screen(
                CustomTerminalScreen(settings_registry.load().terminal_command),
                self._on_custom_terminal)
            return
        settings_registry.set_terminal(program=choice)
        self._render_step()

    def _on_custom_terminal(self, template) -> None:
        if template is None:
            return
        try:
            settings_registry.set_terminal(program=terminals.CUSTOM_PROGRAM,
                                           command=list(template))
        except Exception as exc:  # noqa: BLE001 - the modal pre-validates; surface anything else
            self.query_one("#wizard-error", Label).update(escape(str(exc)))
            return
        self._render_step()

    # -- profile step ------------------------------------------------------
    @on(Input.Submitted, "#profile-name")
    def _create_profile(self) -> None:
        name = self.query_one("#profile-name", Input).value.strip()
        err = self.query_one("#wizard-error", Label)
        try:
            validate_slug(name or "")
        except ValidationError as exc:
            err.update(str(exc) if name else "a profile name is required (e.g. personal)")
            return
        make_default = not profiles_registry.list_names()
        # UI thread by design: app.suspend() restores the tty and the mint flow (browser +
        # token paste) is intentionally blocking — the wizard resumes when it returns.
        try:
            with self.app.suspend():
                profile = setup_token.mint(name, default=make_default)
        except FileNotFoundError:
            self._mint_msg = ("[red]the host `claude` CLI is not installed — install Claude "
                              "Code, then Re-check[/]")
        except KeyboardInterrupt:
            self._mint_msg = "[yellow]cancelled — Create profile retries, Continue skips[/]"
        except Exception as exc:  # noqa: BLE001 - RuntimeError from mint, SuspendNotSupported, …
            self._mint_msg = f"[red]profile create failed: {escape(str(exc))}[/]"
        else:
            account = f" ({profile.account_email})" if profile.account_email else ""
            self._mint_msg = (f"[green]profile {name!r} created{account}"
                              f"{' — set as default' if make_default else ''}[/]")
        self._render_step()

    # -- image step --------------------------------------------------------
    def _start_build(self) -> None:
        self._building = True
        self._render_step()
        self.query_one("#build-log", RichLog).write(
            f"building {config.image_tag(config.DEFAULT_OVERLAY)} …")
        self._build_worker()

    @work(thread=True, group="wizard-build", exclusive=True)
    def _build_worker(self) -> None:
        res = images.ensure_chain(
            config.DEFAULT_OVERLAY,
            on_line=lambda line: self.app.call_from_thread(self._build_line, line))
        self.app.call_from_thread(self._after_build, res)

    def _build_line(self, line: str) -> None:
        try:
            self.query_one("#build-log", RichLog).write(line)
        except Exception:  # noqa: BLE001 - the screen may already be dismissed
            pass

    def _after_build(self, res: images.BuildResult) -> None:
        self._building = False
        if res.ok:
            # Refresh the report so the image step re-renders as OK (and welcome, if revisited).
            self._recheck()
        else:
            self._render_step()
            self.query_one("#wizard-error", Label).update(escape(res.detail))
