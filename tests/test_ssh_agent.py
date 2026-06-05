"""ssh_agent pure parsers + key-load planning (no ssh / agent / subprocess needed)."""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import ssh_agent  # noqa: E402

SSH_ADD_L = (
    "256 SHA256:AAAA1111 richard@host (ED25519)\n"
    "3072 SHA256:BBBB2222 work@host (RSA)\n"
)


class ParseTest(unittest.TestCase):
    def test_parse_fingerprints(self) -> None:
        self.assertEqual(
            ssh_agent.parse_fingerprints(SSH_ADD_L), {"SHA256:AAAA1111", "SHA256:BBBB2222"}
        )

    def test_parse_empty_and_no_identities(self) -> None:
        self.assertEqual(ssh_agent.parse_fingerprints(""), set())
        self.assertEqual(ssh_agent.parse_fingerprints("The agent has no identities."), set())

    def test_expand(self) -> None:
        self.assertEqual(ssh_agent.expand("~/x"), os.path.expanduser("~/x"))
        self.assertEqual(ssh_agent.expand(""), "")


class EnsureKeysTest(unittest.TestCase):
    """ensure_keys: already-loaded skipped, missing reported, new added, no-agent refused — mocked."""

    def test_mixed_states(self) -> None:
        fps = {"~/.ssh/loaded": "SHA256:LOADED", "~/.ssh/new": "SHA256:NEW"}
        with mock.patch.object(ssh_agent, "ensure_agent", lambda: "/sock"), \
             mock.patch.object(ssh_agent, "loaded_fingerprints", lambda: {"SHA256:LOADED"}), \
             mock.patch.object(os.path, "exists",
                               lambda p: p != ssh_agent.expand("~/.ssh/missing")), \
             mock.patch.object(ssh_agent, "fingerprint_of", lambda k: fps.get(k)), \
             mock.patch.object(ssh_agent, "add_key", lambda k: (True, "loaded")):
            results = ssh_agent.ensure_keys(["~/.ssh/loaded", "~/.ssh/new", "~/.ssh/missing"])
        by = {r.path: r for r in results}
        self.assertTrue(by["~/.ssh/loaded"].ok)
        self.assertEqual(by["~/.ssh/loaded"].detail, "already loaded")
        self.assertTrue(by["~/.ssh/new"].ok)
        self.assertEqual(by["~/.ssh/new"].detail, "loaded")
        self.assertFalse(by["~/.ssh/missing"].ok)
        self.assertEqual(by["~/.ssh/missing"].detail, "missing")

    def test_passphrase_failure_is_surfaced(self) -> None:
        with mock.patch.object(ssh_agent, "ensure_agent", lambda: "/sock"), \
             mock.patch.object(ssh_agent, "loaded_fingerprints", lambda: set()), \
             mock.patch.object(os.path, "exists", lambda p: True), \
             mock.patch.object(ssh_agent, "fingerprint_of", lambda k: "SHA256:NEW"), \
             mock.patch.object(ssh_agent, "add_key",
                               lambda k: (False, "needs passphrase (run `ssh-add ~/.ssh/p` manually)")):
            results = ssh_agent.ensure_keys(["~/.ssh/p"])
        self.assertFalse(results[0].ok)
        self.assertIn("passphrase", results[0].detail)

    def test_no_agent_refuses(self) -> None:
        with mock.patch.object(ssh_agent, "ensure_agent", lambda: None):
            results = ssh_agent.ensure_keys(["~/.ssh/k"])
        self.assertFalse(results[0].ok)
        self.assertIn("no ssh-agent", results[0].detail)

    def test_empty_is_noop(self) -> None:
        self.assertEqual(ssh_agent.ensure_keys([]), [])


class ManagedSocketOwnershipTest(unittest.TestCase):
    """`_managed_socket_is_ours` only adopts a real socket owned by us — the guard against a hostile
    socket planted at the managed path capturing the operator's keys."""

    def test_missing_path_not_ours(self) -> None:
        self.assertFalse(ssh_agent._managed_socket_is_ours("/no/such/sock"))

    def test_regular_file_not_ours(self) -> None:
        with tempfile.NamedTemporaryFile() as f:  # a non-socket at the path must be rejected
            self.assertFalse(ssh_agent._managed_socket_is_ours(f.name))

    def test_own_socket_is_ours(self) -> None:
        d = tempfile.mkdtemp()
        sock_path = os.path.join(d, "s.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(sock_path)  # a real unix socket we own
            self.assertTrue(ssh_agent._managed_socket_is_ours(sock_path))
        finally:
            srv.close()
            os.path.exists(sock_path) and os.unlink(sock_path)
            os.rmdir(d)

    def test_foreign_uid_socket_not_ours(self) -> None:
        # Same real socket, but stat() reports a different owner -> must be refused.
        d = tempfile.mkdtemp()
        sock_path = os.path.join(d, "s.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(sock_path)
            real_stat = os.stat(sock_path)
            foreign = os.stat_result(
                (real_stat.st_mode, real_stat.st_ino, real_stat.st_dev, real_stat.st_nlink,
                 real_stat.st_uid + 1, real_stat.st_gid, real_stat.st_size,
                 real_stat.st_atime, real_stat.st_mtime, real_stat.st_ctime)
            )
            with mock.patch.object(os, "stat", lambda p: foreign):
                self.assertFalse(ssh_agent._managed_socket_is_ours(sock_path))
        finally:
            srv.close()
            os.path.exists(sock_path) and os.unlink(sock_path)
            os.rmdir(d)


if __name__ == "__main__":
    unittest.main()
