"""Status helpers (pure — no docker daemon)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.docker import labels, status  # noqa: E402


class StatusStyleTest(unittest.TestCase):
    def test_status_style_colours(self) -> None:
        self.assertEqual(status.status_style(status.UP), "green")
        self.assertEqual(status.status_style(status.STOPPED), "red")
        self.assertEqual(status.status_style(status.DEFINED), "yellow")
        self.assertEqual(status.status_style("anything-else"), "yellow")  # safe default


def _cs(slug, state="running", **kw) -> status.ContainerStatus:
    return status.ContainerStatus(slug=slug, state=state, status_text=kw.pop("status_text", "Up 1m"), **kw)


class JoinTest(unittest.TestCase):
    """status.join is the invariant-4 registry<->docker reconciliation; pin its outer-join branches."""

    def test_defined_with_no_container(self) -> None:
        rows = status.join([("alpha", "work", "open", 2, "qwen3-coder:30b")], {})
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r.slug, r.kind, r.profile, r.egress, r.repos, r.version, r.model),
                         ("alpha", status.DEFINED, "work", "open", "2", "", "qwen3-coder:30b"))

    def test_running_clean_row_uses_container_state(self) -> None:
        cs = _cs("alpha", state="running", profile="work", egress="strict", repos="2", version="2.1.0")
        rows = status.join([("alpha", "work", "strict", 2, "qwen3-coder:30b")], {"alpha": cs})
        self.assertEqual(rows[0].kind, status.UP)
        self.assertEqual(rows[0].repos, "2")            # counts match -> no drift marker
        self.assertEqual(rows[0].version, "2.1.0")
        # model is registry-sourced (no docker label carries it) — it must flow onto the running row.
        self.assertEqual(rows[0].model, "qwen3-coder:30b")
        self.assertNotIn("stale", rows[0].status_text)

    def test_registry_wins_repo_count_with_drift_marker(self) -> None:
        # Container label says 1 repo; registry says 3 (a repo was added post-create, BUG-5).
        cs = _cs("alpha", repos="1", status_text="Up 2h")
        rows = status.join([("alpha", "work", "open", 3, "")], {"alpha": cs})
        self.assertEqual(rows[0].repos, "3*")           # registry count wins + '*' drift marker
        self.assertIn("repos label stale", rows[0].status_text)

    def test_orphan_container_is_surfaced(self) -> None:
        cs = _cs("ghost", state="exited", status_text="Exited (0) 1m ago")
        rows = status.join([], {"ghost": cs})
        self.assertEqual(rows[0].kind, status.STOPPED)
        self.assertIn("orphan", rows[0].status_text)
        self.assertEqual(rows[0].model, "")             # no registry entry -> no pin

    def test_rows_sorted_by_slug(self) -> None:
        rows = status.join([("zeta", "", "open", 0, ""), ("alpha", "", "open", 0, "")], {})
        self.assertEqual([r.slug for r in rows], ["alpha", "zeta"])


class ParseLabelCsvTest(unittest.TestCase):
    """`docker ps` flattens labels to a k=v CSV; the parser must survive the awkward cases."""

    def test_empty_value_and_missing_equals_and_filtering(self) -> None:
        # claude-man.repos with an empty value; a pair with no '=' (ignored); a non-claude-man key
        # (filtered by labels.parse).
        raw = "claude-man.slug=demo,claude-man.repos=,broken-pair,other.label=x"
        parsed = status._parse_label_csv(raw)
        self.assertEqual(parsed.get(labels.SLUG), "demo")
        self.assertEqual(parsed.get(labels.REPOS), "")     # empty value survives
        self.assertNotIn("other.label", parsed)            # non-claude-man key filtered out


if __name__ == "__main__":
    unittest.main()
