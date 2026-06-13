"""Phase 4 strict egress: allowlist, squid.conf render, sidecar/network argv, the agent's additive
strict flags (floor must stay byte-identical), the denied-log parser, and the lock-smoke verdict.

All dependency-free (no docker, no network) — the daemon wrappers in network/egress.py are exercised
only through their PURE argv renderers, exactly like docker/runner's create-argv tests.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402
from claudeman.docker import runner  # noqa: E402
from claudeman.network import allowlist, egress, squid  # noqa: E402
from claudeman.registry.schema import Project  # noqa: E402


def _contains_sublist(big: list, small: list) -> bool:
    n = len(small)
    return any(big[i:i + n] == small for i in range(len(big) - n + 1))


class AllowlistTest(unittest.TestCase):
    def test_claude_ai_always_present(self) -> None:
        # OAuth refresh path — invariant 3 says it must always be allowed or token refresh fails.
        self.assertIn("claude.ai", allowlist.build_allowlist())

    def test_base_covers_anthropic_github_npm_apt(self) -> None:
        al = allowlist.build_allowlist()
        for host in (".anthropic.com", ".github.com", ".githubusercontent.com",
                     "registry.npmjs.org", "registry.yarnpkg.com", "deb.debian.org", "pypi.org"):
            self.assertIn(host, al)

    def test_no_bare_host_overlaps_a_wildcard_in_base(self) -> None:
        # squid FATALs/warns if a single dstdomain ACL has a bare host covered by a leading-dot
        # wildcard (e.g. api.anthropic.com under .anthropic.com) — so the rendered set must have none.
        al = allowlist.build_allowlist()
        wildcards = [h for h in al if h.startswith(".")]
        for h in al:
            if not h.startswith("."):
                for w in wildcards:
                    self.assertFalse(h == w[1:] or h.endswith(w),
                                     f"{h!r} overlaps wildcard {w!r} — squid would reject the config")

    def test_extra_subdomain_of_wildcard_is_dropped(self) -> None:
        # An operator extra already covered by a base wildcard must be dropped (else squid rejects it).
        al = allowlist.build_allowlist(("api.github.com", "internal.example.com"))
        self.assertNotIn("api.github.com", al)                  # covered by .github.com -> dropped
        self.assertIn("internal.example.com", al)               # genuinely new -> kept

    def test_over_broad_and_malformed_entries_rejected(self) -> None:
        # The whole point of the firewall: a hand-edited allow-all (`.`/`*`) or junk must NOT widen
        # egress — invalid entries are dropped (fail-closed), never rendered as an allow rule.
        al = allowlist.build_allowlist((".", "*", "com", ".com", "host:443", "http://x/y", "a b"))
        for bad in (".", "*", "com", ".com", "host:443", "http://x/y", "a b"):
            self.assertNotIn(bad, al)
        # ...while the legitimate base set is untouched.
        self.assertIn("claude.ai", al)

    def test_extras_appended_and_deduped_order_preserved(self) -> None:
        al = allowlist.build_allowlist(("internal.example.com", "claude.ai", "internal.example.com"))
        self.assertEqual(al[-1], "internal.example.com")        # extra appended once, after the base set
        self.assertEqual(al.count("internal.example.com"), 1)   # de-duplicated
        self.assertEqual(al.count("claude.ai"), 1)              # a dup of a base host doesn't re-add

    def test_blank_extras_dropped(self) -> None:
        self.assertNotIn("", allowlist.build_allowlist((" ", "")))


class SquidConfTest(unittest.TestCase):
    def setUp(self) -> None:
        self.al = allowlist.build_allowlist(("internal.example.com",))
        self.conf = squid.render_squid_conf(self.al)

    def test_port_and_one_acl_per_host(self) -> None:
        self.assertIn(f"http_port {config.PROXY_PORT}", self.conf)
        for host in self.al:
            self.assertIn(f"acl allowed_dst dstdomain {host}", self.conf)

    def test_connect_tunnel_no_mitm_and_default_deny(self) -> None:
        # CONNECT-only TLS (no ssl_bump / CA = no MITM), allow allowlisted, then deny all.
        self.assertIn("acl CONNECT method CONNECT", self.conf)
        self.assertNotIn("ssl_bump", self.conf)
        self.assertIn("http_access allow allowed_dst", self.conf)
        self.assertIn("http_access deny all", self.conf)
        # allow must come before the final deny-all, or nothing would ever be permitted.
        self.assertLess(self.conf.index("http_access allow allowed_dst"),
                        self.conf.rindex("http_access deny all"))

    def test_logs_to_proxy_owned_file(self) -> None:
        # squid drops to the 'proxy' user and can't write /dev/stdout, so it logs to a proxy-owned
        # file; the image entrypoint tails it to the container stdout for `docker logs` / egress-log.
        self.assertIn("access_log /var/log/squid/access.log", self.conf)
        # The directive that FATAL'd squid (logging to /dev/*) must be gone — check the directives, not
        # the explanatory comments (which legitimately mention /dev/stdout).
        for line in self.conf.splitlines():
            if line.startswith(("access_log", "cache_log")):
                self.assertNotIn("/dev/", line)

    def test_ssrf_denies_localhost_and_linklocal_before_allow(self) -> None:
        # Defence-in-depth: loopback + link-local (cloud metadata 169.254.169.254) denied even if the
        # allowlist is ever mis-set — and BEFORE the allow rule so it always wins.
        self.assertIn("acl to_linklocal dst 169.254.0.0/16", self.conf)
        self.assertIn("http_access deny to_localhost", self.conf)
        self.assertIn("http_access deny to_linklocal", self.conf)
        self.assertLess(self.conf.index("http_access deny to_linklocal"),
                        self.conf.index("http_access allow allowed_dst"))


class EgressArgvTest(unittest.TestCase):
    def test_network_is_internal_and_labelled(self) -> None:
        argv = egress.network_create_argv("demo")
        self.assertEqual(argv[:4], ["docker", "network", "create", "--internal"])
        self.assertIn(config.egress_net_name("demo"), argv)
        self.assertTrue(_contains_sublist(argv, ["--label", "claude-man.slug=demo"]))
        self.assertTrue(_contains_sublist(argv, ["--label", "claude-man.role=egress-net"]))

    def test_proxy_run_minimal_hardening_and_ro_conf(self) -> None:
        argv = egress.proxy_run_argv("demo", conf_path="/state/demo/squid.conf")
        self.assertEqual(argv[:3], ["docker", "run", "-d"])
        self.assertTrue(_contains_sublist(argv, ["--name", config.proxy_container_name("demo")]))
        self.assertTrue(_contains_sublist(argv, ["--network", config.egress_net_name("demo")]))
        self.assertTrue(_contains_sublist(argv, ["--security-opt", "no-new-privileges"]))
        # The rendered config is bind-mounted READ-ONLY (the proxy never trusts a writable config).
        self.assertTrue(_contains_sublist(
            argv, ["-v", "/state/demo/squid.conf:/etc/squid/squid.conf:ro"]))
        self.assertEqual(argv[-1], config.image_tag(config.PROXY_IMAGE))

    def test_bridge_connect(self) -> None:
        self.assertEqual(
            egress.bridge_connect_argv("demo"),
            ["docker", "network", "connect", "bridge", config.proxy_container_name("demo")],
        )


class EnsureNetworkInternalTest(unittest.TestCase):
    """ensure_network must verify the --internal flag, not just existence — a non-internal collision
    is a silent floor bypass (the agent would get a direct route out while showing Egress=strict)."""

    def _fake_run(self, responses):
        # responses: list of (match_substr, returncode, stdout) consumed by command shape.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            joined = " ".join(argv)
            for sub, rc, out in responses:
                if sub in joined:
                    return subprocess.CompletedProcess(argv, rc, out, "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        return run, calls

    def test_present_and_internal_is_reused(self) -> None:
        run, calls = self._fake_run([("network inspect", 0, "true")])
        with mock.patch.object(egress.runner, "_run", run):
            res = egress.ensure_network("demo")
        self.assertTrue(res.ok)
        self.assertFalse(any("network create" in " ".join(c) for c in calls))  # reused, not recreated

    def test_non_internal_collision_is_removed_and_recreated(self) -> None:
        run, calls = self._fake_run([("network inspect", 0, "false"), ("network rm", 0, "")])
        with mock.patch.object(egress.runner, "_run", run):
            res = egress.ensure_network("demo")
        self.assertTrue(res.ok)
        self.assertTrue(any("network rm" in " ".join(c) for c in calls))      # leaky net removed
        self.assertTrue(any("network create" in " ".join(c) for c in calls))  # recreated --internal

    def test_non_internal_that_cant_be_removed_fails_closed(self) -> None:
        run, _ = self._fake_run([("network inspect", 0, "false"), ("network rm", 1, "in use")])
        with mock.patch.object(egress.runner, "_run", run):
            res = egress.ensure_network("demo")
        self.assertFalse(res.ok)                       # never reuse a non-internal network
        self.assertIn("NOT --internal", res.detail)


class TeardownTimeoutTest(unittest.TestCase):
    """stop_proxy / teardown run on every project stop (open projects rm a non-existent sidecar).
    A wedged/contended docker daemon must not hang the caller — every docker call here is bounded,
    so the stop-all worker can't stall behind the un-cancellable progress modal forever."""

    def _capture(self):
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw.get("timeout")))
            return subprocess.CompletedProcess(argv, 0, "", "")

        return run, calls

    def test_stop_proxy_is_time_bounded(self) -> None:
        run, calls = self._capture()
        with mock.patch.object(egress.runner, "_run", run):
            egress.stop_proxy("demo")
        self.assertTrue(calls and all(t is not None and t > 0 for _, t in calls),
                        f"stop_proxy must pass a timeout to every docker call: {calls}")

    def test_teardown_is_time_bounded(self) -> None:
        run, calls = self._capture()
        with mock.patch.object(egress.runner, "_run", run):
            egress.teardown("demo")
        self.assertEqual(len(calls), 2)  # rm sidecar + rm network
        self.assertTrue(all(t is not None and t > 0 for _, t in calls),
                        f"teardown must pass a timeout to every docker call: {calls}")


class ParseDeniedTest(unittest.TestCase):
    SAMPLE = (
        "1700000000.123     0 172.18.0.3 TCP_DENIED/403 3897 CONNECT example.com:443 - HIER_NONE/- text/html\n"
        "1700000000.456   120 172.18.0.3 TCP_TUNNEL/200 5000 CONNECT api.anthropic.com:443 - HIER_DIRECT/1.2.3.4 -\n"
        "1700000000.789     0 172.18.0.3 TCP_DENIED/403 3897 GET http://evil.test/x - HIER_NONE/- text/html\n"
        "1700000001.000     0 172.18.0.3 TCP_DENIED/403 3897 CONNECT example.com:443 - HIER_NONE/- text/html\n"
        "garbage short line\n"
    )

    def test_denied_hosts_extracted_deduped_portless_in_order(self) -> None:
        denied = egress.parse_denied(self.SAMPLE)
        # allowed (TCP_TUNNEL) excluded; deduped; CONNECT :443 port stripped so it's a paste-able host.
        self.assertEqual(denied, ["example.com", "evil.test"])

    def test_empty_and_garbage(self) -> None:
        self.assertEqual(egress.parse_denied(""), [])
        self.assertEqual(egress.parse_denied("not a squid line at all\n"), [])


class ParseAccessTest(unittest.TestCase):
    """The richer parser backing the TUI Network panel: BOTH allowed and denied requests, with port,
    bytes, method and status — and parse_denied is now a thin filter over it (regression above)."""

    SAMPLE = ParseDeniedTest.SAMPLE

    def test_records_cover_allowed_and_denied_no_dedup(self) -> None:
        recs = egress.parse_access(self.SAMPLE)
        # The garbage/short line is dropped; the two example.com rows are BOTH kept (no dedup).
        self.assertEqual([(r.host, r.allowed) for r in recs], [
            ("example.com", False),
            ("api.anthropic.com", True),
            ("evil.test", False),
            ("example.com", False),
        ])

    def test_port_method_status(self) -> None:
        recs = egress.parse_access(self.SAMPLE)
        connect_denied = recs[0]
        self.assertEqual((connect_denied.port, connect_denied.method, connect_denied.status),
                         (443, "CONNECT", "403"))
        tunnel = recs[1]
        self.assertEqual((tunnel.port, tunnel.method, tunnel.status, tunnel.allowed),
                         (443, "CONNECT", "200", True))
        # A plain-HTTP url with no explicit port -> port is None (not mis-parsed as a host:port split).
        plain_http = recs[2]
        self.assertEqual((plain_http.host, plain_http.port, plain_http.method), ("evil.test", None, "GET"))

    def test_bytes_parsed_int_with_fallback(self) -> None:
        recs = egress.parse_access(self.SAMPLE)
        self.assertEqual(recs[0].bytes, 3897)
        self.assertEqual(recs[1].bytes, 5000)
        # A non-digit bytes field (squid logs "-" for some entries) falls back to 0, never crashes.
        edge = egress.parse_access(
            "1700000002.0 0 172.18.0.3 TCP_TUNNEL/200 - CONNECT host.test:443 - HIER_DIRECT/9 -\n")
        self.assertEqual((edge[0].bytes, edge[0].host, edge[0].port), (0, "host.test", 443))

    def test_empty_and_garbage(self) -> None:
        self.assertEqual(egress.parse_access(""), [])
        self.assertEqual(egress.parse_access("not a squid line at all\n"), [])

    def test_denied_is_a_filter_over_access(self) -> None:
        # The whole point of the refactor: the denied view is exactly the not-allowed hosts, deduped.
        recs = egress.parse_access(self.SAMPLE)
        derived = []
        seen = set()
        for r in recs:
            if not r.allowed and r.host not in seen:
                seen.add(r.host)
                derived.append(r.host)
        self.assertEqual(derived, egress.parse_denied(self.SAMPLE))


class SummarizeAccessTest(unittest.TestCase):
    SAMPLE = ParseDeniedTest.SAMPLE

    def _summary(self):
        return egress.summarize_access(egress.parse_access(self.SAMPLE))

    def test_per_host_counts_ports_bytes_lastseen(self) -> None:
        summary = self._summary()
        stats = {s.host: s for s in summary}
        # first-seen host order is preserved
        self.assertEqual([s.host for s in summary], ["example.com", "api.anthropic.com", "evil.test"])
        ex = stats["example.com"]
        self.assertEqual((ex.allowed_count, ex.denied_count, ex.total_bytes), (0, 2, 3897 * 2))
        self.assertEqual(ex.ports, (443,))
        self.assertEqual((ex.last_seen, ex.last_allowed), ("1700000001.000", False))
        anth = stats["api.anthropic.com"]
        self.assertEqual((anth.allowed_count, anth.denied_count, anth.total_bytes, anth.last_allowed),
                         (1, 0, 5000, True))
        evil = stats["evil.test"]
        self.assertEqual((evil.denied_count, evil.ports), (1, ()))  # plain-HTTP, no port captured

    def test_empty(self) -> None:
        self.assertEqual(egress.summarize_access(egress.parse_access("")), [])


class SmokeVerdictTest(unittest.TestCase):
    def test_pass_requires_allowed_reachable_and_denied_blocked(self) -> None:
        ok, _ = egress.smoke_verdict(allowed_reachable=True, denied_reachable=False)
        self.assertTrue(ok)

    def test_fail_when_denied_host_reachable(self) -> None:
        ok, detail = egress.smoke_verdict(True, True)
        self.assertFalse(ok)
        self.assertIn("NOT blocked", detail)

    def test_fail_when_allowed_host_unreachable(self) -> None:
        ok, detail = egress.smoke_verdict(False, False)
        self.assertFalse(ok)
        self.assertIn("NOT reachable", detail)


class StrictEgressFloorTest(unittest.TestCase):
    """The agent's strict-egress flags are ADDITIVE — the hardened floor (invariant 2) is byte-identical
    whether a project is open or strict, and an open project gets NO --network / proxy env at all."""

    def _argv(self, egress_mode: str) -> list[str]:
        return runner.build_create_argv(
            Project(slug="p", overlay="base", egress=egress_mode),
            profile_name="work", created_iso="t",
            claude_config_path="/state/p/claude-config", workspace_path="/state/p/workspace",
        )

    def test_open_project_has_no_egress_flags(self) -> None:
        argv = self._argv("open")
        self.assertNotIn("--network", argv)
        joined = " ".join(argv)
        self.assertNotIn("HTTP_PROXY", joined)
        self.assertNotIn("HTTPS_PROXY", joined)

    def test_strict_adds_internal_network_and_proxy_env(self) -> None:
        argv = self._argv("strict")
        self.assertTrue(_contains_sublist(argv, ["--network", config.egress_net_name("p")]))
        proxy_url = f"http://{config.proxy_container_name('p')}:{config.PROXY_PORT}"
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            self.assertTrue(_contains_sublist(argv, ["-e", f"{key}={proxy_url}"]))
        # NO_PROXY excludes loopback + the proxy itself (so the agent reaches the proxy directly).
        self.assertTrue(any(a.startswith("NO_PROXY=") and config.proxy_container_name("p") in a
                            for a in argv))

    def test_floor_byte_identical_open_vs_strict(self) -> None:
        a_open = self._argv("open")
        a_strict = self._argv("strict")
        # The contiguous _HARDENING block is present and identical in both.
        self.assertTrue(_contains_sublist(a_open, runner._HARDENING))
        self.assertTrue(_contains_sublist(a_strict, runner._HARDENING))
        # The ONLY thing open has that strict doesn't is the egress LABEL value (a projection of the
        # registry mode, invariant 4) — no hardening/mount/bind token is removed when locking.
        self.assertEqual(set(a_open) - set(a_strict), {"claude-man.egress=open"})
        # Strict ADDS exactly: the matching label value + the --network/-e proxy wiring. Nothing else.
        proxy_url = f"http://{config.proxy_container_name('p')}:{config.PROXY_PORT}"
        no_proxy = f"localhost,127.0.0.1,::1,{config.proxy_container_name('p')}"
        self.assertEqual(set(a_strict) - set(a_open), {
            "claude-man.egress=strict",
            "--network", config.egress_net_name("p"),
            f"HTTP_PROXY={proxy_url}", f"HTTPS_PROXY={proxy_url}",
            f"http_proxy={proxy_url}", f"https_proxy={proxy_url}",
            f"NO_PROXY={no_proxy}", f"no_proxy={no_proxy}",
        })


if __name__ == "__main__":
    unittest.main()
