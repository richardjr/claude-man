"""Profile persistence + token status (non-interactive parts of Phase-2 minting)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402
from claudeman.profiles.setup_token import _extract_email  # noqa: E402
from claudeman.registry import profiles  # noqa: E402
from claudeman.registry.schema import Profile  # noqa: E402


class ProfileSaveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.cfg.name
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        self.cfg.cleanup()
        self.state.cleanup()

    def test_set_account_email_scalar_patch_preserves_scrub(self) -> None:
        # The login-mode identity backfill must be a scalar patch: `save()` never writes the
        # [profile.scrub] table back, so routing the backfill through it would silently drop an
        # operator's keep_identity_fields customisation. Comments must survive too.
        from claudeman import config as config_mod
        path = config_mod.profile_toml_path("work")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '[profile]\nname = "work"\n# keep me\ndefault = false\n\n'
            '[profile.scrub]\nkeep_identity_fields = ["emailAddress"]\n'
        )
        updated = profiles.set_account_email("work", "me@x.com")
        self.assertEqual(updated.account_email, "me@x.com")
        self.assertEqual(profiles.load("work").account_email, "me@x.com")
        self.assertEqual(profiles.load("work").keep_identity_fields, ("emailAddress",))
        text = path.read_text()
        self.assertIn("# keep me", text)
        self.assertIn("[profile.scrub]", text)

    def test_save_roundtrip(self) -> None:
        profiles.save(Profile(name="home", display_name="Home", account_email="me@example.com"))
        loaded = profiles.load("home")
        self.assertEqual(loaded.display_name, "Home")
        self.assertEqual(loaded.account_email, "me@example.com")

    def test_make_default_clears_others(self) -> None:
        profiles.save(Profile(name="home", default=True), make_default=True)
        self.assertEqual(profiles.default_profile().name, "home")
        # Adding work as the new default must demote home.
        profiles.save(Profile(name="work", account_email="me@work.example"), make_default=True)
        self.assertEqual(profiles.default_profile().name, "work")
        self.assertFalse(profiles.load("home").default)
        self.assertTrue(profiles.load("work").default)

    def test_token_age_none_then_fresh(self) -> None:
        profiles.save(Profile(name="home"))
        self.assertIsNone(profiles.token_age_days("home"))
        tok = config.profile_token_path("home")
        tok.parent.mkdir(parents=True, exist_ok=True)
        tok.write_text("sk-ant-oat-xyz\n")
        age = profiles.token_age_days("home")
        self.assertIsNotNone(age)
        self.assertLess(age, 1.0)  # just written


class VerifyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_STATE_HOME"] = self.state.name

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_STATE_HOME", None)
        self.state.cleanup()

    def test_verify_without_token_errors(self) -> None:
        from claudeman.profiles import setup_token
        with self.assertRaises(RuntimeError):
            setup_token.verify("home")  # no token minted → clear error, no claude call


class ExtractEmailTest(unittest.TestCase):
    def test_known_shapes(self) -> None:
        self.assertEqual(_extract_email({"account": {"email": "a@x.io"}}), "a@x.io")
        self.assertEqual(_extract_email({"oauthAccount": {"emailAddress": "b@x.io"}}), "b@x.io")
        self.assertEqual(_extract_email({"email": "c@x.io"}), "c@x.io")

    def test_missing_or_malformed(self) -> None:
        self.assertEqual(_extract_email({"nope": 1}), "")
        self.assertEqual(_extract_email({"email": "not-an-email"}), "")
        self.assertEqual(_extract_email([]), "")


if __name__ == "__main__":
    unittest.main()
