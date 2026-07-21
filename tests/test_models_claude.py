"""Curated claude ``--model`` table + the raw-ref disambiguator (dependency-free)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.models import claude_models  # noqa: E402


class LintTest(unittest.TestCase):
    def test_shipped_table_is_clean(self) -> None:
        self.assertEqual(claude_models.lint_claude_models(), [])

    def test_fable_is_surfaced(self) -> None:
        # The premium tier is the reason the pin exists (the picker gates it under token auth) —
        # it must stay a curated row, not a raw-input-only ref.
        self.assertIn("claude-fable-5", [m.ref for m in claude_models.CLAUDE_MODELS])


class IsClaudeRefTest(unittest.TestCase):
    def test_full_ids_are_claude(self) -> None:
        for ref in ("claude-fable-5", "claude-opus-4-8", "claude-sonnet-5[1m]"):
            self.assertTrue(claude_models.is_claude_ref(ref), ref)

    def test_bare_aliases_are_claude(self) -> None:
        for ref in ("opus", "sonnet", "haiku", "fable", "opusplan", "OPUS"):
            self.assertTrue(claude_models.is_claude_ref(ref), ref)

    def test_ollama_tags_are_not(self) -> None:
        # A colon always marks an ollama name:tag — even with a claude-ish name.
        for ref in ("qwen3-coder:30b", "devstral", "claude-fake:7b", "gpt-oss:20b"):
            self.assertFalse(claude_models.is_claude_ref(ref), ref)


class RegexTwinTest(unittest.TestCase):
    def test_ref_regex_matches_the_enforcing_schema_regex(self) -> None:
        # claude_models._REF_RE is a purity-preserving copy of the regex that actually gates pins
        # (schema._CLAUDE_MODEL_RE via Project.__post_init__). If they drift, a lint-clean curated
        # row could be rejected by set_claude_model at pick time — pin them identical here.
        from claudeman.registry import schema
        self.assertEqual(claude_models._REF_RE.pattern, schema._CLAUDE_MODEL_RE.pattern)
        self.assertEqual(claude_models._REF_RE.flags, schema._CLAUDE_MODEL_RE.flags)


if __name__ == "__main__":
    unittest.main()
