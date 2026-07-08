"""Terminal-spawn argv: launcher table, settings preference, custom templates, openers
(dependency-free — no textual). Platform is always pinned explicitly (or via mocked
``hostplatform`` predicates) so the suite passes identically on Linux and macOS runners.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.registry import settings as settings_registry  # noqa: E402
from claudeman.registry.schema import ValidationError  # noqa: E402
from claudeman.tui import terminals  # noqa: E402


class _IsolatedConfig(unittest.TestCase):
    """Settings isolation: terminals consults config.toml, which must never be the dev's real one."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        self.tmp.cleanup()

    @staticmethod
    def _which(*names: str):
        return mock.patch.object(terminals.shutil, "which",
                                 lambda b: f"/usr/bin/{b}" if b in names else None)

    @staticmethod
    def _platform(name: str):
        """Pin the host platform the pickers see (linux / darwin / wsl)."""
        return mock.patch.multiple(
            terminals.hostplatform,
            is_macos=lambda platform=None: name == "darwin",
            is_wsl=lambda platform=None: name == "wsl",
        )


class TerminalWorkdirTest(unittest.TestCase):
    def test_claude_keep_open_includes_workdir(self) -> None:
        argv = terminals.build_ghostty_argv("demo", "claude", workdir="/workspace/svc")
        joined = " ".join(argv)
        self.assertIn("docker exec -it -w /workspace/svc claude-man-demo claude", joined)

    def test_bash_direct_exec_has_w_flag(self) -> None:
        argv = terminals._inner_exec("demo", "bash", keep_open=False, workdir="/workspace/x")
        self.assertEqual(argv[:5], ["docker", "exec", "-it", "-w", "/workspace/x"])

    def test_empty_workdir_emits_no_w(self) -> None:
        argv = terminals._inner_exec("demo", "bash", keep_open=False, workdir="")
        self.assertNotIn("-w", argv)
        self.assertEqual(argv, ["docker", "exec", "-it", "claude-man-demo", "bash"])

    def test_alacritty_claude_workdir_passthrough(self) -> None:
        argv = terminals.build_alacritty_argv("demo", "claude", workdir="/workspace/api")
        self.assertIn("-w /workspace/api", " ".join(argv))

    def test_nvim_keep_open_drops_to_shell(self) -> None:
        # The `e` Editor action: nvim gets the same keep-open wrap as claude — quitting the
        # editor drops to a shell in the container instead of closing the window.
        argv = terminals.build_ghostty_argv("demo", "nvim", workdir="/workspace/svc")
        self.assertIn("docker exec -it -w /workspace/svc claude-man-demo nvim; exec bash",
                      " ".join(argv))

    def test_alacritty_keep_open_holds(self) -> None:
        argv = terminals.build_alacritty_argv("demo", "claude")
        self.assertIn("--hold", argv)
        self.assertLess(argv.index("--hold"), argv.index("-e"))


class WindowIdentityTest(unittest.TestCase):
    """The project-identity cue on spawned windows (telling parallel project terminals apart): the
    claude/nvim window bypasses the container bashrc, so its keep-open wrapper stamps the OSC title
    (always) and the OSC-11 background tint (when a hex is passed). A bash shell is a bare exec — the
    bashrc names/tints it, so the wrapper must NOT double-stamp."""

    def _cmd(self, argv: list[str]) -> str:
        self.assertEqual(argv[:2], ["bash", "-lc"])  # keep-open wrapper shape
        return argv[-1]

    def test_window_title_matches_launcher(self) -> None:
        self.assertEqual(terminals.window_title("demo"), "claude:demo")

    def test_claude_wrapper_stamps_title_before_exec(self) -> None:
        cmd = self._cmd(terminals._inner_exec("demo", "claude", keep_open=True, workdir=""))
        self.assertIn(r"printf '\033]0;claude:demo\007", cmd)
        self.assertLess(cmd.index("printf"), cmd.index("docker exec"))  # title set BEFORE claude runs

    def test_tint_hex_adds_osc11_background(self) -> None:
        cmd = self._cmd(terminals._inner_exec("demo", "claude", keep_open=True, workdir="",
                                              tint_hex="#241d12"))
        self.assertIn(r"\033]11;#241d12\007", cmd)

    def test_no_tint_hex_omits_osc11(self) -> None:
        cmd = self._cmd(terminals._inner_exec("demo", "claude", keep_open=True, workdir=""))
        self.assertNotIn(r"\033]11;", cmd)

    def test_bash_shell_is_bare_exec_no_wrapper_stamp(self) -> None:
        # bash = direct `docker exec` (bashrc owns its title/tint) — no wrapper, so no printf to inject.
        argv = terminals._inner_exec("demo", "bash", keep_open=True, workdir="")
        self.assertEqual(argv, ["docker", "exec", "-it", "claude-man-demo", "bash"])


class SlugBoundaryTest(unittest.TestCase):
    """SEC-6 defence-in-depth: no slug reaches the keep-open shell string unvalidated."""

    def test_inner_exec_rejects_malformed_slug(self) -> None:
        for bad in ("demo; rm -rf /", "../escape", "$(id)", "Demo"):
            with self.assertRaises(ValidationError, msg=bad):
                terminals._inner_exec(bad, "claude", keep_open=True, workdir="")

    def test_build_argv_rejects_malformed_slug(self) -> None:
        with self.assertRaises(ValidationError):
            terminals.build_ghostty_argv("demo; touch /tmp/pwn", "claude")


class RenderSpecTest(unittest.TestCase):
    INNER = ["docker", "exec", "-it", "claude-man-demo", "claude"]

    def _render(self, name: str, table) -> list[str]:
        spec = next(s for s in table if s.name == name)
        return terminals.render_spec(spec, cls="claude-man-demo", title="claude:demo",
                                     inner=self.INNER)

    def test_kitty_splices_inner_argv(self) -> None:
        argv = self._render("kitty", terminals._LINUX_TERMINALS)
        self.assertEqual(argv[:5], ["kitty", "--class", "claude-man-demo", "--title", "claude:demo"])
        self.assertEqual(argv[5:], self.INNER)

    def test_gnome_terminal_uses_double_dash(self) -> None:
        argv = self._render("gnome-terminal", terminals._LINUX_TERMINALS)
        self.assertEqual(argv, ["gnome-terminal", "--title=claude:demo", "--", *self.INNER])

    def test_darwin_alacritty_has_no_class_flag(self) -> None:
        argv = self._render("alacritty", terminals._DARWIN_TERMINALS)
        self.assertNotIn("--class", argv)
        self.assertEqual(argv, ["alacritty", "-T", "claude:demo", "-e", *self.INNER])

    def test_terminal_app_builds_osascript(self) -> None:
        argv = self._render("terminal-app", terminals._DARWIN_TERMINALS)
        self.assertEqual(argv[0], "osascript")
        self.assertIn('tell application "Terminal" to do script', argv[2])
        self.assertIn("docker exec -it claude-man-demo claude", argv[2])

    def test_osascript_escapes_double_quotes(self) -> None:
        inner = ["bash", "-lc", 'echo "hi"; exec bash']
        spec = next(s for s in terminals._DARWIN_TERMINALS if s.name == "terminal-app")
        argv = terminals.render_spec(spec, cls="c", title="t", inner=inner)
        # The shell string is shlex-quoted, then its quotes are AppleScript-escaped (\").
        self.assertNotIn('do script ""', argv[2])
        self.assertIn('\\"', argv[2])

    def test_wsl_wt_wraps_via_wsl_exe(self) -> None:
        argv = self._render("wt", terminals._WSL_EXTRA)
        self.assertEqual(argv, ["wt.exe", "new-tab", "--title", "claude:demo",
                                "wsl.exe", "-e", *self.INNER])

    def test_template_without_argv_element_raises(self) -> None:
        spec = terminals.TerminalSpec("broken", "x", ("x", "--title", "{title}"))
        with self.assertRaises(RuntimeError):
            terminals.render_spec(spec, cls="c", title="t", inner=self.INNER)


class PlatformTableTest(unittest.TestCase):
    def test_linux_order_preserves_historical_preference(self) -> None:
        names = [s.name for s in terminals.platform_terminals("linux", wsl=False)]
        self.assertEqual(names[:2], ["ghostty", "alacritty"])  # the pre-preference behaviour

    def test_wsl_appends_windows_terminal(self) -> None:
        names = [s.name for s in terminals.platform_terminals("linux", wsl=True)]
        self.assertEqual(names[-1], "wt")
        self.assertIn("ghostty", names)

    def test_darwin_table_ends_with_zero_install_fallback(self) -> None:
        names = [s.name for s in terminals.platform_terminals("darwin")]
        self.assertEqual(names[-1], "terminal-app")

    def test_known_program_names_cover_all_tables(self) -> None:
        names = terminals.known_program_names()
        for expected in ("ghostty", "kitty", "terminal-app", "wt", "custom"):
            self.assertIn(expected, names)


class ResolveSpecTest(_IsolatedConfig):
    def test_auto_detect_prefers_ghostty(self) -> None:
        with self._platform("linux"), self._which("ghostty", "alacritty"):
            self.assertEqual(terminals.resolve_spec("linux").name, "ghostty")

    def test_auto_detect_falls_back_to_alacritty(self) -> None:
        with self._platform("linux"), self._which("alacritty"):
            self.assertEqual(terminals.resolve_spec("linux").name, "alacritty")

    def test_auto_detect_none_raises(self) -> None:
        with self._platform("linux"), self._which():
            with self.assertRaises(RuntimeError):
                terminals.resolve_spec("linux")

    def test_darwin_auto_detect_always_lands_on_terminal_app(self) -> None:
        with self._platform("darwin"), self._which("osascript"):
            self.assertEqual(terminals.resolve_spec("darwin").name, "terminal-app")

    def test_configured_program_wins_over_detection_order(self) -> None:
        settings_registry.set_terminal(program="kitty")
        with self._platform("linux"), self._which("ghostty", "kitty"):
            self.assertEqual(terminals.resolve_spec("linux").name, "kitty")

    def test_configured_but_missing_binary_raises(self) -> None:
        settings_registry.set_terminal(program="kitty")
        with self._platform("linux"), self._which("ghostty"):
            with self.assertRaises(RuntimeError):
                terminals.resolve_spec("linux")

    def test_unknown_configured_name_raises(self) -> None:
        settings_registry.set_terminal(program="wt")  # valid name, but not in the plain-linux table
        with self._platform("linux"), self._which("wt.exe"):
            with self.assertRaises(RuntimeError):
                terminals.resolve_spec("linux")

    def test_custom_template_used_verbatim(self) -> None:
        settings_registry.set_terminal(
            program="custom", command=["myterm", "-T", "{title}", "-e", "{argv}"])
        with self._platform("linux"), self._which():  # custom needs no detection
            argv = terminals.build_argv("demo", "bash", keep_open=False)
        self.assertEqual(argv, ["myterm", "-T", "claude:demo", "-e",
                                "docker", "exec", "-it", "claude-man-demo", "bash"])

    def test_build_argv_matches_legacy_ghostty_behaviour(self) -> None:
        with self._platform("linux"), self._which("ghostty", "alacritty"):
            self.assertEqual(terminals.build_argv("demo", "claude", workdir="/workspace/x"),
                             terminals.build_ghostty_argv("demo", "claude", workdir="/workspace/x"))


class OpenPathArgvTest(_IsolatedConfig):
    """`build_open_path_argv` — the system file-manager 'open this dir' launcher (browse action)."""

    def test_prefers_xdg_open(self) -> None:
        with self._platform("linux"), self._which("xdg-open"):
            self.assertEqual(terminals.build_open_path_argv("/some/ws"), ["xdg-open", "/some/ws"])

    def test_falls_back_to_gio(self) -> None:
        with self._platform("linux"), self._which("gio"):
            self.assertEqual(terminals.build_open_path_argv("/some/ws"), ["gio", "open", "/some/ws"])

    def test_raises_when_no_opener(self) -> None:
        with self._platform("linux"), self._which():
            with self.assertRaises(RuntimeError):
                terminals.build_open_path_argv("/some/ws")

    def test_macos_uses_open(self) -> None:
        with self._platform("darwin"), self._which("open"):
            self.assertEqual(terminals.build_open_path_argv("/some/ws"), ["open", "/some/ws"])

    def test_wsl_prefers_wslview(self) -> None:
        with self._platform("wsl"), self._which("wslview", "xdg-open", "explorer.exe"):
            self.assertEqual(terminals.build_open_path_argv("/some/ws"), ["wslview", "/some/ws"])

    def test_wsl_explorer_is_last_resort(self) -> None:
        with self._platform("wsl"), self._which("explorer.exe"):
            self.assertEqual(terminals.build_open_path_argv("/some/ws"),
                             ["explorer.exe", "/some/ws"])

    def test_configured_opener_wins(self) -> None:
        settings_registry.set_opener(["nautilus", "--new-window"])
        with self._platform("linux"), self._which("xdg-open"):
            self.assertEqual(terminals.build_open_path_argv("/some/ws"),
                             ["nautilus", "--new-window", "/some/ws"])


if __name__ == "__main__":
    unittest.main()
