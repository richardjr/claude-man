"""Remove-repo modal (Phase 3 TUI).

Pick one of the project's repos to drop from the registry. Registry-only by default (the on-disk
checkout can hold unsynced agent work — "persistence is the default, deletion is explicit"); a
deliberately unchecked ``purge`` checkbox offers the destructive ``rm -rf`` of the checkout. The app
runs the removal off the UI thread via ``lifecycle.remove_repo`` — this screen never mutates anything.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label, Select

# What the screen hands back: (resolved_dir, purge) or None on cancel.
RemoveRepo = tuple[str, bool]


class RemoveRepoScreen(ModalScreen["RemoveRepo | None"]):
    """Select a repo (by its workspace dir) to remove from ``slug``; optional ``--purge``.

    ``dirs`` is the project's ``[repo.resolved_dir() for repo in project.repos]`` (the app passes a
    non-empty list — it guards the empty case before pushing this screen).
    """

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    RemoveRepoScreen { align: center middle; }
    #dialog {
        width: 64; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $error; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #dialog Label { color: $text-muted; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, slug: str, dirs: list[str]) -> None:
        super().__init__()
        self._slug = slug
        self._dirs = dirs

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Remove a repo from {self._slug}", classes="title")
            yield Label("Repo (workspace dir)")
            yield Select([(d, d) for d in self._dirs], value=self._dirs[0],
                         allow_blank=False, id="repo")
            yield Checkbox("also delete the on-disk checkout (--purge)", value=False, id="purge")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Remove", variant="error", id="remove")

    # -- handlers ---------------------------------------------------------
    @on(Button.Pressed, "#remove")
    def _remove(self) -> None:
        dir_ = self.query_one("#repo", Select).value
        purge = self.query_one("#purge", Checkbox).value
        self.dismiss((dir_, purge))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
