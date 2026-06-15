"""Static guards on the baked shell dev environment (Phase 8a, images/bash/ + the base Dockerfile).

Dependency-free: these read the baked asset files and the Dockerfile, no docker/daemon. The runtime
behaviour (rc loads under --read-only, starship runs, HISTFILE writable) is covered by the image
smoke probes in docker/smoke.py — here we pin the source-level invariants that protect the exec
probes (the non-interactive guard) and invariant 6 (no claude-launching alias).
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402

BASH_DIR = config.repo_root() / "images" / "bash"
BASHRC = (BASH_DIR / "bashrc").read_text(encoding="utf-8")
INPUTRC = (BASH_DIR / "inputrc").read_text(encoding="utf-8")
STARSHIP = (BASH_DIR / "starship.toml").read_text(encoding="utf-8")
MOTD = (BASH_DIR / "motd").read_text(encoding="utf-8")
DOCKERFILE = (config.repo_root() / "images" / "base" / "Dockerfile").read_text(encoding="utf-8")


def _splash_logo() -> list[str]:
    """The splash LOGO rows, read from source via ``ast`` — dependency-free (no ``tui``/textual
    import, per the test rules). Lets the banner-art sync guard compare against the real wordmark."""
    src = (config.repo_root() / "src" / "claudeman" / "tui" / "splash.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        # LOGO is an ANNOTATED assignment (`LOGO: tuple[str, ...] = (...)`) -> ast.AnnAssign.
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == "LOGO" for t in targets):
            return [el.value for el in node.value.elts]
    return []


def _code_lines(text: str) -> str:
    """The non-comment, non-blank lines joined — so assertions ignore prose in comments."""
    return "\n".join(
        ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    )


class BashrcTest(unittest.TestCase):
    def test_first_effective_line_is_noninteractive_guard(self) -> None:
        # The exec probes (one-claude comm walk, ssh seed, gitstate) run non-interactive bash/sh; the
        # rc must return immediately for them. This guard MUST stay the first effective line.
        first = _code_lines(BASHRC).splitlines()[0].strip()
        self.assertEqual(first, "[[ $- != *i* ]] && return")

    def test_no_claude_launching_alias(self) -> None:
        # Invariant 6: launching claude from a shell bypasses the host-side one-per-container guard.
        # The rc may reference the `claude-man-motd` banner script (not the agent) and may MENTION
        # claude in a comment, but no executable line may invoke the `claude`/`opencode` agent.
        code = _code_lines(BASHRC).replace("claude-man-motd", "")  # the banner, not the agent
        self.assertNotIn("claude", code)
        self.assertNotIn("opencode", code)

    def test_n_function_defined(self) -> None:
        self.assertIn("n()", _code_lines(BASHRC))

    def test_history_targets_cache_tmpfs(self) -> None:
        # Ephemeral default: HISTFILE on the writable .cache tmpfs (the read-only home can't hold it).
        self.assertIn("HISTFILE=", BASHRC)
        self.assertIn(".cache", _code_lines(BASHRC))

    def test_history_honours_persistent_bind_env(self) -> None:
        # Phase 8d: when claude-man mounts a persistent history bind it injects CLAUDEMAN_HISTFILE;
        # the rc must prefer it over the ephemeral tmpfs path.
        hist_line = next(ln for ln in BASHRC.splitlines() if ln.startswith("HISTFILE="))
        self.assertIn("CLAUDEMAN_HISTFILE", hist_line)

    def test_banner_shown_on_every_interactive_tty(self) -> None:
        # The banner prints on every interactive shell, but ONLY on a real terminal (so non-tty
        # execs/probes stay silent); `hints` re-shows it on demand.
        code = _code_lines(BASHRC)
        self.assertIn("claude-man-motd", code)
        self.assertIn("-t 1", code)                       # tty guard (keeps probes silent)
        self.assertIn("alias hints=", code)               # re-show on demand

    def test_tool_inits_are_guarded(self) -> None:
        # A missing tool must not break the rc — each init is behind a `command -v` probe.
        code = _code_lines(BASHRC)
        for tool in ("starship", "zoxide", "fzf"):
            self.assertIn(f"command -v {tool}", code, f"{tool} init not command-v guarded")


class InputrcTest(unittest.TestCase):
    def test_includes_etc_inputrc_first(self) -> None:
        # A baked ~/.inputrc is read INSTEAD of /etc/inputrc; pull the distro defaults in first.
        self.assertIn("$include /etc/inputrc", INPUTRC)

    def test_arrow_history_prefix_search(self) -> None:
        # The operator's "type a prefix, ↑ cycles only matching history" behaviour.
        self.assertIn('"\\e[A": history-search-backward', INPUTRC)
        self.assertIn('"\\e[B": history-search-forward', INPUTRC)


class StarshipTest(unittest.TestCase):
    def test_prompt_shows_git_branch_and_status(self) -> None:
        # The whole point of the prompt ask: branch + status modules in the format.
        self.assertIn("$git_branch", STARSHIP)
        self.assertIn("$git_status", STARSHIP)


class MotdBannerTest(unittest.TestCase):
    def test_logo_art_matches_the_splash_byte_for_byte(self) -> None:
        # The banner echoes the boot splash; pin every wordmark row so the two never silently drift.
        logo = _splash_logo()
        self.assertEqual(len(logo), 12)
        for row in logo:
            self.assertIn(row, MOTD, "banner logo row drifted from tui/splash.py LOGO")

    def test_history_line_is_dynamic(self) -> None:
        # The HISTORY line reports ephemeral vs persistent from the injected env.
        self.assertIn("CLAUDEMAN_HISTFILE", MOTD)
        self.assertIn("ephemeral", MOTD)
        self.assertIn("persistent", MOTD)

    def test_lists_the_core_commands_and_git_legend(self) -> None:
        for needle in ("n ", "ls", "Ctrl-R", "hints", "config shell-history on", "ahead", "behind"):
            self.assertIn(needle, MOTD)

    def test_colour_only_on_a_tty(self) -> None:
        # No ANSI leaks to a pipe/file (and NO_COLOR honoured) — the colour vars gate on `-t 1`.
        self.assertIn("-t 1", MOTD)
        self.assertIn("NO_COLOR", MOTD)


class DockerfileBakesShellEnvTest(unittest.TestCase):
    def test_copies_the_baked_dotfiles(self) -> None:
        for dst in ("/home/agent/.bashrc", "/home/agent/.inputrc",
                    "/home/agent/.config/starship.toml"):
            self.assertIn(dst, DOCKERFILE, f"Dockerfile does not COPY {dst}")

    def test_bakes_the_banner_script_executable_on_path(self) -> None:
        self.assertIn("/usr/local/bin/claude-man-motd", DOCKERFILE)
        self.assertIn("chmod 0755 /usr/local/bin/claude-man-motd", DOCKERFILE)

    def test_installs_the_dev_clis(self) -> None:
        for pkg in ("starship", "fzf", "eza", "zoxide", "bat", "bash-completion"):
            self.assertIn(pkg, DOCKERFILE, f"Dockerfile does not apt-install {pkg}")

    def test_bat_symlink_for_debian_batcat(self) -> None:
        self.assertIn("ln -s /usr/bin/batcat /usr/local/bin/bat", DOCKERFILE)

    def test_starship_config_env(self) -> None:
        self.assertIn("STARSHIP_CONFIG=/home/agent/.config/starship.toml", DOCKERFILE)


if __name__ == "__main__":
    unittest.main()
