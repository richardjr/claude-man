"""New-project form (Phase 1).

A modal form collecting the four fields ``lifecycle.create_project`` accepts today —
slug, profile, overlay, egress. On submit it dismisses with those values; the app then
runs ``lifecycle.create_project`` off the UI thread (it writes the registry TOML, seeds
``claude-config/`` and ``docker create``s the hardened container). Repos / env / allowlist
are a later increment — see ROADMAP.md and the ``[[repos]]`` shape in registry/schema.py.

The slug is validated inline against the registry schema's regex and checked for duplicates
before submit, because argparse ``choices``/regex never run from the TUI (review SEC-6).
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select

from ... import config
from ...registry import profiles as profiles_registry
from ...registry import projects
from ...registry.schema import _SLUG_RE

# What the screen hands back to the app: (slug, profile|None, overlay, egress), or None on cancel.
NewProject = tuple[str, "str | None", str, str]


class NewProjectScreen(ModalScreen["NewProject | None"]):
    """Collect slug/profile/overlay/egress for a new project.

    Dismisses with ``(slug, profile, overlay, egress)`` on Create (``profile`` is ``None``
    when the operator keeps the default), or ``None`` on Cancel/Escape. The app owns the
    actual create call so the blocking ``docker create`` stays off the UI thread.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    NewProjectScreen { align: center middle; }
    #dialog {
        width: 64; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    #slug-error { color: $error; height: auto; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self) -> None:
        super().__init__()
        default = profiles_registry.default_profile()
        default_label = f"default: {default.name}" if default else "default (none defined)"
        # "" == inherit the default profile; explicit names follow.
        self._profile_options: list[tuple[str, str]] = [(f"({default_label})", "")]
        self._profile_options += [(name, name) for name in profiles_registry.list_names()]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("New project", classes="title")
            yield Label("Slug")
            yield Input(placeholder="lowercase, digits, hyphens (e.g. landarna-api)", id="slug")
            yield Label("", id="slug-error")
            yield Label("Profile (account)")
            yield Select(self._profile_options, value="", allow_blank=False, id="profile")
            yield Label("Overlay (image)")
            yield Select(
                [(o, o) for o in config.OVERLAYS],
                value=config.DEFAULT_OVERLAY, allow_blank=False, id="overlay",
            )
            yield Label("Egress")
            yield Select(
                [(e, e) for e in config.EGRESS_MODES],
                value=config.DEFAULT_EGRESS, allow_blank=False, id="egress",
            )
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Create", variant="success", id="create")

    def on_mount(self) -> None:
        self.query_one("#slug", Input).focus()

    # -- handlers ---------------------------------------------------------
    @on(Input.Changed, "#slug")
    def _clear_error(self) -> None:
        self.query_one("#slug-error", Label).update("")

    @on(Input.Submitted, "#slug")
    @on(Button.Pressed, "#create")
    def _create(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        slug = self.query_one("#slug", Input).value.strip()
        err = self.query_one("#slug-error", Label)
        if not slug:
            err.update("slug is required")
            return
        if not _SLUG_RE.match(slug):
            err.update("lowercase letters/digits/hyphens only, ≤ 64 chars, no leading '-'")
            return
        if projects.exists(slug):
            err.update(f"project {slug!r} already exists")
            return
        profile = self.query_one("#profile", Select).value or None
        overlay = self.query_one("#overlay", Select).value
        egress = self.query_one("#egress", Select).value
        self.dismiss((slug, profile, overlay, egress))
