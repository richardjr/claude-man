"""Add-repo modal (Phase 3 TUI).

Mirrors ``NewProjectScreen``: a ``ModalScreen`` with inline validation that dismisses with a tuple the
app then runs off the UI thread (the host-side clone is slow). Collects ``url`` / ``branch`` / ``dir``
for a new repo on an existing project. Dir-collisions are caught inline against the project's existing
``resolved_dir()`` set; the URL is shape-checked (``repos.looks_like_repo_url``) — never a network probe,
so the modal stays instant. The real reachability check is the clone, surfaced in the app's log pane.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from ...checkout import repos as repos_mod
from ...registry.schema import Repo

# What the screen hands back: (url, branch, dir) — dir "" means "derive from the url" — or None.
AddRepo = tuple[str, str, str]


class AddRepoScreen(ModalScreen["AddRepo | None"]):
    """Collect url / branch / dir for a new repo on project ``slug``.

    ``existing_dirs`` is ``{repo.resolved_dir() for repo in project.repos}`` so a collision is caught
    before submit; the registry write + host clone happen in the app's thread worker via
    ``lifecycle.add_repo`` (this screen never mutates the registry itself).
    """

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    AddRepoScreen { align: center middle; }
    #dialog {
        width: 72; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    #repo-error { color: $error; height: auto; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str, existing_dirs: set[str]) -> None:
        super().__init__()
        self._slug = slug
        self._existing = existing_dirs

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Add repo to {self._slug}", classes="title")
            yield Label("Remote URL")
            yield Input(placeholder="git@github.com:org/repo.git  or  https://host/org/repo.git", id="url")
            yield Label("Branch")
            yield Input(value="main", id="branch")
            yield Label("Dir (workspace subdir — blank = derived from url)")
            yield Input(placeholder="derived from url", id="dir")
            yield Label("", id="repo-error")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Add", variant="success", id="add")

    def on_mount(self) -> None:
        self.query_one("#url", Input).focus()

    # -- handlers ---------------------------------------------------------
    @staticmethod
    def _derived_dir(url: str) -> str:
        try:
            return Repo(url=url.strip()).resolved_dir()
        except Exception:  # noqa: BLE001 - any malformed url just yields no preview
            return ""

    @on(Input.Changed)
    def _clear_error(self) -> None:
        # Any field edit clears the error — so fixing the dir/branch after a failed submit clears it too.
        self.query_one("#repo-error", Label).update("")

    @on(Input.Changed, "#url")
    def _update_preview(self) -> None:
        url = self.query_one("#url", Input).value.strip()
        self.query_one("#dir", Input).placeholder = self._derived_dir(url) or "derived from url"

    @on(Input.Submitted)
    @on(Button.Pressed, "#add")
    def _create(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        url = self.query_one("#url", Input).value.strip()
        branch = self.query_one("#branch", Input).value.strip() or "main"
        dir_ = self.query_one("#dir", Input).value.strip()
        err = self.query_one("#repo-error", Label)
        if not url:
            err.update("remote URL is required")
            return
        if not repos_mod.looks_like_repo_url(url):
            err.update("expected git@host:org/repo.git or https://host/org/repo[.git]")
            return
        effective = dir_ or self._derived_dir(url)
        if not effective:
            err.update("could not derive a dir from that url — set one explicitly")
            return
        if effective in self._existing:
            err.update(f"dir {effective!r} already exists in this project")
            return
        self.dismiss((url, branch, dir_))
