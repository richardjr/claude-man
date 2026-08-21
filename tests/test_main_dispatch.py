"""`claudeman <group>` CLI dispatch — `__main__._CTL_GROUPS` must cover every top-level
claudemanctl group/verb, or that group silently launches the TUI instead of the CLI (the
pre-fix bug: `claudeman config show` opened the TUI because `config`/`packs`/`model` were
missing from the set). Dependency-free — only argparse introspection."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import __main__ as main_mod  # noqa: E402
from claudeman import cli  # noqa: E402


class CtlGroupsParityTest(unittest.TestCase):
    def _parser_groups(self) -> set[str]:
        parser = cli.build_parser()
        sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        return set(sub.choices)

    def test_ctl_groups_match_the_real_parser(self) -> None:
        # Exact equality both ways: a new CLI group must be added to _CTL_GROUPS (else it
        # launches the TUI), and a removed one must be dropped (else dead dispatch).
        self.assertEqual(main_mod._CTL_GROUPS, self._parser_groups())

    def test_doctor_is_dispatchable(self) -> None:
        self.assertIn("doctor", main_mod._CTL_GROUPS)


if __name__ == "__main__":
    unittest.main()
