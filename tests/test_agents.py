"""Phase 7a — the agent-provider seam (docs/AGENTS.md) is a PURE REFACTOR: every rendered argv,
label key, allowlist and env pointer must be byte-identical to the pre-seam code. The literals below
were captured from the pre-refactor tree and pin that (7b added exactly ONE token pair: the
`claude-man.agent=<id>` label after the slug label — the only deliberate change); a provider change that moves one of these
tokens is a deliberate behaviour change and must update the literal AND the docs.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import agents, config, updates  # noqa: E402
from claudeman.agents import AgentProvider, AuthSpec, ImageSpec, UpdateSpec  # noqa: E402
from claudeman.docker import images, labels, runner  # noqa: E402
from claudeman.network import allowlist  # noqa: E402
from claudeman.registry import schema  # noqa: E402
from claudeman.registry.schema import EnvMount, PortMapping, Project, Repo  # noqa: E402
from claudeman.tools import library as tools_library  # noqa: E402
from claudeman.tui import terminals  # noqa: E402

# --- goldens captured from the pre-seam tree (2026-09-26) ---------------------------------------
CREATE_ARGV_FULL = ['docker',
 'create',
 '--name',
 'claude-man-landarna',
 '--label',
 'claude-man.slug=landarna',
 '--label',
 'claude-man.agent=claude',
 '--label',
 'claude-man.profile=work',
 '--label',
 'claude-man.overlay=node',
 '--label',
 'claude-man.tools=jq',
 '--label',
 'claude-man.egress=strict',
 '--label',
 'claude-man.auth=token',
 '--label',
 'claude-man.repos=1',
 '--label',
 'claude-man.version=2.1.159',
 '--label',
 'claude-man.created=2026-06-01T00:00:00Z',
 '--read-only',
 '--cap-drop',
 'ALL',
 '--security-opt',
 'no-new-privileges',
 '--user',
 '1000:1000',
 '--pids-limit',
 '1024',
 '--tmpfs',
 '/tmp:rw,exec,nosuid,size=512m',
 '--tmpfs',
 '/home/agent/.cache:rw,exec,nosuid,size=256m,uid=1000,gid=1000,mode=0700',
 '--memory',
 '8g',
 '--memory-swap',
 '8g',
 '-e',
 'HOME=/home/agent',
 '-e',
 'CLAUDE_CONFIG_DIR=/home/agent/.claude',
 '-e',
 'XDG_CACHE_HOME=/home/agent/.cache',
 '-e',
 'XDG_STATE_HOME=/home/agent/.cache/state',
 '-e',
 'GIT_CONFIG_GLOBAL=/home/agent/.cache/gitconfig',
 '-e',
 'GH_CONFIG_DIR=/home/agent/.cache/gh',
 '-e',
 'YARN_GLOBAL_FOLDER=/home/agent/.cache/yarn',
 '-e',
 'YARN_ENABLE_GLOBAL_CACHE=false',
 '-e',
 'YARN_ENABLE_MIRROR=false',
 '-e',
 'YARN_CACHE_FOLDER=/workspace/.yarn-cache',
 '-e',
 'PIP_CACHE_DIR=/workspace/.pip-cache',
 '-e',
 'UV_CACHE_DIR=/workspace/.uv/cache',
 '-e',
 'UV_PYTHON_INSTALL_DIR=/workspace/.uv/python',
 '-e',
 'UV_PYTHON_BIN_DIR=/workspace/.uv/bin',
 '-e',
 'UV_TOOL_DIR=/workspace/.uv/tools',
 '-e',
 'UV_TOOL_BIN_DIR=/workspace/.uv/bin',
 '-e',
 'TMPDIR=/workspace/.tmp',
 '-e',
 'TMUX_TMPDIR=/tmp',
 '-e',
 'USE_BUILTIN_RIPGREP=0',
 '-e',
 'DISABLE_AUTOUPDATER=1',
 '-e',
 'CLAUDE_CODE_OAUTH_TOKEN',
 '-e',
 'GH_TOKEN',
 '-e',
 'NODE_ENV=development',
 '-e',
 'FOO',
 '-e',
 'GIT_CONFIG_COUNT=1',
 '-e',
 'CLAUDE_MAN_PROJECT=landarna',
 '-e',
 'CLAUDE_MAN_PROJECT_TINT=#241318',
 '-v',
 '/state/landarna/claude-config:/home/agent/.claude',
 '-v',
 '/state/landarna/workspace:/workspace',
 '--tmpfs',
 '/home/agent/.ssh:rw,nosuid,mode=0700,uid=1000,gid=1000,size=1m',
 '-v',
 '/run/user/1000/ssh:/ssh-agent:ro',
 '-e',
 'SSH_AUTH_SOCK=/ssh-agent',
 '-p',
 '127.0.0.1:5173:5173/tcp',
 '--network',
 'claude-man-net-landarna',
 '-e',
 'HTTP_PROXY=http://claude-man-proxy-landarna:3128',
 '-e',
 'HTTPS_PROXY=http://claude-man-proxy-landarna:3128',
 '-e',
 'http_proxy=http://claude-man-proxy-landarna:3128',
 '-e',
 'https_proxy=http://claude-man-proxy-landarna:3128',
 '-e',
 'NO_PROXY=localhost,127.0.0.1,::1,claude-man-proxy-landarna',
 '-e',
 'no_proxy=localhost,127.0.0.1,::1,claude-man-proxy-landarna',
 '-v',
 '/state/landarna/history:/home/agent/.persistent-history',
 '-e',
 'CLAUDEMAN_HISTFILE=/home/agent/.persistent-history/bash_history',
 '-w',
 '/workspace',
 'claude-man:node-t-abc',
 'sleep',
 'infinity']

CREATE_ARGV_MIN = ['docker',
 'create',
 '--name',
 'claude-man-demo',
 '--label',
 'claude-man.slug=demo',
 '--label',
 'claude-man.agent=claude',
 '--label',
 'claude-man.profile=home',
 '--label',
 'claude-man.overlay=base',
 '--label',
 'claude-man.tools=',
 '--label',
 'claude-man.egress=open',
 '--label',
 'claude-man.auth=token',
 '--label',
 'claude-man.repos=0',
 '--label',
 'claude-man.version=2.1.173',
 '--label',
 'claude-man.created=c',
 '--read-only',
 '--cap-drop',
 'ALL',
 '--security-opt',
 'no-new-privileges',
 '--user',
 '1000:1000',
 '--pids-limit',
 '1024',
 '--tmpfs',
 '/tmp:rw,exec,nosuid,size=512m',
 '--tmpfs',
 '/home/agent/.cache:rw,exec,nosuid,size=256m,uid=1000,gid=1000,mode=0700',
 '--memory',
 '16g',
 '--memory-swap',
 '16g',
 '-e',
 'HOME=/home/agent',
 '-e',
 'CLAUDE_CONFIG_DIR=/home/agent/.claude',
 '-e',
 'XDG_CACHE_HOME=/home/agent/.cache',
 '-e',
 'XDG_STATE_HOME=/home/agent/.cache/state',
 '-e',
 'GIT_CONFIG_GLOBAL=/home/agent/.cache/gitconfig',
 '-e',
 'GH_CONFIG_DIR=/home/agent/.cache/gh',
 '-e',
 'YARN_GLOBAL_FOLDER=/home/agent/.cache/yarn',
 '-e',
 'YARN_ENABLE_GLOBAL_CACHE=false',
 '-e',
 'YARN_ENABLE_MIRROR=false',
 '-e',
 'YARN_CACHE_FOLDER=/workspace/.yarn-cache',
 '-e',
 'PIP_CACHE_DIR=/workspace/.pip-cache',
 '-e',
 'UV_CACHE_DIR=/workspace/.uv/cache',
 '-e',
 'UV_PYTHON_INSTALL_DIR=/workspace/.uv/python',
 '-e',
 'UV_PYTHON_BIN_DIR=/workspace/.uv/bin',
 '-e',
 'UV_TOOL_DIR=/workspace/.uv/tools',
 '-e',
 'UV_TOOL_BIN_DIR=/workspace/.uv/bin',
 '-e',
 'TMPDIR=/workspace/.tmp',
 '-e',
 'TMUX_TMPDIR=/tmp',
 '-e',
 'USE_BUILTIN_RIPGREP=0',
 '-e',
 'DISABLE_AUTOUPDATER=1',
 '-e',
 'CLAUDE_CODE_OAUTH_TOKEN',
 '-e',
 'CLAUDE_MAN_PROJECT=demo',
 '-v',
 '/c:/home/agent/.claude',
 '-v',
 '/w:/workspace',
 '-w',
 '/workspace',
 'claude-man:base',
 'sleep',
 'infinity']

BUILD_ARGV = ['docker',
 'build',
 '-f',
 '/home/richard/Work/claude-man/images/overlays/node.Dockerfile',
 '--build-arg',
 'CLAUDE_VERSION=2.1.170',
 '-t',
 'claude-man:node',
 '/home/richard/Work/claude-man']
PROBE_ARGV = ['docker',
 'exec',
 'claude-man-demo',
 'sh',
 '-c',
 'for c in /proc/[0-9]*/comm; do read -r n < "$c" 2>/dev/null && [ "$n" = claude ] && exit 0; '
 'done; exit 1']
BASE_ALLOWLIST = ['.anthropic.com',
 'claude.ai',
 'downloads.claude.ai',
 'sentry.io',
 'registry.npmjs.org',
 'pypi.org',
 'files.pythonhosted.org',
 'registry.yarnpkg.com',
 'repo.yarnpkg.com',
 'deb.debian.org',
 'security.debian.org',
 '.github.com',
 '.githubusercontent.com',
 '.gitlab.com',
 '.bitbucket.org']
INNER_EXEC_CLAUDE = ['bash',
 '-lc',
 "printf '\\033]0;claude:demo\\007\\033]11;#112233\\007'; docker exec -it -w /workspace "
 'claude-man-demo claude --model opus; exec bash']
INNER_EXEC_BASH = ['docker', 'exec', '-it', '-w', '/workspace', 'claude-man-demo', 'bash']


def _full_project() -> Project:
    return Project(
        slug="landarna", profile="work", overlay="node", env={"NODE_ENV": "development"},
        repos=(Repo(url="git@github.com:3ADAPT/landarna-backend.git", branch="main"),),
        egress="strict", model="qwen3-coder:30b", tools=("jq",),
        env_mount=(EnvMount(kind="ssh"),), ports=(PortMapping(container=5173),),
    )


class ByteIdentityTest(unittest.TestCase):
    """The seam changes WHERE the tokens come from, never WHAT is rendered (invariant 2)."""

    def test_create_argv_full_byte_identical(self) -> None:
        argv = runner.build_create_argv(
            _full_project(), profile_name="work", version="2.1.159",
            created_iso="2026-06-01T00:00:00Z",
            claude_config_path="/state/landarna/claude-config", workspace_path="/state/landarna/workspace",
            inject_gh_token=True, file_env={"FOO": "1", "ANTHROPIC_AUTH_TOKEN": "x"},
            ssh_auth_sock="/run/user/1000/ssh", git_env={"GIT_CONFIG_COUNT": "1"},
            shell_history_host_dir="/state/landarna/history", tint=True, memory="8g",
            image="claude-man:node-t-abc",
        )
        self.assertEqual(argv, CREATE_ARGV_FULL)

    def test_create_argv_min_byte_identical(self) -> None:
        argv = runner.build_create_argv(Project(slug="demo"), profile_name="home", created_iso="c",
                                        claude_config_path="/c", workspace_path="/w")
        self.assertEqual(argv, CREATE_ARGV_MIN)

    def test_explicit_default_provider_is_a_no_op(self) -> None:
        kw = dict(profile_name="home", created_iso="c", claude_config_path="/c", workspace_path="/w")
        self.assertEqual(runner.build_create_argv(Project(slug="demo"), **kw),
                         runner.build_create_argv(Project(slug="demo"), provider=agents.CLAUDE, **kw))

    def test_build_argv_byte_identical(self) -> None:
        self.assertEqual(images.build_argv("node", "2.1.170"), BUILD_ARGV)
        self.assertIn(f"CLAUDE_VERSION={config.DEFAULT_CLAUDE_VERSION}", images.build_argv("base"))

    def test_probe_argv_byte_identical(self) -> None:
        self.assertEqual(terminals.build_claude_probe_argv("demo"), PROBE_ARGV)

    def test_base_allowlist_byte_identical(self) -> None:
        self.assertEqual(list(allowlist.BASE_ALLOWLIST), BASE_ALLOWLIST)
        self.assertEqual(list(allowlist.base_allowlist(agents.CLAUDE)), BASE_ALLOWLIST)
        # Provider hosts lead, then the neutral toolchain set — claude.ai (OAuth refresh) never drops.
        self.assertEqual(allowlist.BASE_ALLOWLIST[:len(agents.CLAUDE.required_hosts)],
                         agents.CLAUDE.required_hosts)
        self.assertIn("claude.ai", allowlist.build_allowlist())

    def test_image_version_label_byte_identical(self) -> None:
        self.assertEqual(labels.IMAGE_VERSION, "claude-man.claude-version")
        self.assertEqual(labels.image_version_label(agents.CLAUDE), labels.IMAGE_VERSION)

    def test_inner_exec_byte_identical(self) -> None:
        self.assertEqual(
            terminals._inner_exec("demo", "claude", keep_open=True, workdir="/workspace",
                                  tint_hex="#112233", args=("--model", "opus")),
            INNER_EXEC_CLAUDE)
        self.assertEqual(terminals._inner_exec("demo", "bash", keep_open=True, workdir="/workspace"),
                         INNER_EXEC_BASH)

    def test_release_pointer_byte_identical(self) -> None:
        spec = agents.CLAUDE.updates
        assert spec is not None
        self.assertEqual(spec.releases_url, "https://downloads.claude.ai/claude-code-releases")
        self.assertEqual(spec.channels, ("latest", "stable"))
        self.assertEqual(spec.user_agent, config.CLAUDE_CODE_USER_AGENT)


class ClaudeProviderTest(unittest.TestCase):
    def test_registry(self) -> None:
        self.assertIs(agents.resolve(), agents.CLAUDE)
        self.assertIs(agents.resolve("claude"), agents.CLAUDE)
        self.assertEqual(agents.ids(), ("claude",))
        self.assertEqual(agents.DEFAULT_ID, "claude")
        with self.assertRaises(KeyError):
            agents.resolve("codex")   # 7c

    def test_claude_values_match_the_constants(self) -> None:
        p = agents.CLAUDE
        self.assertEqual((p.binary, p.proc_comm), ("claude", "claude"))
        self.assertEqual(p.config_dir, config.CONTAINER_CLAUDE_CONFIG)
        self.assertEqual(p.config_dir_env, "CLAUDE_CONFIG_DIR")
        self.assertEqual(p.auth.token_env, config.OAUTH_TOKEN_ENV)
        self.assertEqual(p.auth.scrub_env, config.SCRUBBED_ENV_KEYS)
        self.assertEqual(p.auth.credential_file, ".credentials.json")
        self.assertEqual(p.auth.identity_file, ".claude.json")
        self.assertEqual(p.image.version_build_arg, "CLAUDE_VERSION")
        self.assertEqual(p.image.default_version, config.DEFAULT_CLAUDE_VERSION)

    def test_derived_sets(self) -> None:
        self.assertEqual(agents.binaries(), frozenset({"claude"}))
        self.assertEqual(agents.config_dirs(), ("/home/agent/.claude",))
        self.assertEqual(agents.config_dir_envs(), frozenset({"CLAUDE_CONFIG_DIR"}))

    def test_baked_env_pointer_follows_home(self) -> None:
        env = list(runner._baked_env(agents.CLAUDE).items())
        self.assertEqual(env[0], ("HOME", "/home/agent"))
        self.assertEqual(env[1], ("CLAUDE_CONFIG_DIR", "/home/agent/.claude"))
        self.assertNotIn("CLAUDE_CONFIG_DIR", runner._BAKED_ENV)   # provider-owned, not baked

    def test_scrub_covers_provider_token_and_scrub_set(self) -> None:
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN"):
            self.assertTrue(runner._is_scrubbed(key, agents.CLAUDE), key)
        self.assertFalse(runner._is_scrubbed("NODE_ENV", agents.CLAUDE))


class SeamConsumersTest(unittest.TestCase):
    """The guards that must cover EVERY provider's config dir / env pointer, not just claude's."""

    def test_mount_dst_denylist_covers_every_provider_config_dir(self) -> None:
        for d in agents.config_dirs():
            self.assertIn(d, schema._forbidden_dst_exact())
            self.assertIn(d + ".json", schema._forbidden_dst_exact())
            self.assertIn(d + "/", schema._forbidden_dst_prefixes())
        with self.assertRaises(schema.ValidationError):
            EnvMount(kind="file", src="/h/creds", dst="/home/agent/.claude/.credentials.json")

    def test_tool_env_reserves_every_provider_pointer(self) -> None:
        self.assertTrue(agents.config_dir_envs() <= tools_library._RESERVED_ENV)

    def test_is_agent_program(self) -> None:
        self.assertTrue(terminals._is_agent("claude"))
        self.assertFalse(terminals._is_agent("bash"))
        self.assertFalse(terminals._is_agent("nvim"))

    def test_update_check_fails_open_without_a_channel(self) -> None:
        no_updates = AgentProvider(
            id="mute", display_name="Mute", binary="mute", proc_comm="mute",
            config_dir="/home/agent/.mute", config_dir_env="MUTE_HOME",
            auth=AuthSpec(token_env="MUTE_TOKEN", scrub_env=(), credential_file="auth.json",
                          identity_file="id.json"),
            image=ImageSpec(version_build_arg="MUTE_VERSION", version_label="mute-version",
                            default_version="1.0.0"),
            updates=None, required_hosts=("mute.example",),
        )
        rc = updates.resolve_channel("latest", provider=no_updates)
        self.assertIsNone(rc.version)
        self.assertIn("no release channel", rc.note)
        self.assertEqual(labels.image_version_label(no_updates), "claude-man.mute-version")
        self.assertIn("--build-arg", images.build_argv("base", "1.0.0", provider=no_updates))
        self.assertIn("MUTE_VERSION=1.0.0", images.build_argv("base", "1.0.0", provider=no_updates))

    def test_bad_channel_still_rejected(self) -> None:
        self.assertIn("bad channel", updates.resolve_channel("nightly").note)


class ProviderValidationTest(unittest.TestCase):
    """A provider's comm/binary/env names reach a `sh -c` string and docker `-e` argv — keep them
    plain tokens so no provider definition can smuggle shell into the probe or argv."""

    def _mk(self, **over) -> AgentProvider:
        kw = dict(
            id="x", display_name="X", binary="x", proc_comm="x", config_dir="/home/agent/.x",
            config_dir_env="X_HOME",
            auth=AuthSpec(token_env="X_TOKEN", scrub_env=("X_KEY",), credential_file="auth.json",
                          identity_file="id.json"),
            image=ImageSpec(version_build_arg="X_VERSION", version_label="x-version", default_version="1"),
            updates=UpdateSpec(releases_url="https://x.example/r", channels=("latest",),
                               default_channel="latest", user_agent="x/1"),
            required_hosts=("x.example",),
        )
        kw.update(over)
        return AgentProvider(**kw)

    def test_valid(self) -> None:
        self._mk()

    def test_rejects_shell_in_comm(self) -> None:
        for bad in ("claude ]; rm -rf /; [", "a b", "", "x" * 16):
            with self.assertRaises(ValueError, msg=bad):
                self._mk(proc_comm=bad)

    def test_rejects_bad_env_names_and_paths(self) -> None:
        with self.assertRaises(ValueError):
            self._mk(config_dir_env="lower")
        with self.assertRaises(ValueError):
            self._mk(auth=AuthSpec(token_env="X-TOKEN", scrub_env=(), credential_file="a", identity_file="b"))
        with self.assertRaises(ValueError):
            self._mk(config_dir="relative/.x")
        with self.assertRaises(ValueError):
            self._mk(config_dir="/home/agent/.x/")
        with self.assertRaises(ValueError):
            self._mk(required_hosts=())
        with self.assertRaises(ValueError):
            self._mk(id="Codex")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Phase 7b — Project.agent / Profile.agent threaded through registry, labels, status, lifecycle, CLI
# ---------------------------------------------------------------------------
import os  # noqa: E402
import tempfile  # noqa: E402
from unittest import mock  # noqa: E402

from claudeman import cli, lifecycle  # noqa: E402
from claudeman.docker import status  # noqa: E402
from claudeman.registry import profiles as profiles_registry  # noqa: E402
from claudeman.registry import projects as projects_registry  # noqa: E402
from claudeman.registry.schema import Profile, ValidationError  # noqa: E402


class ProjectAgentFieldTest(unittest.TestCase):
    def test_default_is_claude_and_resolves(self) -> None:
        p = Project(slug="demo")
        self.assertEqual(p.agent, "claude")
        self.assertIs(p.provider, agents.CLAUDE)

    def test_unknown_agent_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Project(slug="demo", agent="codex")     # 7c registers it; until then it is invalid
        with self.assertRaises(ValidationError):
            Profile(name="work", agent="gemini")

    def test_profile_default_is_claude(self) -> None:
        self.assertEqual(Profile(name="work").agent, "claude")

    def test_registry_roundtrip_and_terse_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"CLAUDE_MAN_CONFIG_HOME": tmp,
                                             "CLAUDE_MAN_STATE_HOME": tmp + "/state"}):
            path = projects_registry.save(Project(slug="demo"))
            self.assertNotIn("agent =", path.read_text())      # the claude default stays absent
            self.assertEqual(projects_registry.load("demo").agent, "claude")
            ppath = profiles_registry.save(Profile(name="work"))
            self.assertNotIn("agent =", ppath.read_text())     # ("agents/" in seed.include is not it)
            self.assertEqual(profiles_registry.load("work").agent, "claude")
            # An explicit non-default agent id in the TOML is rejected at load until 7c registers it
            path.write_text(path.read_text().replace('slug = "demo"', 'slug = "demo"\nagent = "codex"'))
            with self.assertRaises(ValidationError):
                projects_registry.load("demo")

    def test_label_and_status_row(self) -> None:
        lbls = labels.build(Project(slug="demo"), profile="work", version="1", created_iso="c")
        self.assertEqual(lbls[labels.AGENT], "claude")
        self.assertEqual(labels.AGENT, "claude-man.agent")
        # registry wins over a blank (pre-7b) container label; defaults to claude either way
        cs = status.ContainerStatus(slug="demo", state="running", status_text="Up", agent="")
        rows = status.join([("demo", "work", "open", 0, "", "token", "claude")], {"demo": cs})
        self.assertEqual(rows[0].agent, "claude")
        orphan = status.join([], {"ghost": status.ContainerStatus(slug="ghost", state="exited",
                                                                  status_text="Exited")})
        self.assertEqual(orphan[0].agent, "claude")

    def test_agent_mismatch_guard(self) -> None:
        p = Project(slug="demo")
        self.assertEqual(lifecycle.agent_mismatch(p, None), "")
        self.assertEqual(lifecycle.agent_mismatch(p, Profile(name="work")), "")
        other = Profile.__new__(Profile)   # bypass validation to fake a second provider's profile
        object.__setattr__(other, "name", "oa")
        object.__setattr__(other, "agent", "codex")
        msg = lifecycle.agent_mismatch(p, other)
        self.assertIn("codex profile", msg)
        self.assertIn("runs claude", msg)

    def test_cli_create_takes_agent(self) -> None:
        parser = cli.build_parser()
        ns = parser.parse_args(["project", "create", "demo", "--agent", "claude"])
        self.assertEqual(ns.agent, "claude")
        self.assertIsNone(parser.parse_args(["project", "create", "demo"]).agent)
        with self.assertRaises(SystemExit):
            parser.parse_args(["project", "create", "demo", "--agent", "codex"])

    def test_terminals_provider_for_fails_open(self) -> None:
        with mock.patch.object(terminals.projects, "load", side_effect=FileNotFoundError):
            self.assertIs(terminals.provider_for("nope"), agents.DEFAULT)


# ---------------------------------------------------------------------------
# Phase 7-auth — both auth modes per provider as DATA; invariant 9 (every provider's credential
# env names scrubbed); login-mode plumbing keyed on the provider's credential file
# ---------------------------------------------------------------------------
from claudeman.profiles import seed as seed_mod  # noqa: E402
from claudeman.profiles import setup_token  # noqa: E402
from claudeman.tui import profilesview  # noqa: E402

FAKE = AgentProvider(
    id="fake", display_name="Fake Agent", binary="fakeagent", proc_comm="fakeagent",
    config_dir="/home/agent/.fake", config_dir_env="FAKE_HOME",
    auth=AuthSpec(token_env="FAKE_API_KEY", scrub_env=("FAKE_ALT_KEY",), credential_file="auth.json",
                  identity_file="", token_kind="api-key",
                  login_hint="run `fakeagent login` inside the container ({slug})",
                  token_hint="paste a Fake API key via `profile add --agent fake`"),
    image=ImageSpec(version_build_arg="FAKE_VERSION", version_label="fake-version", default_version="1"),
    updates=None, required_hosts=("api.fake.example",),
)


def _with_fake():
    return mock.patch.dict(agents.PROVIDERS, {"fake": FAKE})


class Invariant9Test(unittest.TestCase):
    def test_credential_names_union_all_providers(self) -> None:
        with _with_fake():
            names = agents.credential_env_names()
            for n in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                      "FAKE_API_KEY", "FAKE_ALT_KEY"):
                self.assertIn(n, names)
            self.assertTrue(agents.is_forbidden_env_name("fake_api_key"))   # normalised
            self.assertTrue(agents.is_forbidden_env_name("GH_TOKEN"))       # config's set still
            self.assertFalse(agents.is_forbidden_env_name("FAKE_API_KEY_BACKUP"))

    def test_runner_scrubs_other_providers_keys(self) -> None:
        with _with_fake():
            # A claude container never renders another provider's key from operator env
            self.assertTrue(runner._is_scrubbed("FAKE_API_KEY", agents.CLAUDE))
            self.assertTrue(runner._is_scrubbed("CLAUDE_CODE_OAUTH_TOKEN", FAKE))
            argv = runner.build_create_argv(Project(slug="demo"), profile_name="h", created_iso="c",
                                            claude_config_path="/c", workspace_path="/w",
                                            file_env={"FAKE_API_KEY": "x", "OK": "1"})
            self.assertNotIn("FAKE_API_KEY", argv)
            self.assertIn("OK", argv)
            # and the FAKE provider's container injects ITS token env, binds ITS config dir
            argv = runner.build_create_argv(Project(slug="demo"), profile_name="h", created_iso="c",
                                            claude_config_path="/c", workspace_path="/w", provider=FAKE)
            self.assertIn("FAKE_API_KEY", argv)
            self.assertIn("FAKE_HOME=/home/agent/.fake", argv)
            self.assertIn("/c:/home/agent/.fake", argv)
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", argv)

    def test_schema_rejects_other_providers_keys(self) -> None:
        with _with_fake():
            with self.assertRaises(ValidationError):
                Project(slug="demo", env={"FAKE_API_KEY": "x"})
            with self.assertRaises(ValidationError):
                EnvMount(kind="env", name="FAKE_ALT_KEY")
            with self.assertRaises(ValidationError):
                EnvMount(kind="file", src="/h/auth.json", dst="/home/agent/.fake/auth.json")


class LoginModePerProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"CLAUDE_MAN_CONFIG_HOME": self.tmp.name + "/cfg",
                                                "CLAUDE_MAN_STATE_HOME": self.tmp.name + "/state"})
        self.env.start()
        self.fake = _with_fake()
        self.fake.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.fake.stop)

    def test_credential_path_and_hint_follow_the_provider(self) -> None:
        p = Project(slug="demo", agent="fake", auth="login")
        self.assertEqual(lifecycle.login_credential_path(p).name, "auth.json")
        self.assertEqual(lifecycle.login_credential_path(Project(slug="demo", auth="login")).name,
                         ".credentials.json")
        self.assertIn("run `fakeagent login` inside the container (demo)", lifecycle._login_note(p))
        self.assertIn("/login once inside the container", lifecycle._login_note(Project(slug="d", auth="login")))
        self.assertEqual(lifecycle._login_note(Project(slug="d")), "")   # token mode: no note

    def test_logout_removes_the_providers_file(self) -> None:
        p = Project(slug="demo", agent="fake", auth="login")
        projects_registry.save(p)
        cred = config.claude_config_dir("demo") / "auth.json"
        cred.parent.mkdir(parents=True)
        cred.write_text("{}")
        with mock.patch.object(lifecycle.runner, "is_running", lambda slug: False):
            res = lifecycle.logout("demo")
        self.assertTrue(res.ok, res.detail)
        self.assertFalse(cred.exists())
        self.assertIn("auth.json", res.detail)

    def test_seed_skips_identity_stub_for_a_provider_without_one(self) -> None:
        cfg = seed_mod.seed_project_config(Project(slug="demo", agent="fake"), Profile(name="w", agent="fake"))
        self.assertTrue(cfg.is_dir())
        self.assertFalse((cfg / ".claude.json").exists())
        (cfg / "auth.json").write_text("{}")
        seed_mod.seed_project_config(Project(slug="demo", agent="fake"), None, overwrite_identity=True)
        self.assertFalse((cfg / "auth.json").exists())   # forced re-seed unlinks the provider's cred
        # the claude seed is unchanged
        cfg2 = seed_mod.seed_project_config(Project(slug="cl"), Profile(name="w"))
        self.assertTrue((cfg2 / ".claude.json").exists())

    def test_profile_mint_api_key_kind_and_login_only(self) -> None:
        prof = setup_token.mint("fk", agent="fake", api_key="sk-fake-123", email="a@b.c")
        self.assertEqual(prof.agent, "fake")
        path = config.profile_token_path("fk")
        self.assertEqual(path.read_text().strip(), "sk-fake-123")
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(profiles_registry.load("fk").agent, "fake")
        with self.assertRaises(RuntimeError):
            setup_token.mint("fk2", agent="fake", api_key="has space")
        lo = setup_token.mint("lo", agent="fake", login_only=True)
        self.assertIsNone(profiles_registry.load_token("lo"))
        self.assertEqual(lo.agent, "fake")
        setup_token.renew("fk", api_key="sk-fake-456")
        self.assertEqual(path.read_text().strip(), "sk-fake-456")
        # a claude login-only profile needs no host claude at all
        setup_token.mint("cl-login", login_only=True)
        self.assertIsNone(profiles_registry.load_token("cl-login"))

    def test_profile_picker_filters_by_agent(self) -> None:
        profiles_registry.save(Profile(name="w"))
        profiles_registry.save(Profile(name="fk", agent="fake"))
        self.assertEqual([r.name for r in profilesview.rows("w")], ["fk", "w"])
        self.assertEqual([r.name for r in profilesview.rows("w", "claude")], ["w"])
        self.assertEqual([r.name for r in profilesview.rows("", "fake")], ["fk"])

    def test_agent_mismatch_with_a_real_second_provider(self) -> None:
        msg = lifecycle.agent_mismatch(Project(slug="d", agent="fake"), Profile(name="w"))
        self.assertIn("claude profile", msg)
        self.assertIn("runs fake", msg)

    def test_cli_profile_add_flags(self) -> None:
        parser = cli.build_parser()
        ns = parser.parse_args(["profile", "add", "fk", "--agent", "fake", "--login-only", "--stdin"])
        self.assertEqual((ns.agent, ns.login_only, ns.stdin), ("fake", True, True))
        self.assertIsNone(parser.parse_args(["profile", "add", "w"]).agent)


class AuthSpecValidationTest(unittest.TestCase):
    def test_token_kind_validated(self) -> None:
        with self.assertRaises(ValueError):
            AuthSpec(token_env="X", scrub_env=(), credential_file="a", identity_file="b", token_kind="magic")


# ---------------------------------------------------------------------------
# Phase 7-run — the headless seam: RunRequest → provider argv; provider JSONL → AgentEvents
# (pinned against REAL captured streams in tests/fixtures/{claude,codex}/); lifecycle.run
# ---------------------------------------------------------------------------
import dataclasses  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402

from claudeman.agents import run as run_mod  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _events(parse, path: Path) -> list[agents.AgentEvent]:
    out: list[agents.AgentEvent] = []
    for rec in _records(path):
        out.extend(parse(rec))
    return out


class RunRequestTest(unittest.TestCase):
    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            agents.RunRequest(prompt="   ")
        with self.assertRaises(ValueError):
            agents.RunRequest(prompt="x", permission="yolo")
        self.assertEqual(agents.PERMISSIONS, ("default", "edits", "full"))

    def test_claude_argv_never_carries_the_prompt(self) -> None:
        req = agents.RunRequest(prompt="do the thing", permission="edits", resume="abc", model="opus")
        argv = run_mod.claude_argv(req)
        self.assertEqual(argv[:5], ("claude", "-p", "--output-format", "stream-json", "--verbose"))
        self.assertIn("acceptEdits", argv)
        self.assertEqual(argv[argv.index("--resume") + 1], "abc")
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertNotIn("do the thing", " ".join(argv))
        self.assertIn("--dangerously-skip-permissions", run_mod.claude_argv(agents.RunRequest("x", "full")))
        self.assertNotIn("--permission-mode", run_mod.claude_argv(agents.RunRequest("x")))

    def test_codex_argv_disables_its_own_sandbox_and_reads_stdin(self) -> None:
        argv = run_mod.codex_argv(agents.RunRequest(prompt="p", resume="t1"))
        self.assertEqual(argv[:3], ("codex", "exec", "--json"))
        self.assertIn("--skip-git-repo-check", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "danger-full-access")   # our container IS the sandbox
        self.assertEqual(argv[-3:], ("resume", "t1", "-"))
        self.assertEqual(run_mod.codex_argv(agents.RunRequest("p"))[-1], "-")

    def test_provider_run_spec(self) -> None:
        assert agents.CLAUDE.run is not None
        self.assertIs(agents.CLAUDE.run.argv, run_mod.claude_argv)
        self.assertIs(agents.CLAUDE.run.parse, run_mod.claude_parse)


class ClaudeStreamParseTest(unittest.TestCase):
    def test_ok_fixture(self) -> None:
        ev = _events(run_mod.claude_parse, FIXTURES / "claude" / "stream-ok.jsonl")
        kinds = [e.kind for e in ev]
        self.assertEqual(kinds, ["started", "message", "turn_done"])   # rate_limit_event ignored
        self.assertEqual(ev[0].text, "claude-fable-5-1")
        self.assertTrue(ev[0].session_id)
        self.assertEqual(ev[1].text, "ok")
        done = ev[-1]
        self.assertTrue(done.ok)
        self.assertEqual(done.text, "ok")
        self.assertEqual(done.usage["output"], 4)
        self.assertEqual(done.usage["cache_read"], 10234)
        self.assertEqual(done.session_id, ev[0].session_id)

    def test_tool_fixture(self) -> None:
        ev = _events(run_mod.claude_parse, FIXTURES / "claude" / "stream-tool.jsonl")
        kinds = [e.kind for e in ev]
        self.assertEqual(kinds, ["started", "message", "tool_use", "tool_result", "message", "turn_done"])
        self.assertEqual(ev[2].tool, "Bash")
        self.assertEqual(ev[2].text, "echo hello")
        self.assertEqual(ev[3].text, "hello")
        self.assertTrue(ev[3].ok)
        self.assertEqual(ev[-2].text, "hello")

    def test_error_result_is_failed(self) -> None:
        ev = run_mod.claude_parse({"type": "result", "subtype": "error_max_turns", "is_error": True,
                                   "session_id": "s", "usage": {}})
        self.assertEqual(ev[0].kind, "failed")
        self.assertFalse(ev[0].ok)
        self.assertEqual(run_mod.claude_parse({"type": "rate_limit_event"}), ())
        self.assertEqual(run_mod.claude_parse({"type": "something_new"}), ())


class CodexStreamParseTest(unittest.TestCase):
    def test_unauth_fixture_fails_cleanly(self) -> None:
        ev = _events(run_mod.codex_parse, FIXTURES / "codex" / "exec-unauth.jsonl")
        self.assertEqual(ev[0].kind, "started")
        self.assertTrue(ev[0].session_id)
        self.assertEqual(ev[-1].kind, "failed")
        self.assertIn("401", ev[-1].text)
        self.assertTrue(any(e.kind == "notice" for e in ev))

    def test_auth_fixture(self) -> None:
        ev = _events(run_mod.codex_parse, FIXTURES / "codex" / "exec-auth.jsonl")
        self.assertEqual([e.kind for e in ev], ["started", "notice", "message", "turn_done"])
        self.assertIn("code-mode host", ev[1].text)   # the single-binary image, before the package fix
        self.assertEqual(ev[2].text, "ok")
        self.assertEqual(ev[3].usage["output"], 5)
        self.assertEqual(ev[3].usage["cache_read"], 11776)

    def test_shell_fixture(self) -> None:
        ev = _events(run_mod.codex_parse, FIXTURES / "codex" / "exec-shell.jsonl")
        kinds = [e.kind for e in ev]
        self.assertIn("tool_use", kinds)
        self.assertIn("tool_result", kinds)
        tr = next(e for e in ev if e.kind == "tool_result")
        self.assertTrue(tr.ok)
        self.assertIn("hello", tr.text)
        self.assertEqual(ev[-1].kind, "turn_done")


class LifecycleRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.project = Project(slug="demo", workdir="app")

    def test_exec_argv(self) -> None:
        argv = lifecycle.run_exec_argv(self.project, agents.RunRequest("hi"))
        self.assertEqual(argv[:6], ["docker", "exec", "-i", "-w", "/workspace/app", "claude-man-demo"])
        self.assertEqual(argv[6:8], ["claude", "-p"])
        self.assertNotIn("hi", argv)

    def _fake_popen(self, fixture: Path, rc: int = 0, stderr: str = ""):
        lines = fixture.read_text().splitlines(keepends=True)
        proc = mock.Mock()
        proc.stdin = mock.Mock()
        proc.stdout = iter(lines + ["not json\n"])
        proc.stderr = mock.Mock(read=lambda: stderr)
        proc.returncode = rc
        proc.wait = mock.Mock()
        return proc

    def test_streams_fixture_into_outcome(self) -> None:
        proc = self._fake_popen(FIXTURES / "claude" / "stream-tool.jsonl")
        seen: list[str] = []
        with mock.patch.object(lifecycle.runner, "is_running", lambda slug: True), \
                mock.patch.object(lifecycle.subprocess if hasattr(lifecycle, "subprocess") else subprocess,
                                  "Popen", return_value=proc), \
                mock.patch("claudeman.tui.terminals.claude_already_running", lambda slug, **kw: False):
            out = lifecycle.run(self.project, agents.RunRequest("run echo hello"),
                                on_event=lambda e: seen.append(e.kind))
        self.assertTrue(out.ok, out.detail)
        self.assertEqual(out.text, "hello")
        self.assertEqual(out.events, 6)
        result = [r for r in _records(FIXTURES / "claude" / "stream-tool.jsonl") if r["type"] == "result"][-1]
        self.assertEqual(out.usage["output"], result["usage"]["output_tokens"])
        self.assertTrue(out.session_id)
        self.assertIn("tool_use", seen)
        proc.stdin.write.assert_any_call("run echo hello")
        proc.stdin.close.assert_called_once()

    def test_failed_stream_and_nonzero_exit(self) -> None:
        # A fake provider whose stream is codex-shaped: the unauth fixture ends in turn.failed,
        # and the process exits 1 — the outcome must be failed with the stream's reason.
        fake = dataclasses.replace(FAKE, run=agents.RunSpec(argv=run_mod.codex_argv, parse=run_mod.codex_parse))
        proc = self._fake_popen(FIXTURES / "codex" / "exec-unauth.jsonl", rc=1, stderr="boom\nlast line")
        with mock.patch.dict(agents.PROVIDERS, {"fake": fake}), \
                mock.patch.object(lifecycle.runner, "is_running", lambda slug: True), \
                mock.patch.object(subprocess, "Popen", return_value=proc), \
                mock.patch("claudeman.tui.terminals.claude_already_running", lambda slug, **kw: False):
            project = Project(slug="demo", agent="fake")
            self.assertEqual(lifecycle.run_exec_argv(project, agents.RunRequest("x"))[6:8], ["codex", "exec"])
            out = lifecycle.run(project, agents.RunRequest("x"))
        self.assertFalse(out.ok)
        self.assertIn("401", out.detail)
        self.assertEqual(out.returncode, 1)
        # a provider with no headless mode (FAKE has run=None) is refused up front
        with mock.patch.dict(agents.PROVIDERS, {"fake": FAKE}):
            with self.assertRaises(ValueError):
                lifecycle.run_exec_argv(Project(slug="d", agent="fake"), agents.RunRequest("x"))
            self.assertFalse(lifecycle.run(Project(slug="d", agent="fake"), agents.RunRequest("x")).ok)

    def test_refuses_when_not_running_or_agent_live(self) -> None:
        with mock.patch.object(lifecycle.runner, "is_running", lambda slug: False):
            out = lifecycle.run(self.project, agents.RunRequest("x"))
        self.assertFalse(out.ok)
        self.assertIn("not running", out.detail)
        with mock.patch.object(lifecycle.runner, "is_running", lambda slug: True), \
                mock.patch("claudeman.tui.terminals.claude_already_running", lambda slug, **kw: True):
            out = lifecycle.run(self.project, agents.RunRequest("x"))
        self.assertFalse(out.ok)
        self.assertIn("one agent per container", out.detail)

    def test_cli_run_parser(self) -> None:
        parser = cli.build_parser()
        ns = parser.parse_args(["project", "run", "demo", "do it", "--permission", "full", "--json",
                                "--timeout", "30"])
        self.assertEqual((ns.slug, ns.prompt, ns.permission, ns.json, ns.timeout),
                         ("demo", "do it", "full", True, 30.0))
        with self.assertRaises(SystemExit):
            parser.parse_args(["project", "run", "demo", "x", "--permission", "yolo"])

