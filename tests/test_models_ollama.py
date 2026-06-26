"""Pure parsers of the Ollama backend (Phase 9 — issue #14). No daemon, no sockets — the parse/render
split from urllib IO is exercised with literal JSON fixtures from the Ollama API docs."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.models import ollama  # noqa: E402
from claudeman.models.base import PullEvent  # noqa: E402


class ParseTagsTest(unittest.TestCase):
    SAMPLE = {
        "models": [
            {
                "name": "qwen3-coder:30b", "model": "qwen3-coder:30b",
                "modified_at": "2026-01-02T08:06:48.6-07:00", "size": 19000000000,
                "digest": "sha256:0a8c266910232fd3291e71e5ba1e058cc5af9d411192cf88b6d30e92b6e73163",
                "details": {"family": "qwen3moe", "parameter_size": "30.5B", "quantization_level": "Q4_K_M"},
            },
            {"name": "", "digest": "x"},          # nameless -> dropped
            "garbage",                              # non-dict -> skipped
        ]
    }

    def test_parses_and_drops_invalid(self) -> None:
        models = ollama.parse_tags(self.SAMPLE)
        self.assertEqual(len(models), 1)
        m = models[0]
        self.assertEqual(m.name, "qwen3-coder:30b")
        self.assertEqual(m.size, 19000000000)
        self.assertEqual(m.family, "qwen3moe")
        self.assertEqual(m.param_size, "30.5B")
        self.assertEqual(m.quant, "Q4_K_M")
        # digest is normalised (sha256: prefix stripped) for registry comparison
        self.assertEqual(m.digest, "0a8c266910232fd3291e71e5ba1e058cc5af9d411192cf88b6d30e92b6e73163")

    def test_empty_and_garbage(self) -> None:
        self.assertEqual(ollama.parse_tags({}), [])
        self.assertEqual(ollama.parse_tags("nope"), [])
        self.assertEqual(ollama.parse_tags({"models": None}), [])


class ParsePullLineTest(unittest.TestCase):
    def test_status_kinds(self) -> None:
        cases = {
            "pulling manifest": "manifest",
            "verifying sha256 digest": "verifying",
            "writing manifest": "writing",            # must NOT be mistaken for 'manifest'
            "removing any unused layers": "removing",
            "success": "success",
        }
        for status, kind in cases.items():
            self.assertEqual(ollama.parse_pull_line({"status": status}).kind, kind, status)

    def test_layer_line_carries_bytes(self) -> None:
        ev = ollama.parse_pull_line(
            {"status": "pulling 0a8c", "digest": "sha256:0a8c", "total": 2142590208, "completed": 241970})
        self.assertEqual(ev.kind, "layer")
        self.assertEqual(ev.digest, "0a8c")           # normalised
        self.assertEqual((ev.total, ev.completed), (2142590208, 241970))

    def test_error_line(self) -> None:
        self.assertEqual(ollama.parse_pull_line({"error": "model not found"}).kind, "error")
        self.assertEqual(ollama.parse_pull_line("notadict").kind, "error")


class AggregateProgressTest(unittest.TestCase):
    def test_sums_latest_per_layer(self) -> None:
        events = [
            PullEvent(kind="manifest", status="pulling manifest"),
            PullEvent(kind="layer", digest="a", total=100, completed=50),
            PullEvent(kind="layer", digest="b", total=100, completed=0),
            PullEvent(kind="layer", digest="a", total=100, completed=100),   # latest for 'a' wins
        ]
        self.assertAlmostEqual(ollama.aggregate_pull_progress(events), 50.0)  # (100+0)/(100+100)

    def test_no_layers_is_zero(self) -> None:
        self.assertEqual(ollama.aggregate_pull_progress([PullEvent(kind="manifest")]), 0.0)


class ParseShowTest(unittest.TestCase):
    SAMPLE = {
        "details": {"family": "qwen3moe", "parameter_size": "30.5B", "quantization_level": "Q4_K_M"},
        "model_info": {"general.architecture": "qwen3moe", "qwen3moe.context_length": 262144,
                       "qwen3moe.embedding_length": 4096},
        "capabilities": ["completion", "tools"],
    }

    def test_extracts_context_and_caps(self) -> None:
        info = ollama.parse_show(self.SAMPLE, name="qwen3-coder:30b")
        self.assertEqual(info.context_length, 262144)
        self.assertEqual(info.capabilities, ("completion", "tools"))
        self.assertEqual(info.family, "qwen3moe")
        self.assertEqual(info.quant, "Q4_K_M")

    def test_missing_fields_safe(self) -> None:
        info = ollama.parse_show({}, name="x")
        self.assertEqual((info.context_length, info.capabilities, info.name), (0, (), "x"))


class SplitRefTest(unittest.TestCase):
    def test_library_default_tag(self) -> None:
        self.assertEqual(ollama.split_ref("qwen3-coder"), ("library", "qwen3-coder", "latest"))

    def test_explicit_tag(self) -> None:
        self.assertEqual(ollama.split_ref("qwen3-coder:30b"), ("library", "qwen3-coder", "30b"))

    def test_namespaced(self) -> None:
        self.assertEqual(ollama.split_ref("user/model:tag"), ("user", "model", "tag"))


class UpdateVerdictTest(unittest.TestCase):
    def test_behind_when_digests_differ(self) -> None:
        st = ollama.update_verdict("m", "aaa", "sha256:bbb")
        self.assertTrue(st.behind)

    def test_current_when_equal(self) -> None:
        st = ollama.update_verdict("m", "sha256:aaa", "aaa")   # normalised, equal
        self.assertFalse(st.behind)

    def test_note_means_unknown_never_behind(self) -> None:
        st = ollama.update_verdict("m", "aaa", "", note="offline")
        self.assertFalse(st.behind)        # fail-open: unknown is never reported as behind
        self.assertEqual(st.note, "offline")


if __name__ == "__main__":
    unittest.main()
