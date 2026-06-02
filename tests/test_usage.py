"""Token-usage transcript parsing (dependency-free)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config, usage  # noqa: E402


class UsageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        self.state.cleanup()

    def test_project_usage_sums_assistant_tokens(self) -> None:
        d = config.claude_config_dir("p") / "projects" / "-workspace"
        d.mkdir(parents=True)
        lines = [
            {"type": "assistant", "message": {"usage": {
                "input_tokens": 10, "output_tokens": 5,
                "cache_creation_input_tokens": 2, "cache_read_input_tokens": 100}}},
            {"type": "user", "message": {"content": "hi"}},          # ignored (not assistant)
            {"type": "assistant", "message": {"usage": {"input_tokens": 20, "output_tokens": 7}}},
            "not json at all",                                        # ignored (bad line)
            {"type": "assistant", "message": {"role": "assistant"}},  # ignored (no usage)
        ]
        with open(d / "s1.jsonl", "w") as fh:
            for item in lines:
                fh.write((item if isinstance(item, str) else json.dumps(item)) + "\n")
        u = usage.project_usage("p")
        self.assertEqual(u.input, 30)
        self.assertEqual(u.output, 12)
        self.assertEqual(u.cache_creation, 2)
        self.assertEqual(u.cache_read, 100)
        self.assertEqual(u.messages, 2)
        self.assertEqual(u.sessions, 1)
        self.assertEqual(u.total, 30 + 12 + 2 + 100)

    def test_empty_project_is_zero(self) -> None:
        u = usage.project_usage("nonexistent")
        self.assertEqual(u.total, 0)
        self.assertEqual(u.sessions, 0)

    def test_human(self) -> None:
        self.assertEqual(usage.human(999), "999")
        self.assertEqual(usage.human(12345), "12.3k")
        self.assertEqual(usage.human(2_500_000), "2.5M")


if __name__ == "__main__":
    unittest.main()
