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
from claudeman.registry.schema import EnvMount, Project, Repo  # noqa: E402


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
        # The package cache (Berry + Yarn Classic v1) -> the disk-backed /workspace bind, not the
        # size-capped .cache tmpfs (a v1 install OOM'd the 256m tmpfs with ENOSPC). v1 ignores the two
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


if __name__ == "__main__":
    unittest.main()
