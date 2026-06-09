"""PortMapping schema: validation (container >=1024, proto, bind IP), defaults, exposure, lenient load,
and the CLI <host>:<container> spec parser. No docker/network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import cli  # noqa: E402
from claudeman.registry.schema import PortMapping, ValidationError  # noqa: E402


class PortMappingValidationTest(unittest.TestCase):
    def test_container_below_1024_rejected(self) -> None:
        # --cap-drop ALL strips NET_BIND_SERVICE -> a service inside can't bind <1024.
        with self.assertRaises(ValidationError):
            PortMapping(container=80)
        with self.assertRaises(ValidationError):
            PortMapping(container=1023)

    def test_container_range(self) -> None:
        self.assertEqual(PortMapping(container=1024).container, 1024)
        self.assertEqual(PortMapping(container=65535).container, 65535)
        with self.assertRaises(ValidationError):
            PortMapping(container=65536)

    def test_host_defaults_to_container(self) -> None:
        p = PortMapping(container=5173)
        self.assertEqual(p.host_eff, 5173)
        self.assertEqual(p.host, 0)  # stored 0 = "default"

    def test_explicit_host(self) -> None:
        self.assertEqual(PortMapping(container=5173, host=8080).host_eff, 8080)

    def test_host_range_validated(self) -> None:
        with self.assertRaises(ValidationError):
            PortMapping(container=5173, host=70000)
        # host <1024 IS allowed (the docker daemon, root, binds the privileged host port).
        self.assertEqual(PortMapping(container=5173, host=443).host_eff, 443)

    def test_proto(self) -> None:
        self.assertEqual(PortMapping(container=5173, proto="UDP").proto, "udp")  # normalised
        with self.assertRaises(ValidationError):
            PortMapping(container=5173, proto="sctp")

    def test_bind_default_and_exposure(self) -> None:
        self.assertEqual(PortMapping(container=5173).bind, "127.0.0.1")
        self.assertFalse(PortMapping(container=5173).exposed)            # loopback -> host-only
        self.assertFalse(PortMapping(container=5173, bind="::1").exposed)
        self.assertTrue(PortMapping(container=5173, bind="0.0.0.0").exposed)  # LAN-exposed

    def test_bind_must_be_ip_not_hostname(self) -> None:
        with self.assertRaises(ValidationError):
            PortMapping(container=5173, bind="localhost")

    def test_non_integer_port_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            PortMapping(container="abc")  # type: ignore[arg-type]

    def test_publish_arg_and_target(self) -> None:
        p = PortMapping(container=5173, host=8080, bind="0.0.0.0", proto="tcp")
        self.assertEqual(p.publish_arg(), "0.0.0.0:8080:5173/tcp")
        self.assertEqual(p.target, "8080/tcp")
        self.assertEqual(PortMapping(container=5173).publish_arg(), "127.0.0.1:5173:5173/tcp")


class PortMappingLenientTest(unittest.TestCase):
    def test_invalid_is_flagged_not_raised(self) -> None:
        p = PortMapping.lenient(container=80)  # privileged -> flagged
        self.assertTrue(p.error)
        self.assertEqual(p.container, 80)      # raw value preserved for round-trip + display

    def test_valid_lenient_has_no_error(self) -> None:
        p = PortMapping.lenient(container=5173, host=8080)
        self.assertEqual(p.error, "")
        self.assertEqual(p.publish_arg(), "127.0.0.1:8080:5173/tcp")


class PortSpecParseTest(unittest.TestCase):
    def test_container_only(self) -> None:
        self.assertEqual(cli._parse_port_spec("5173"), (0, 5173))

    def test_host_colon_container(self) -> None:
        self.assertEqual(cli._parse_port_spec("8080:5173"), (8080, 5173))

    def test_non_numeric_raises(self) -> None:
        with self.assertRaises(ValueError):
            cli._parse_port_spec("web:5173")


if __name__ == "__main__":
    unittest.main()
