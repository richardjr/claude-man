"""The hardened `docker create` argv renderer is the security floor — pin it."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402
from claudeman.docker import runner  # noqa: E402
from claudeman.docker.runner import GH_TOKEN_ENV, OAUTH_TOKEN_ENV  # noqa: E402
from claudeman.registry.schema import EnvMount, PortMapping, Project, Repo  # noqa: E402


def _contains_sublist(big: list, small: list) -> bool:
    n = len(small)
    return any(big[i:i + n] == small for i in range(len(big) - n + 1))


def _project() -> Project:
    return Project(
        slug="landarna",
        profile="work",
        overlay="node",
        env={"NODE_ENV": "development"},
        repos=(Repo(url="git@github.com:3ADAPT/landarna-backend.git", branch="main"),),
    )


class HardenedArgvTest(unittest.TestCase):
    def setUp(self) -> None:
        self.argv = runner.build_create_argv(
            _project(),
            profile_name="work",
            version="2.1.159",
            created_iso="2026-06-01T00:00:00Z",
            claude_config_path="/state/landarna/claude-config",
            workspace_path="/state/landarna/workspace",
        )

    def test_hardening_flags_present(self) -> None:
        a = self.argv
        self.assertIn("--read-only", a)
        self.assertIn("--security-opt", a)
        self.assertIn("no-new-privileges", a)
        # --cap-drop ALL as an adjacent pair
        self.assertEqual(a[a.index("--cap-drop") + 1], "ALL")
        self.assertEqual(a[a.index("--user") + 1], "1000:1000")
        self.assertEqual(a[a.index("--pids-limit") + 1], "1024")

    def test_git_gh_config_dirs_redirected_to_writable_cache(self) -> None:
        # The read-only rootfs makes ~/.gitconfig and ~/.config/gh unwritable; redirect both to the
        # writable .cache tmpfs so `git config --global` / `gh` don't fail with EROFS.
        joined = " ".join(self.argv)
        self.assertIn("GIT_CONFIG_GLOBAL=/home/agent/.cache/gitconfig", joined)
        self.assertIn("GH_CONFIG_DIR=/home/agent/.cache/gh", joined)
        # Yarn (Berry) writes ~/.yarn by default — EROFS under --read-only; redirect the small global
        # folder to the writable .cache tmpfs.
        self.assertIn("YARN_GLOBAL_FOLDER=/home/agent/.cache/yarn", joined)
        self.assertIn("YARN_ENABLE_GLOBAL_CACHE=false", joined)
        # Berry's mirror defaults ON and duplicates the full package cache into globalFolder/cache (the
        # .cache tmpfs) even with a local cacheFolder -> re-fills the 256m tmpfs, ENOSPC on a large
        # install ("only works on the 2nd/3rd run"). Off = packages go straight to the disk cache.
        self.assertIn("YARN_ENABLE_MIRROR=false", joined)
        # The package cache (Berry + Yarn Classic v1) -> the disk-backed /workspace bind, not the
        # size-capped .cache tmpfs (a v1 install OOM'd the 256m tmpfs with ENOSPC). v1 ignores the three
        # Berry vars above but honours YARN_CACHE_FOLDER.
        self.assertIn("YARN_CACHE_FOLDER=/workspace/.yarn-cache", joined)

    def test_git_identity_env_rendered_as_values(self) -> None:
        argv = runner.build_create_argv(
            _project(), profile_name="work", created_iso="t",
            git_env={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name",
                     "GIT_CONFIG_VALUE_0": "Ada Lovelace"},
        )
        self.assertTrue(_contains_sublist(argv, ["-e", "GIT_CONFIG_COUNT=1"]))
        self.assertTrue(_contains_sublist(argv, ["-e", "GIT_CONFIG_KEY_0=user.name"]))
        self.assertTrue(_contains_sublist(argv, ["-e", "GIT_CONFIG_VALUE_0=Ada Lovelace"]))

    def test_project_slug_env_always_injected(self) -> None:
        # The in-terminal "which project?" cue: the slug is always injected (names the prompt + title).
        self.assertTrue(_contains_sublist(self.argv, ["-e", "CLAUDE_MAN_PROJECT=landarna"]))

    def test_tint_env_absent_by_default(self) -> None:
        # Opt-in: without tint=True (the default), no OSC-11 background hex is injected.
        self.assertFalse(any(a.startswith("CLAUDE_MAN_PROJECT_TINT=") for a in self.argv))

    def test_tint_env_injected_when_enabled(self) -> None:
        argv = runner.build_create_argv(_project(), profile_name="work", created_iso="t", tint=True)
        hexval = config.project_tint("landarna")
        self.assertIn(hexval, config._PROJECT_TINTS)                       # a curated palette entry
        self.assertTrue(_contains_sublist(argv, ["-e", f"CLAUDE_MAN_PROJECT_TINT={hexval}"]))

    def test_tmpfs_mounts(self) -> None:
        tmpfs = [a for a in self.argv if a.startswith("/tmp:") or a.startswith("/home/agent/.cache:")]
        self.assertTrue(any(t.startswith("/tmp:") and "exec" in t for t in tmpfs))
        # .cache must be pinned agent-owned (uid/gid 1000) + nosuid — a bare tmpfs is root:root 755
        # and the agent (uid 1000) can't write it (node/corepack + claude's XDG_STATE_HOME break).
        cache = next(t for t in tmpfs if t.startswith("/home/agent/.cache:"))
        self.assertIn("uid=1000", cache)
        self.assertIn("gid=1000", cache)
        self.assertIn("nosuid", cache)

    def test_token_is_passthrough_never_a_value(self) -> None:
        # The token name is present as an env pass-through (no "=value").
        self.assertIn(OAUTH_TOKEN_ENV, self.argv)
        self.assertFalse(
            any(a.startswith(f"{OAUTH_TOKEN_ENV}=") for a in self.argv),
            "token must be pass-through, never inlined into argv",
        )

    def test_anthropic_keys_never_rendered(self) -> None:
        for a in self.argv:
            self.assertNotIn("ANTHROPIC_API_KEY", a)
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", a)

    def test_gh_token_absent_by_default(self) -> None:
        # Opt-in: with inject_gh_token unset (the default), no GH_TOKEN is rendered at all.
        self.assertNotIn(GH_TOKEN_ENV, self.argv)

    def test_gh_token_passthrough_when_injected(self) -> None:
        argv = runner.build_create_argv(
            _project(), profile_name="work", created_iso="t", inject_gh_token=True
        )
        self.assertIn(GH_TOKEN_ENV, argv)  # name-only pass-through
        self.assertFalse(
            any(a.startswith(f"{GH_TOKEN_ENV}=") for a in argv),
            "gh token must be pass-through, never inlined into argv",
        )

    def test_gh_token_never_sourced_from_project_env(self) -> None:
        # GH_TOKEN in project.env must NEVER render (value leak), with or without a configured token —
        # it is sole-sourced from the configured state-tier token (schema also rejects it at load).
        proj = _project()
        object.__setattr__(proj, "env", {GH_TOKEN_ENV: "leak", "FOO": "bar"})
        injected = runner.build_create_argv(proj, profile_name="work", created_iso="t",
                                            inject_gh_token=True)
        self.assertEqual(injected.count(GH_TOKEN_ENV), 1)    # exactly the one pass-through
        self.assertNotIn(f"{GH_TOKEN_ENV}=leak", injected)
        self.assertIn("FOO=bar", injected)
        off = runner.build_create_argv(proj, profile_name="work", created_iso="t")  # no opt-in
        self.assertNotIn(GH_TOKEN_ENV, off)                  # neither pass-through nor value
        self.assertNotIn(f"{GH_TOKEN_ENV}=leak", off)

    def test_env_var_mount_passthrough(self) -> None:
        # A kind="env" mount renders `-e NAME` (name only) — the value is supplied via the child env.
        proj = _project()
        object.__setattr__(proj, "env_mount", (EnvMount(kind="env", name="MY_VAR"),))
        argv = runner.build_create_argv(proj, profile_name="work", created_iso="t")
        self.assertTrue(_contains_sublist(argv, ["-e", "MY_VAR"]))
        self.assertFalse(any(a.startswith("MY_VAR=") for a in argv))  # value never inlined

    def test_env_var_mount_forbidden_name_not_rendered(self) -> None:
        # Even a flagged (lenient) env mount whose name is forbidden must never render an -e for it.
        proj = _project()
        object.__setattr__(proj, "env_mount", (EnvMount.lenient(kind="env", name="GH_TOKEN"),))
        argv = runner.build_create_argv(proj, profile_name="work", created_iso="t")
        # the only GH_TOKEN that may appear is the opt-in OAuth-style pass-through (not here)
        self.assertNotIn("GH_TOKEN", argv)

    def test_gh_token_never_sourced_from_file_env(self) -> None:
        # Opt-in only: a GH_TOKEN passed in file_env must not render (the renderer skips it even if a
        # raw dict slips past read_env_file's scrub).
        argv = runner.build_create_argv(_project(), profile_name="work", created_iso="t",
                                        file_env={GH_TOKEN_ENV: "leak", "KEEP": "1"})
        self.assertNotIn(GH_TOKEN_ENV, argv)
        self.assertIn("KEEP", argv)

    def test_scrubbed_project_env_dropped(self) -> None:
        # Even if a forbidden key sneaks past schema, the renderer drops it.
        proj = Project(slug="x", env={})
        object.__setattr__(proj, "env", {"ANTHROPIC_API_KEY": "sk-leak", "FOO": "bar"})
        argv = runner.build_create_argv(proj, profile_name="home", created_iso="t")
        self.assertNotIn("ANTHROPIC_API_KEY=sk-leak", argv)
        self.assertIn("FOO=bar", argv)

    def test_persistent_binds(self) -> None:
        self.assertIn("/state/landarna/claude-config:/home/agent/.claude", self.argv)
        self.assertIn("/state/landarna/workspace:/workspace", self.argv)
        self.assertEqual(self.argv[self.argv.index("-w") + 1], "/workspace")

    def test_image_and_idle_command(self) -> None:
        self.assertEqual(self.argv[-3:], ["claude-man:node", "sleep", "infinity"])

    def test_labels_present(self) -> None:
        joined = " ".join(self.argv)
        self.assertIn("claude-man.slug=landarna", joined)
        self.assertIn("claude-man.profile=work", joined)
        self.assertIn("claude-man.repos=1", joined)
        self.assertIn("claude-man.auth=token", joined)


class LoginModeArgvTest(unittest.TestCase):
    """auth = "login" (invariant 1's opt-in amendment): NO token env is rendered, the auth label
    self-describes the mode, and the hardened floor is byte-identical modulo exactly those two."""

    def _argv(self, *, auth: str, inject_token: bool) -> list:
        import dataclasses
        return runner.build_create_argv(
            dataclasses.replace(_project(), auth=auth),
            profile_name="work",
            version="2.1.159",
            created_iso="2026-06-01T00:00:00Z",
            claude_config_path="/state/landarna/claude-config",
            workspace_path="/state/landarna/workspace",
            inject_token=inject_token,
        )

    def test_login_mode_renders_no_token_env(self) -> None:
        argv = self._argv(auth="login", inject_token=False)
        self.assertFalse(any(OAUTH_TOKEN_ENV in a for a in argv),
                         "login mode must not reference the OAuth token env at all")

    def test_auth_label_stamped_both_modes(self) -> None:
        self.assertIn("claude-man.auth=login",
                      " ".join(self._argv(auth="login", inject_token=False)))
        self.assertIn("claude-man.auth=token",
                      " ".join(self._argv(auth="token", inject_token=True)))

    def test_floor_byte_identical_modulo_token_and_label(self) -> None:
        token_argv = self._argv(auth="token", inject_token=True)
        login_argv = self._argv(auth="login", inject_token=False)
        # The ONLY differences allowed: the one `-e CLAUDE_CODE_OAUTH_TOKEN` pass-through pair
        # and the auth label's value. Everything else — every hardening flag — is identical.
        i = token_argv.index(OAUTH_TOKEN_ENV)
        self.assertEqual(token_argv[i - 1], "-e")

        def norm(argv: list) -> list:
            return ["claude-man.auth=X" if a.startswith("claude-man.auth=") else a for a in argv]

        self.assertEqual(norm(token_argv[:i - 1] + token_argv[i + 1:]), norm(login_argv))


class RepoFeatureDoesNotTouchHardeningTest(unittest.TestCase):
    """Adding repos must change ONLY the `claude-man.repos` label value — never a mount, env, or
    hardening flag. This is what lets the live-clone-no-recreate design skip an `image smoke` re-run
    (invariant 2 untouched; invariant 1's no-credential-in-container surface unchanged)."""

    def _argv(self, *repos):
        return runner.build_create_argv(
            Project(slug="p", overlay="node", repos=tuple(repos)),
            profile_name="work", created_iso="t",
            claude_config_path="/state/p/claude-config", workspace_path="/state/p/workspace",
        )

    def test_only_repos_label_count_differs(self) -> None:
        one = self._argv(Repo(url="git@github.com:o/a.git"))
        two = self._argv(Repo(url="git@github.com:o/a.git"), Repo(url="git@github.com:o/b.git"))
        # Exactly the two repos label values differ; nothing else in the rendered argv changes.
        self.assertIn("claude-man.repos=1", one)
        self.assertIn("claude-man.repos=2", two)
        diff_one = [a for a in one if a not in two]
        diff_two = [a for a in two if a not in one]
        self.assertEqual(diff_one, ["claude-man.repos=1"])
        self.assertEqual(diff_two, ["claude-man.repos=2"])

    def test_mounts_and_no_ssh_or_credentials_regardless_of_repos(self) -> None:
        argv = self._argv(Repo(url="https://u:tok@github.com/o/a.git"))
        # Exactly the two persistent binds, no others; no ssh-agent / credential surface ever.
        binds = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        self.assertEqual(sorted(binds), [
            "/state/p/claude-config:/home/agent/.claude",
            "/state/p/workspace:/workspace",
        ])
        joined = " ".join(argv)
        for forbidden in ("SSH_AUTH_SOCK", ".ssh", "GIT_ASKPASS", "GITHUB_TOKEN", "u:tok@"):
            self.assertNotIn(forbidden, joined)


class EnvMountRenderTest(unittest.TestCase):
    """Env-mounts render the expected -v/--tmpfs/-e and NEVER alter the hardened floor."""

    def _argv(self, *mounts, ssh_auth_sock=None):
        return runner.build_create_argv(
            Project(slug="p", overlay="base", env_mount=tuple(mounts)),
            profile_name="work", created_iso="t", ssh_auth_sock=ssh_auth_sock,
        )

    def test_file_mount_ro_and_rw(self) -> None:
        argv = self._argv(
            EnvMount(kind="file", src="/abs/netrc", dst="/home/agent/.netrc"),
            EnvMount(kind="file", src="/abs/scratch", dst="/home/agent/out", ro=False),
        )
        self.assertIn("/abs/netrc:/home/agent/.netrc:ro", argv)
        self.assertIn("/abs/scratch:/home/agent/out", argv)        # rw -> no :ro suffix
        self.assertNotIn("/abs/scratch:/home/agent/out:ro", argv)

    def test_ssh_mount_with_agent_socket(self) -> None:
        argv = self._argv(EnvMount(kind="ssh"), ssh_auth_sock="/run/user/1000/ssh.sock")
        joined = " ".join(argv)
        self.assertIn("/home/agent/.ssh:rw,nosuid,mode=0700,uid=1000,gid=1000,size=1m", joined)
        self.assertIn("/run/user/1000/ssh.sock:/ssh-agent:ro", argv)
        self.assertIn("SSH_AUTH_SOCK=/ssh-agent", argv)

    def test_ssh_mount_without_agent_renders_tmpfs_but_no_socket(self) -> None:
        argv = self._argv(EnvMount(kind="ssh"), ssh_auth_sock=None)
        joined = " ".join(argv)
        self.assertIn("/home/agent/.ssh:rw", joined)         # tmpfs still present
        self.assertNotIn("/ssh-agent", joined)               # but no socket forward
        self.assertNotIn("SSH_AUTH_SOCK", joined)

    def test_flagged_mount_is_not_rendered(self) -> None:
        # A load-time-invalid (flagged) mount must never reach docker — its argv must equal no-mount.
        flagged = EnvMount.lenient(kind="file", src="/abs/x", dst="/Workspace/CLAUDE.md")
        self.assertTrue(flagged.error)
        self.assertEqual(self._argv(flagged), self._argv())

    def test_floor_byte_identical_with_and_without_mounts(self) -> None:
        # The contiguous _HARDENING block must be present and identical whether or not env-mounts exist.
        a0 = self._argv()
        a1 = self._argv(
            EnvMount(kind="file", src="/abs/x", dst="/home/agent/.gitconfig"),
            EnvMount(kind="ssh"), ssh_auth_sock="/run/ssh",
        )
        self.assertTrue(_contains_sublist(a0, runner._HARDENING))
        self.assertTrue(_contains_sublist(a1, runner._HARDENING))
        # Env-mounts only ADD value tokens — the -v/-e/--tmpfs flags already exist in a0, so the
        # net-new tokens are exactly the mount values (no floor token is removed or altered).
        self.assertEqual(set(a1) - set(a0), {
            "/home/agent/.ssh:rw,nosuid,mode=0700,uid=1000,gid=1000,size=1m",
            "/abs/x:/home/agent/.gitconfig:ro",
            "/run/ssh:/ssh-agent:ro",
            "SSH_AUTH_SOCK=/ssh-agent",
        })


class PortRenderTest(unittest.TestCase):
    """Published ports render `-p <bind>:<host>:<container>/<proto>` and NEVER alter the hardened floor."""

    def _argv(self, *ports):
        return runner.build_create_argv(
            Project(slug="p", overlay="base", ports=tuple(ports)),
            profile_name="work", created_iso="t",
        )

    def test_default_bind_loopback(self) -> None:
        argv = self._argv(PortMapping(container=5173))   # host defaults to container, bind to 127.0.0.1
        self.assertTrue(_contains_sublist(argv, ["-p", "127.0.0.1:5173:5173/tcp"]))

    def test_explicit_host_proto_and_exposed_bind(self) -> None:
        argv = self._argv(PortMapping(container=5173, host=8080, bind="0.0.0.0", proto="udp"))
        self.assertTrue(_contains_sublist(argv, ["-p", "0.0.0.0:8080:5173/udp"]))

    def test_multiple_ports(self) -> None:
        argv = self._argv(PortMapping(container=5173), PortMapping(container=3000, host=3001))
        self.assertTrue(_contains_sublist(argv, ["-p", "127.0.0.1:5173:5173/tcp"]))
        self.assertTrue(_contains_sublist(argv, ["-p", "127.0.0.1:3001:3000/tcp"]))
        self.assertEqual(argv.count("-p"), 2)

    def test_flagged_port_is_not_rendered(self) -> None:
        # A load-time-invalid (flagged) port must never reach docker — argv must equal no-port.
        flagged = PortMapping.lenient(container=80)   # <1024 -> flagged
        self.assertTrue(flagged.error)
        self.assertEqual(self._argv(flagged), self._argv())

    def test_floor_byte_identical_with_and_without_ports(self) -> None:
        a0 = self._argv()
        a1 = self._argv(PortMapping(container=5173), PortMapping(container=3000, host=3001, bind="0.0.0.0"))
        self.assertTrue(_contains_sublist(a0, runner._HARDENING))
        self.assertTrue(_contains_sublist(a1, runner._HARDENING))
        # Ports only ADD `-p` + value tokens (a0 has no `-p` at all) — no floor token is removed/altered.
        self.assertEqual(set(a1) - set(a0), {"-p", "127.0.0.1:5173:5173/tcp", "0.0.0.0:3001:3000/tcp"})


class EnvFileScrubTest(unittest.TestCase):
    """env_file values must be pass-through (not in argv) and ANTHROPIC_* must never appear."""

    def test_file_env_injected_as_passthrough_not_value(self) -> None:
        argv = runner.build_create_argv(
            Project(slug="x"),
            profile_name="home",
            created_iso="t",
            file_env={"DATABASE_URL": "postgres://secret@host/db", "NODE_ENV": "production"},
        )
        # Pass-through name present as an adjacent `-e KEY` pair...
        self.assertEqual(argv[argv.index("DATABASE_URL") - 1], "-e")
        # ...but the secret value never appears anywhere in argv.
        self.assertFalse(any("postgres://secret@host/db" in a for a in argv))
        self.assertNotIn("DATABASE_URL=postgres://secret@host/db", argv)
        # --env-file is never handed to docker (that path bypassed the scrub).
        self.assertNotIn("--env-file", argv)

    def test_anthropic_keys_in_file_env_never_rendered(self) -> None:
        argv = runner.build_create_argv(
            Project(slug="x"),
            profile_name="home",
            created_iso="t",
            file_env={"ANTHROPIC_API_KEY": "sk-leak", "ANTHROPIC_AUTH_TOKEN": "tok", "OK": "1"},
        )
        joined = " ".join(argv)
        self.assertNotIn("ANTHROPIC_API_KEY", joined)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", joined)
        self.assertNotIn("sk-leak", joined)
        self.assertIn("OK", argv)  # the benign key still passes through

    def test_read_env_file_parses_and_scrubs(self) -> None:
        body = (
            "# a comment\n"
            "\n"
            "export NODE_ENV=production\n"
            'API_BASE="https://api.example/v1"\n'
            "ANTHROPIC_API_KEY=sk-should-be-dropped\n"
            "CLAUDE_CODE_OAUTH_TOKEN=should-also-drop\n"
            "GH_TOKEN=ghp-should-also-drop\n"
            "BARE_LINE_NO_EQUALS\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write(body)
            path = fh.name
        parsed = runner.read_env_file(path)
        Path(path).unlink()
        self.assertEqual(parsed["NODE_ENV"], "production")
        self.assertEqual(parsed["API_BASE"], "https://api.example/v1")  # quotes stripped
        self.assertNotIn("ANTHROPIC_API_KEY", parsed)
        self.assertNotIn(OAUTH_TOKEN_ENV, parsed)
        self.assertNotIn(GH_TOKEN_ENV, parsed)  # opt-in only: never sourced from an env_file
        self.assertNotIn("BARE_LINE_NO_EQUALS", parsed)


class RunMissingBinaryTest(unittest.TestCase):
    def test_missing_binary_becomes_nonzero_result(self) -> None:
        # A missing docker/git binary must NOT raise FileNotFoundError out of _run — that would
        # tear down the TUI create worker. _run maps it to a 127 "not found" CompletedProcess so
        # every caller logs a red line instead. (See review: worker crash-the-app finding.)
        cp = runner._run(["__claude_man_definitely_missing_binary__", "--version"])
        self.assertEqual(cp.returncode, 127)
        self.assertIn("not found", cp.stderr)


class RunnerStopTest(unittest.TestCase):
    """`stop` bounds the SIGTERM grace (sleep-as-PID1 ignores it → full grace then SIGKILL) and a
    wedged daemon can't hang the caller forever."""

    def test_stop_uses_short_grace_and_subprocess_timeout(self) -> None:
        captured: dict = {}

        def fake_run(argv, *, env=None, timeout=None):
            captured["argv"], captured["timeout"] = argv, timeout
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(runner, "_run", fake_run):
            runner.stop("demo")
        argv = captured["argv"]
        self.assertIn("-t", argv)
        self.assertEqual(argv[argv.index("-t") + 1], str(config.DOCKER_STOP_GRACE_S))
        self.assertLess(config.DOCKER_STOP_GRACE_S, 10)            # shorter than docker's 10s default
        self.assertIsNotNone(captured["timeout"])                  # subprocess safety net set
        self.assertGreater(captured["timeout"], config.DOCKER_STOP_GRACE_S)

    def test_run_timeout_returns_124(self) -> None:
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="docker stop", timeout=1)

        with mock.patch.object(runner.subprocess, "run", boom):
            cp = runner._run(["docker", "stop", "x"], timeout=1)
        self.assertEqual(cp.returncode, 124)                       # never hangs; maps to a red Result
        self.assertIn("timed out", cp.stderr)


class LogsArgvTest(unittest.TestCase):
    """The log-stream argv is rendered by a pure builder (TUI-5) so it's pinned without a daemon."""

    def test_default_follows_with_bounded_tail_and_timestamps(self) -> None:
        argv = runner.build_logs_argv("demo")
        self.assertEqual(argv[:2], ["docker", "logs"])
        self.assertIn("-f", argv)                                   # follows new lines
        self.assertIn("--timestamps", argv)                        # keeps stdout/stderr ordered
        self.assertEqual(argv[argv.index("--tail") + 1], "200")    # bounded backfill
        self.assertEqual(argv[-1], config.container_name("demo"))   # container is the final positional

    def test_follow_false_drops_dash_f(self) -> None:
        self.assertNotIn("-f", runner.build_logs_argv("demo", follow=False))

    def test_custom_tail_is_stringified(self) -> None:
        argv = runner.build_logs_argv("demo", tail=10)
        self.assertEqual(argv[argv.index("--tail") + 1], "10")


class ShellHistoryRenderTest(unittest.TestCase):
    """Opt-in persistent shell history (Phase 8d) adds exactly a -v bind + CLAUDEMAN_HISTFILE, and
    NEVER alters the hardened floor — default OFF is byte-identical (invariant 2)."""

    def _argv(self, host_dir=None):
        return runner.build_create_argv(
            _project(), profile_name="work", created_iso="t",
            claude_config_path="/cfg", workspace_path="/ws",
            shell_history_host_dir=host_dir,
        )

    def test_off_by_default_floor_byte_identical(self) -> None:
        a0 = self._argv()  # the default (None) — no bind/env, floor intact
        self.assertTrue(_contains_sublist(a0, runner._HARDENING))
        self.assertNotIn(config.CONTAINER_SHELL_HISTORY_DIR, " ".join(a0))
        self.assertFalse(any(t.startswith("CLAUDEMAN_HISTFILE=") for t in a0))

    def test_on_adds_exactly_the_rw_bind_and_histfile_env(self) -> None:
        a0 = self._argv()
        a1 = self._argv("/state/landarna/shell")
        self.assertTrue(_contains_sublist(a1, runner._HARDENING))  # floor still present + contiguous
        # Net-new tokens are exactly the bind value + the HISTFILE env (the -v/-e flags pre-exist).
        self.assertEqual(set(a1) - set(a0), {
            f"/state/landarna/shell:{config.CONTAINER_SHELL_HISTORY_DIR}",
            f"CLAUDEMAN_HISTFILE={config.CONTAINER_SHELL_HISTORY_DIR}/bash_history",
        })
        # Read-WRITE bind (no :ro) so the agent can persist its history.
        self.assertNotIn(f"/state/landarna/shell:{config.CONTAINER_SHELL_HISTORY_DIR}:ro", a1)


class MemoryLimitRenderTest(unittest.TestCase):
    """The hard memory cap (issue #29) is PART OF THE FLOOR: ALWAYS rendered as `--memory X
    --memory-swap X` (equal -> no swap), default 16g, value from Settings.container_memory. Unlike the
    additive renderers it is never absent — and `_HARDENING` itself stays byte-identical beside it."""

    def _argv(self, **kw):
        return runner.build_create_argv(
            _project(), profile_name="work", created_iso="t",
            claude_config_path="/cfg", workspace_path="/ws", **kw,
        )

    def test_default_cap_always_present_and_swap_disabled(self) -> None:
        a = self._argv()
        self.assertEqual(a[a.index("--memory") + 1], config.DEFAULT_CONTAINER_MEMORY)
        self.assertEqual(a[a.index("--memory-swap") + 1], config.DEFAULT_CONTAINER_MEMORY)
        self.assertEqual(config.DEFAULT_CONTAINER_MEMORY, "16g")
        # Contiguous block, right after the fixed hardening flags (floor + cap are one unit).
        self.assertTrue(_contains_sublist(a, runner._HARDENING + ["--memory", "16g", "--memory-swap", "16g"]))

    def test_custom_value_flows_through_and_is_canonicalised(self) -> None:
        a = self._argv(memory="8G")
        self.assertEqual(a[a.index("--memory") + 1], "8g")
        self.assertEqual(a[a.index("--memory-swap") + 1], "8g")  # swap cap tracks the memory cap
        self.assertTrue(_contains_sublist(a, runner._HARDENING))  # floor unchanged

    def test_cap_never_absent_regardless_of_other_features(self) -> None:
        # Ports / env-mounts / strict egress / shell history are additive; the cap rides along always.
        p = Project(slug="x", profile="work", overlay="node", egress="strict",
                    ports=(PortMapping(container=3000),),
                    env_mount=(EnvMount(kind="ssh"),))
        a = runner.build_create_argv(p, profile_name="work", created_iso="t",
                                     claude_config_path="/cfg", workspace_path="/ws",
                                     shell_history_host_dir="/hist", ssh_auth_sock="/sock")
        self.assertEqual(a.count("--memory"), 1)
        self.assertEqual(a.count("--memory-swap"), 1)

    def test_rejects_junk_loudly(self) -> None:
        # settings.load() coerces junk to the default; a bad PROGRAMMATIC value must fail, not render
        # an argv docker would reject (or silently drop the cap).
        with self.assertRaises(ValueError):
            self._argv(memory="lots")
        with self.assertRaises(ValueError):
            self._argv(memory="512m")  # below the 1g floor
        with self.assertRaises(ValueError):
            self._argv(memory="")      # empty is NOT "no cap" — the cap is mandatory

    def test_render_memory_is_pure_and_exact(self) -> None:
        self.assertEqual(runner._render_memory("2.0G"), ["--memory", "2g", "--memory-swap", "2g"])


class ProjectTintTest(unittest.TestCase):
    """The per-project colour must be STABLE (same colour every run/window) and a curated palette pick
    — it keys on a process-stable SHA-256, not the salted builtin hash()."""

    def test_deterministic_across_calls(self) -> None:
        self.assertEqual(config.project_tint("taskbot"), config.project_tint("taskbot"))

    def test_all_slugs_map_into_palette(self) -> None:
        for slug in ("taskbot", "landarna", "a", "some-long-project-slug", "z9"):
            self.assertIn(config.project_tint(slug), config._PROJECT_TINTS)

    def test_distinct_slugs_can_differ(self) -> None:
        # Not a guarantee for every pair (pigeonhole), but the palette must actually spread — a hash
        # that collapsed everything to one bucket would defeat the feature.
        colours = {config.project_tint(s) for s in
                   ("taskbot", "landarna", "infra", "client", "queue", "db", "packages", "tcv")}
        self.assertGreater(len(colours), 1)


if __name__ == "__main__":
    unittest.main()
