"""docker stats NetIO: the pure argv renderer + ``parse_stats`` (the Network panel's Traffic source).

Dependency-free — the daemon wrapper ``container_net_io`` is exercised through its pure parts + a mocked
``runner._run``, exactly like the egress argv tests (no docker required).
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.docker import stats  # noqa: E402


class StatsArgvTest(unittest.TestCase):
    def test_no_stream_format_and_names(self) -> None:
        argv = stats.stats_argv(["claude-man-api", "claude-man-web"])
        self.assertEqual(argv[:4], ["docker", "stats", "--no-stream", "--format"])
        self.assertEqual(argv[4], "{{.Name}}\t{{.NetIO}}")
        self.assertEqual(argv[5:], ["claude-man-api", "claude-man-web"])


class ParseStatsTest(unittest.TestCase):
    SAMPLE = (
        "claude-man-api\t12.3MB / 4.1MB\n"
        "claude-man-web\t0B / 0B\n"
        "\n"                       # blank line skipped
        "no-tab-here 5MB\n"        # tab-less line skipped (not name<TAB>netio)
    )

    def test_name_to_netio_map_skips_junk(self) -> None:
        self.assertEqual(stats.parse_stats(self.SAMPLE), {
            "claude-man-api": "12.3MB / 4.1MB",
            "claude-man-web": "0B / 0B",
        })

    def test_empty(self) -> None:
        self.assertEqual(stats.parse_stats(""), {})


class ContainerNetIoTest(unittest.TestCase):
    def test_no_names_skips_docker_entirely(self) -> None:
        # An empty (or all-blank) name list must not shell out — there's nothing to stat.
        with mock.patch.object(stats.runner, "_run") as run:
            self.assertEqual(stats.container_net_io([""]), {})
            run.assert_not_called()

    def test_parses_stdout_on_success(self) -> None:
        cp = subprocess.CompletedProcess([], 0, "claude-man-api\t1MB / 2MB\n", "")
        with mock.patch.object(stats.runner, "_run", return_value=cp) as run:
            self.assertEqual(stats.container_net_io(["claude-man-api"]), {"claude-man-api": "1MB / 2MB"})
        # Must be time-bounded — a wedged daemon can't hang the repeating panel poll.
        self.assertIsNotNone(run.call_args.kwargs.get("timeout"))

    def test_nonzero_returns_empty(self) -> None:
        cp = subprocess.CompletedProcess([], 1, "", "no such container")
        with mock.patch.object(stats.runner, "_run", return_value=cp):
            self.assertEqual(stats.container_net_io(["gone"]), {})


if __name__ == "__main__":
    unittest.main()
