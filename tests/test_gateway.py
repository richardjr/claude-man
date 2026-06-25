"""Phase 9 hybrid gateway: the PURE LiteLLM config + sidecar/network argv renderers and the
host-gateway seam. Dependency-free (no docker) — the daemon wrappers are exercised only via their pure
renderers, exactly like test_egress.py."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config, hostplatform  # noqa: E402
from claudeman.docker import runner  # noqa: E402
from claudeman.network import gateway  # noqa: E402
from claudeman.registry.schema import Project  # noqa: E402


def _contains_sublist(big: list, small: list) -> bool:
    n = len(small)
    return any(big[i:i + n] == small for i in range(len(big) - n + 1))


class LocalModelIdTest(unittest.TestCase):
    def test_sanitises_tag_into_a_claude_prefixed_id(self) -> None:
        # CC's /model picker ignores ids not starting with claude/anthropic, and `:`/`/` aren't id-safe.
        self.assertEqual(gateway.local_model_id("qwen3-coder:30b"), "claude-local-qwen3-coder-30b")
        self.assertEqual(gateway.local_model_id("user/model:tag"), "claude-local-user-model-tag")


class ConfigYamlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conf = gateway.gateway_config_yaml("qwen3-coder:30b", ollama_host="host.docker.internal")

    def test_claude_passthrough_and_local_route(self) -> None:
        self.assertIn("model_name: claude-*", self.conf)          # any claude-* id -> Anthropic
        self.assertIn("model: anthropic/*", self.conf)
        self.assertIn("model_name: claude-local-qwen3-coder-30b", self.conf)
        self.assertIn("model: ollama_chat/qwen3-coder:30b", self.conf)
        self.assertIn("api_base: http://host.docker.internal:11434", self.conf)

    def test_forwards_client_headers_for_subscription(self) -> None:
        # The make-or-break for keeping the subscription: the agent's OAuth Authorization is forwarded.
        self.assertIn("forward_client_headers_to_llm_api: true", self.conf)

    def test_secret_free(self) -> None:
        # The master key is injected as env, NEVER written into the (state-tier) config.
        self.assertNotIn("master_key", self.conf)
        self.assertNotIn("sk-", self.conf)


class NetworkArgvTest(unittest.TestCase):
    def test_gateway_net_is_bridge_not_internal(self) -> None:
        argv = gateway.network_create_argv("demo")
        self.assertEqual(argv[:3], ["docker", "network", "create"])
        self.assertNotIn("--internal", argv)   # open hybrid keeps egress via this net's gateway
        self.assertIn(config.gateway_net_name("demo"), argv)
        self.assertTrue(_contains_sublist(argv, ["--label", "claude-man.role=gateway-net"]))


class RunArgvTest(unittest.TestCase):
    def setUp(self) -> None:
        self.argv = gateway.gateway_run_argv(
            "demo", conf_path="/state/demo/litellm.config.yaml",
            host_gateway_args=["--add-host=host.docker.internal:host-gateway"],
        )

    def test_run_shape_and_pinned_image(self) -> None:
        self.assertEqual(self.argv[:3], ["docker", "run", "-d"])
        self.assertTrue(_contains_sublist(self.argv, ["--name", config.gateway_container_name("demo")]))
        self.assertTrue(_contains_sublist(self.argv, ["--network", config.gateway_net_name("demo")]))
        self.assertTrue(_contains_sublist(self.argv, ["--security-opt", "no-new-privileges"]))
        self.assertIn(config.GATEWAY_IMAGE_REF, self.argv)

    def test_config_bind_readonly_and_host_route(self) -> None:
        self.assertTrue(_contains_sublist(
            self.argv, ["-v", "/state/demo/litellm.config.yaml:/app/config.yaml:ro"]))
        self.assertIn("--add-host=host.docker.internal:host-gateway", self.argv)

    def test_master_key_passthrough_value_not_in_argv(self) -> None:
        # Pass-through: `-e LITELLM_MASTER_KEY` with NO `=value` (the value rides the child env).
        self.assertTrue(_contains_sublist(self.argv, ["-e", "LITELLM_MASTER_KEY"]))
        self.assertFalse(any(a.startswith("LITELLM_MASTER_KEY=") for a in self.argv))

    def test_not_published_to_host(self) -> None:
        self.assertNotIn("-p", self.argv)   # in-network only; the agent reaches it by DNS name


class HybridFloorTest(unittest.TestCase):
    """The agent's hybrid flags are ADDITIVE — the hardened floor is byte-identical with/without a model
    pinned (invariant 2); the OAuth token is STILL injected (subscription passthrough) and no
    ``ANTHROPIC_*`` credential key is ever rendered (invariant 1)."""

    def _argv(self, *, model: str = "", egress: str = "open") -> list[str]:
        return runner.build_create_argv(
            Project(slug="p", overlay="base", egress=egress, model=model),
            profile_name="work", created_iso="t",
            claude_config_path="/s/cc", workspace_path="/s/ws",
        )

    def test_floor_byte_identical_plain_vs_hybrid(self) -> None:
        plain = self._argv()
        hybrid = self._argv(model="qwen3-coder:30b")
        self.assertTrue(_contains_sublist(plain, runner._HARDENING))
        self.assertTrue(_contains_sublist(hybrid, runner._HARDENING))   # floor unchanged
        gw = config.gateway_container_name("p")
        self.assertEqual(set(hybrid) - set(plain), {
            "--network", config.gateway_net_name("p"),
            f"ANTHROPIC_BASE_URL=http://{gw}:{config.GATEWAY_PORT}",
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1",
            f"ANTHROPIC_DEFAULT_HAIKU_MODEL={config.gateway_local_id('qwen3-coder:30b')}",
            "ANTHROPIC_CUSTOM_HEADERS",   # pass-through (no value in argv — the key rides the child env)
        })

    def test_oauth_still_injected_and_anthropic_keys_never_rendered(self) -> None:
        hybrid = self._argv(model="qwen3-coder:30b")
        self.assertIn(runner.OAUTH_TOKEN_ENV, hybrid)   # subscription passthrough keeps the OAuth token
        joined = " ".join(hybrid)
        self.assertNotIn("ANTHROPIC_API_KEY", joined)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", joined)

    def test_strict_plus_model_renders_no_hybrid(self) -> None:
        # strict+hybrid is gated in lifecycle.up; the renderer emits nothing so the create argv stays
        # valid (no double --network).
        strict = self._argv(model="qwen3-coder:30b", egress="strict")
        self.assertNotIn(config.gateway_net_name("p"), strict)
        self.assertNotIn("ANTHROPIC_BASE_URL", " ".join(strict))


class HostGatewaySeamTest(unittest.TestCase):
    def test_linux_adds_host_gateway(self) -> None:
        self.assertEqual(hostplatform.host_gateway_create_args("linux"),
                         ["--add-host=host.docker.internal:host-gateway"])

    def test_macos_is_automatic(self) -> None:
        self.assertEqual(hostplatform.host_gateway_create_args("darwin"), [])

    def test_loopback_host(self) -> None:
        self.assertEqual(hostplatform.host_loopback_host(), "host.docker.internal")


if __name__ == "__main__":
    unittest.main()
