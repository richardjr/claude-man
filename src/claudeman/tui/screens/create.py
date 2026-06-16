"""New-project form (Phase 1; Language field added in Phase 6b).

A modal form collecting the five fields ``lifecycle.create_project`` accepts today —
slug, profile, overlay, language, egress. On submit it dismisses with those values; the app
then runs ``lifecycle.create_project`` off the UI thread (it writes the registry TOML, seeds
``claude-config/`` and ``docker create``s the hardened container). Repos / env / allowlist
are a later increment — see ROADMAP.md and the ``[[repos]]`` shape in registry/schema.py.

The slug is validated inline against the registry schema's regex and checked for duplicates
before submit, because argparse ``choices``/regex never run from the TUI (review SEC-6).

Language picks the pack tier whose defaults are applied at create (docs/PACKS.md). The options
are the library's discovered tiers; choosing an Overlay PRE-FILLS the matching tier as a
suggestion (e.g. the ``python`` overlay suggests the ``python`` tier) until the operator picks
a language themselves — the stored value is always the explicit selection.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select

from ... import config
from ...packs import library as packs_library
from ...registry import profiles as profiles_registry
from ...registry import projects
from ...registry.schema import _SLUG_RE

# What the screen hands back to the app: (slug, profile|None, overlay, egress, language),
# or None on cancel. language == "" means common-tier packs only.
NewProject = tuple[str, "str | None", str, str, str]

_SUGGESTION_CONSUMED = object()  # sentinel: no programmatic language echo pending

# Combo overlays aren't a pack tier of their own. Map them to the tier whose conventions matter most
# under the read-only floor for the language-suggestion pre-fill (python needs the venv/uv guidance;
# node works without special steering). The operator can still override the suggestion.
_OVERLAY_TIER_HINT = {"python-node": "python"}


class NewProjectScreen(ModalScreen["NewProject | None"]):
    """Collect slug/profile/overlay/egress/language for a new project.

    Dismisses with ``(slug, profile, overlay, egress, language)`` on Create (``profile`` is
    ``None`` when the operator keeps the default), or ``None`` on Cancel/Escape. The app owns
    the actual create call so the blocking ``docker create`` stays off the UI thread.
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
        # Language tiers discovered from the pack library — fail-soft: a malformed OR unreadable
        # library must not block creating a project (it just loses the language suggestion).
        # OSError too: discover() maps only pack.toml faults to LibraryError; iterdir/glob on an
        # unreadable tree raise raw OSError.
        try:
            self._tiers: tuple[str, ...] = tuple(
                t for t in packs_library.tiers() if t != packs_library.COMMON_TIER)
        except (packs_library.LibraryError, OSError):
            self._tiers = ()
        # Overlay→language pre-fill bookkeeping: ``_language_touched`` stops suggesting once the
        # operator picks a language themselves. A programmatic ``Select.value`` assignment echoes
        # back as a Changed message, so the pending suggestion is remembered and its one echo is
        # swallowed rather than mistaken for an operator pick.
        self._language_touched = False
        self._suggested: object = ""  # the initial Select.Changed echo carries ""

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
            yield Label("Language (pack tier — default packs at create)")
            yield Select(
                [("(none — common packs only)", "")] + [(t, t) for t in self._tiers],
                value="", allow_blank=False, id="language",
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

    @on(Select.Changed, "#overlay")
    def _suggest_language(self, event: Select.Changed) -> None:
        """Pre-fill the language from the overlay (``python`` overlay → ``python`` tier) while
        the operator hasn't picked one themselves."""
        if self._language_touched:
            return
        # Combo overlays aren't themselves a pack tier; map them to the tier whose conventions matter
        # most under the read-only floor (python needs the venv/uv guidance; node "just works").
        hint = _OVERLAY_TIER_HINT.get(event.value, event.value)
        suggestion = hint if hint in self._tiers else ""
        select = self.query_one("#language", Select)
        if select.value != suggestion:
            self._suggested = suggestion
            select.value = suggestion

    @on(Select.Changed, "#language")
    def _language_changed(self, event: Select.Changed) -> None:
        if event.value == self._suggested:
            self._suggested = _SUGGESTION_CONSUMED  # our own echo, not an operator pick
            return
        self._language_touched = True

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
        language = self.query_one("#language", Select).value
        self.dismiss((slug, profile, overlay, egress, language))
