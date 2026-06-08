"""Status helpers (pure — no docker daemon)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.docker import status  # noqa: E402


class StatusStyleTest(unittest.TestCase):
    def test_status_style_colours(self) -> None:
        self.assertEqual(status.status_style(status.UP), "green")
        self.assertEqual(status.status_style(status.STOPPED), "red")
        self.assertEqual(status.status_style(status.DEFINED), "yellow")
        self.assertEqual(status.status_style("anything-else"), "yellow")  # safe default


if __name__ == "__main__":
    unittest.main()
