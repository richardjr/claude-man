"""Models screen — manage local models (Models, ``m`` key; Phase 9 — issue #14).

Global model management (not per-project): lists the models installed in the host Ollama daemon and
installs / updates / removes / inspects them, plus an ``AddModelScreen`` picker over the curated coding
presets. Mirrors ``SettingsScreen`` — a ModalScreen with its own hotkeys + a status Label, and every
daemon-touching call (list / pull-stream / remove / show) on a thread worker so the UI never blocks; the
streamed pull paints an aggregate-percent line via ``call_from_thread``. Fails open: with no daemon the
table shows an actionable hint instead of an error.

The Phase-9 gateway/hybrid-mode wiring (a project actually USING a local model) is the follow-on; this
screen is the management surface the framework already supports.
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ItemGrid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label

from ...models import get_backend, presets
from ...models.ollama import aggregate_pull_progress

_COLUMNS = ("Model", "Size", "Params", "Quant")
_PRESET_COLUMNS = ("Key", "Tag", "VRAM", "Notes")


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f}GB" if n else "-"


class AddModelScreen(ModalScreen["str | None"]):
    """Pick a curated preset (or type a raw ``name:tag``) to install. Dismisses the resolved ollama tag,
    or ``None`` on cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("enter", "install", "Install")]
    CSS = """
    AddModelScreen { align: center middle; }
    #dialog {
        width: 88; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #presets { height: auto; max-height: 12; }
    #add-note { height: auto; color: $text-muted; padding-top: 1; }
    #raw { margin-top: 1; }
    #buttons { height: auto; padding-top: 1; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Install a model · curated coding presets", classes="title")
            yield DataTable(id="presets", cursor_type="row")
            yield Label("Enter installs the selected preset — or type any raw ollama tag below.",
                        id="add-note")
            yield Input(placeholder="raw ollama tag, e.g. qwen3-coder:30b (overrides the selection)",
                        id="raw")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Install", variant="success", id="install")

    def on_mount(self) -> None:
        table = self.query_one("#presets", DataTable)
        table.add_columns(*_PRESET_COLUMNS)
        for p in presets.PRESETS:
            label = f"{p.key}{' ★' if p.default else ''}"
            table.add_row(label, p.tag, f"{p.vram_gb}GB", p.note, key=p.key)

    def _chosen(self) -> str | None:
        raw = self.query_one("#raw", Input).value.strip()
        if raw:
            return raw
        table = self.query_one("#presets", DataTable)
        if table.row_count == 0:
            return None
        try:
            key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:
            return None
        p = presets.resolve_preset(key)
        return p.tag if p else None

    @on(Button.Pressed, "#install")
    def action_install(self) -> None:
        ref = self._chosen()
        self.dismiss(ref or None)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ModelsScreen(ModalScreen[None]):
    """View + manage local models installed in the host Ollama daemon."""

    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("u", "update", "Update"),
        Binding("x", "remove", "Remove"),
        Binding("s", "show", "Show"),
        Binding("r", "refresh", "Refresh"),
        Binding("escape", "close", "Close"),
    ]
    CSS = """
    ModelsScreen { align: center middle; }
    #dialog {
        width: 92; height: auto; max-height: 90%;
        padding: 1 2; overflow-y: auto;
        border: round $primary; background: $surface;
    }
    #dialog .title { text-style: bold; padding-bottom: 1; }
    #models { height: auto; max-height: 14; }
    #models-status { height: auto; color: $text-muted; padding-top: 1; }
    /* ItemGrid wraps the action buttons onto rows instead of cropping them — CLAUDE.md
       "TUI dialog button rows" (reflow, no crop). */
    #buttons { height: auto; padding-top: 1; grid-gutter: 0 1; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._backend = get_backend()
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Models · local (host Ollama)", classes="title")
            yield DataTable(id="models", cursor_type="row")
            yield Label("a Add · u Update · x Remove · s Show · r Refresh · esc Close", id="models-status")
            with ItemGrid(id="buttons", min_column_width=16):
                yield Button("Add", variant="success", id="add")
                yield Button("Update", id="update")
                yield Button("Remove", variant="error", id="remove")
                yield Button("Show", id="show")
                yield Button("Refresh", id="refresh")
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#models", DataTable).add_columns(*_COLUMNS)
        self.action_refresh()

    # -- helpers ----------------------------------------------------------
    def _set_status(self, text: str) -> None:
        self.query_one("#models-status", Label).update(text)

    def _selected(self) -> str | None:
        table = self.query_one("#models", DataTable)
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:
            return None

    def _paint(self, models, note: str) -> None:
        table = self.query_one("#models", DataTable)
        table.clear()
        if note:
            table.add_row(f"(ollama: {note})", "", "", "")
            self._set_status("install + run Ollama on the host (127.0.0.1:11434) — see docs/MODELS.md")
            return
        if not models:
            table.add_row("(no models — press 'a' to install one)", "", "", "")
            return
        for m in models:
            table.add_row(m.name, _gb(m.size), m.param_size or "-", m.quant or "-", key=m.name)

    # -- refresh ----------------------------------------------------------
    @work(thread=True, exclusive=True, group="models-list")
    def action_refresh(self) -> None:
        models, note = self._backend.list_models()
        self.app.call_from_thread(self._paint, models, note)

    # -- add / pull -------------------------------------------------------
    @on(Button.Pressed, "#add")
    def action_add(self) -> None:
        if self._busy:
            return
        self.app.push_screen(AddModelScreen(), self._on_add)

    def _on_add(self, ref) -> None:
        if not ref:
            return
        self._start_pull(ref)

    def _start_pull(self, ref: str) -> None:
        self._busy = True
        self._set_status(f"pulling {ref} …")
        self._pull_worker(ref)

    @work(thread=True, group="models-pull")
    def _pull_worker(self, ref: str) -> None:
        events = []
        last_pct = {"v": -1}

        def on_progress(ev) -> None:
            events.append(ev)
            if ev.kind == "layer":
                pct = int(aggregate_pull_progress(events))
                if pct != last_pct["v"]:           # throttle: only repaint on a percent change
                    last_pct["v"] = pct
                    self.app.call_from_thread(self._set_status, f"pulling {ref} … {pct}%")
            elif ev.kind in ("manifest", "verifying", "writing", "removing"):
                self.app.call_from_thread(self._set_status, f"{ref}: {ev.status} …")

        term = self._backend.pull(ref, on_progress=on_progress)
        self.app.call_from_thread(self._after_pull, ref, term)

    def _after_pull(self, ref: str, term) -> None:
        self._busy = False
        if term.kind == "success":
            self._set_status(f"{ref}: installed")
        else:
            self._set_status(f"{ref}: pull failed — {term.status}")
        self.action_refresh()

    # -- update (re-pull, incremental) ------------------------------------
    @on(Button.Pressed, "#update")
    def action_update(self) -> None:
        if self._busy:
            return
        ref = self._selected()
        if not ref:
            self._set_status("no model selected")
            return
        self._set_status(f"updating {ref} …")
        self._start_pull(ref)

    # -- remove -----------------------------------------------------------
    @on(Button.Pressed, "#remove")
    def action_remove(self) -> None:
        if self._busy:
            return
        ref = self._selected()
        if not ref:
            self._set_status("no model selected")
            return
        self._set_status(f"removing {ref} …")
        self._remove_worker(ref)

    @work(thread=True, group="models-rm")
    def _remove_worker(self, ref: str) -> None:
        ok, note = self._backend.remove(ref)
        self.app.call_from_thread(self._after_remove, ref, ok, note)

    def _after_remove(self, ref: str, ok: bool, note: str) -> None:
        self._set_status(f"{ref}: {note or 'removed'}" if ok else f"{ref}: {note}")
        self.action_refresh()

    # -- show -------------------------------------------------------------
    @on(Button.Pressed, "#show")
    def action_show(self) -> None:
        ref = self._selected()
        if not ref:
            self._set_status("no model selected")
            return
        self._show_worker(ref)

    @work(thread=True, group="models-show")
    def _show_worker(self, ref: str) -> None:
        info = self._backend.show(ref)
        self.app.call_from_thread(self._after_show, info)

    def _after_show(self, info) -> None:
        if info.note:
            self._set_status(f"{info.name}: {info.note}")
            return
        caps = ", ".join(info.capabilities) or "-"
        tools = "" if "tools" in info.capabilities else "  ⚠ no 'tools' capability (agentic use limited)"
        self._set_status(
            f"{info.name} · ctx {info.context_length or '?'} · {info.param_size or '?'} · "
            f"{info.quant or '?'} · caps: {caps}{tools}"
        )

    @on(Button.Pressed, "#refresh")
    def _btn_refresh(self) -> None:
        self.action_refresh()

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)
