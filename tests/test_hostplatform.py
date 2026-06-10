"""hostplatform — the per-host seams (pure functions over an explicit platform; dependency-free)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import hostplatform  # noqa: E402


class HostPlatformTest(unittest.TestCase):
    def test_is_macos_explicit_platform(self) -> None:
        self.assertTrue(hostplatform.is_macos("darwin"))
        self.assertFalse(hostplatform.is_macos("linux"))

    def test_uid_checks_meaningful(self) -> None:
        self.assertTrue(hostplatform.uid_checks_meaningful("linux"))
        self.assertFalse(hostplatform.uid_checks_meaningful("darwin"))  # ownership is synthesised

    def test_docker_ssh_sock_passthrough_on_linux(self) -> None:
        self.assertEqual(hostplatform.docker_ssh_auth_sock("/tmp/agent.1", "linux"), "/tmp/agent.1")
        self.assertIsNone(hostplatform.docker_ssh_auth_sock(None, "linux"))

    def test_docker_ssh_sock_is_magic_path_on_macos(self) -> None:
        # Docker Desktop forwards ONLY the default agent at the fixed in-VM path — always that,
        # regardless of (even instead of) the host's own SSH_AUTH_SOCK value.
        self.assertEqual(hostplatform.docker_ssh_auth_sock("/tmp/agent.1", "darwin"),
                         hostplatform.DOCKER_DESKTOP_SSH_SOCK)
        self.assertEqual(hostplatform.docker_ssh_auth_sock(None, "darwin"),
                         hostplatform.DOCKER_DESKTOP_SSH_SOCK)

    def test_is_wsl_false_off_linux(self) -> None:
        self.assertFalse(hostplatform.is_wsl("darwin"))

    def test_is_wsl_env_var_wins(self) -> None:
        with mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}):
            self.assertTrue(hostplatform.is_wsl("linux"))

    def test_is_wsl_false_without_signals(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "WSL_DISTRO_NAME"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(hostplatform, "_wsl_kernel_hint", lambda: False):
            self.assertFalse(hostplatform.is_wsl("linux"))


if __name__ == "__main__":
    unittest.main()
