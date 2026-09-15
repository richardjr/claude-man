"""Tools-screen view model — rows, pulled-in marks, toggle/changed semantics, the summary line.
Pure (no textual import)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.tools import library  # noqa: E402
from claudeman.tui import toolsview  # noqa: E402


def _tool(name: str, *, requires: tuple[str, ...] = (), note: str = "") -> library.Tool:
    return library.Tool(name=name, description=f"{name} tool", kind="apt", path=Path("/x"),
                        packages=(name,), requires=requires, note=note)


LIB = {t.name: t for t in (_tool("jq"), _tool("python3"),
                            _tool("python3-yaml", requires=("python3",), note="needs python3"))}


class RowsTest(unittest.TestCase):
    def test_rows_mark_selection_and_pulled_in(self) -> None:
        rows = toolsview.rows(LIB, ("python3-yaml",))
        by = {r.name: r for r in rows}
        self.assertEqual([r.name for r in rows], ["jq", "python3", "python3-yaml"])
        self.assertTrue(by["python3-yaml"].selected)
        self.assertFalse(by["python3"].selected)
        self.assertTrue(by["python3"].pulled_in)        # required by the selected tool
        self.assertFalse(by["jq"].pulled_in)
        self.assertEqual(by["python3-yaml"].requires, "python3")
        self.assertIn("[needs python3]", by["python3-yaml"].description)
        self.assertEqual(by["jq"].source, "apt: jq")

    def test_unknown_selection_entry_stays_visible(self) -> None:
        rows = toolsview.rows(LIB, ("ghost", "jq"))
        ghost = rows[-1]
        self.assertEqual((ghost.name, ghost.selected, ghost.source), ("ghost", True, "?"))
        self.assertIn("not in the registry", ghost.description)
        self.assertTrue({r.name for r in rows} >= {"jq", "python3"})  # library rows still there

    def test_toggle_and_changed(self) -> None:
        self.assertEqual(toolsview.toggled(("jq",), "python3"), ("jq", "python3"))
        self.assertEqual(toolsview.toggled(("jq", "python3"), "jq"), ("python3",))
        self.assertFalse(toolsview.changed(("a", "b"), ("b", "a")))   # order-insensitive
        self.assertTrue(toolsview.changed(("a",), ("a", "b")))
        self.assertTrue(toolsview.changed(("a",), ()))

    def test_summary(self) -> None:
        self.assertIn("no tools selected", toolsview.summary(LIB, ()))
        self.assertEqual(toolsview.summary(LIB, ("jq",)), "bakes: jq")
        self.assertEqual(toolsview.summary(LIB, ("python3-yaml",)),
                         "bakes: python3, python3-yaml  (+python3 pulled in by requires)")
        self.assertIn("cannot render", toolsview.summary(LIB, ("ghost",)))


if __name__ == "__main__":
    unittest.main()
