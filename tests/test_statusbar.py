"""statusbar.render — the per-project tmux bar strings (issue #37): PURE, palette-aligned, and
injection-proof (`#` escaped so no registry value can smuggle a `#()` shell into the bar)."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config, statusbar  # noqa: E402


class RenderTest(unittest.TestCase):
    def test_palette_matches_the_project_identity_colours(self) -> None:
        spec = statusbar.render("demo")
        fg, bg = config.project_name_color("demo"), config.project_tint("demo")
        self.assertEqual(spec.style, f"bg={bg},fg={fg}")
        # The slug chip inverts the pair: bright bg, dark text; the rest of the bar reverts.
        self.assertIn(f"#[bg={fg},fg=#101010,bold] demo #[bg={bg},fg={fg},nobold]", spec.left)

    def test_deterministic(self) -> None:
        self.assertEqual(statusbar.render("demo", profile="p"), statusbar.render("demo", profile="p"))

    def test_segments_present_when_given_and_model_never_silent(self) -> None:
        spec = statusbar.render("demo", profile="work", auth="login", overlay="node",
                                model="claude-opus-5", egress="strict", session="claude")
        for token in ("profile", "work", "auth", "login", "image", "node", "model", "claude-opus-5",
                      "egress", "locked"):
            self.assertIn(token, spec.left, token)
        self.assertIn("claude", spec.right)
        bare = statusbar.render("demo")
        self.assertIn("default", bare.left)          # subscription-direct is spelled out
        self.assertNotIn("profile", bare.left)        # omitted, not rendered empty
        self.assertNotIn("egress", bare.left)

    def test_open_egress_is_shown_as_open(self) -> None:
        self.assertIn("open", statusbar.render("demo", egress="open").left)

    def test_right_side_has_branch_probe_and_clock(self) -> None:
        right = statusbar.render("demo").right
        self.assertIn("git branch --show-current", right)
        self.assertIn("#{pane_current_path}", right)
        self.assertIn("%H:%M", right)

    def test_env_carries_the_three_strings(self) -> None:
        spec = statusbar.render("demo", session="shell")
        env = spec.env()
        self.assertEqual(set(env), set(statusbar.BAR_ENV_NAMES))
        self.assertEqual(env[statusbar.STYLE_ENV], spec.style)
        self.assertEqual(env[statusbar.LEFT_ENV], spec.left)
        self.assertEqual(env[statusbar.RIGHT_ENV], spec.right)


class EscapeTest(unittest.TestCase):
    """A registry string (model ref, profile name…) can never become tmux format syntax."""

    def test_hash_is_doubled(self) -> None:
        self.assertEqual(statusbar.escape("a#(id)#{x}#[fg=red]"), "a##(id)##{x}##[fg=red]")

    def test_quotes_backslashes_and_controls_are_dropped(self) -> None:
        # Only the control BYTES go (ESC, newline); printable remainder stays literal.
        self.assertEqual(statusbar.escape('a"b\\c\x1b[31md\n'), "abc[31md")

    def test_injection_via_every_field_is_neutralised(self) -> None:
        evil = "#(touch /tmp/pwn)"
        spec = statusbar.render("demo", profile=evil, auth=evil, overlay=evil, model=evil,
                                egress=evil, session=evil)
        # The ONLY unescaped `#(` in the whole bar is the fixed git-branch probe on the right: every
        # operator value's `#` arrives doubled (`##(` is a literal `#(` to tmux, never a shell).
        unescaped = re.compile(r"(?<!#)(?:##)*#\(")   # a `#(` preceded by an EVEN number of `#`
        self.assertEqual(len(unescaped.findall(spec.left)), 0)
        self.assertEqual(len(unescaped.findall(spec.right)), 1)
        self.assertIn("##(touch /tmp/pwn)", spec.left)

    def test_model_ref_with_brackets_survives_literally(self) -> None:
        spec = statusbar.render("demo", model="claude-sonnet-5[1m]")
        self.assertIn("claude-sonnet-5[1m]", spec.left)


if __name__ == "__main__":
    unittest.main()
