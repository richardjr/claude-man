"""Registry: load/validate project + profile TOML, and resolve the default profile."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman.registry import profiles, projects  # noqa: E402
from claudeman.registry.schema import (  # noqa: E402
    EnvMount,
    Project,
    Repo,
    Sync,
    ValidationError,
)

PROJECT_TOML = """\
[project]
slug = "landarna"
profile = "work"
overlay = "node"
extra_apt = ["jq"]

[project.egress]
mode = "strict"
allowlist = ["registry.yarnpkg.com"]

[project.env]
NODE_ENV = "development"

[[project.repos]]
url = "git@github.com:3ADAPT/landarna-backend.git"
branch = "main"
dir = "landarna-backend"
"""

PROFILE_TOML = """\
[profile]
name = "home"
display_name = "Home"
account_email = "me@example.com"
default = true
"""


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.tmp.name
        (Path(self.tmp.name) / "projects").mkdir(parents=True)
        (Path(self.tmp.name) / "profiles").mkdir(parents=True)
        (Path(self.tmp.name) / "projects" / "landarna.toml").write_text(PROJECT_TOML)
        (Path(self.tmp.name) / "profiles" / "home.toml").write_text(PROFILE_TOML)

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        self.tmp.cleanup()

    def test_load_project(self) -> None:
        p = projects.load("landarna")
        self.assertEqual(p.slug, "landarna")
        self.assertEqual(p.profile, "work")
        self.assertEqual(p.overlay, "node")
        self.assertEqual(p.egress, "strict")
        self.assertEqual(p.allowlist, ("registry.yarnpkg.com",))
        self.assertEqual(p.env, {"NODE_ENV": "development"})
        self.assertEqual(len(p.repos), 1)
        self.assertEqual(p.repos[0].resolved_dir(), "landarna-backend")
        self.assertEqual(p.image, "claude-man:node")
        self.assertEqual(p.container, "claude-man-landarna")

    def test_list_slugs(self) -> None:
        self.assertEqual(projects.list_slugs(), ["landarna"])

    def test_save_roundtrips_and_leaves_no_tmp_residue(self) -> None:
        # Atomic write (tmp + os.replace) must round-trip and leave no temp file behind.
        projects.save(Project(slug="freshproj", overlay="python", egress="strict"))
        p = projects.load("freshproj")
        self.assertEqual((p.overlay, p.egress), ("python", "strict"))
        d = Path(self.tmp.name) / "projects"
        self.assertEqual([f.name for f in d.iterdir() if f.name.endswith(".tmp")], [])

    def test_list_projects_skips_malformed_toml(self) -> None:
        # A half-written / hand-broken file must not crash the listing — the create worker can
        # write while the TUI poll reads (review: torn-read TOMLDecodeError guard).
        (Path(self.tmp.name) / "projects" / "broken.toml").write_text('[project]\nslug = "x"\noops')
        slugs = {p.slug for p in projects.list_projects()}
        self.assertIn("landarna", slugs)
        self.assertNotIn("x", slugs)

    def test_env_values_coerced_to_str(self) -> None:
        # TOML bool/int/float in [project.env] must become env strings (review BUG-1).
        toml = '[project]\nslug = "typed"\n\n[project.env]\nDEBUG = true\nPORT = 3000\nRATIO = 1.5\n'
        (Path(self.tmp.name) / "projects" / "typed.toml").write_text(toml)
        p = projects.load("typed")
        self.assertEqual(p.env, {"DEBUG": "true", "PORT": "3000", "RATIO": "1.5"})

    def test_claude_version_pin_roundtrips(self) -> None:
        projects.save(Project(slug="pinned", claude_version="2.1.169"))
        self.assertEqual(projects.load("pinned").claude_version, "2.1.169")

    def test_language_and_packs_roundtrip(self) -> None:
        projects.save(Project(slug="packed", language="node",
                              packs=("guardrails", "node-conventions")))
        p = projects.load("packed")
        self.assertEqual(p.language, "node")
        self.assertEqual(p.packs, ("guardrails", "node-conventions"))
        # Defaults stay terse: an unset language/packs emits no keys at all.
        projects.save(Project(slug="bare"))
        text = (Path(self.tmp.name) / "projects" / "bare.toml").read_text()
        self.assertNotIn("language", text)
        self.assertNotIn("packs", text)

    def test_set_packs_patches_and_validates(self) -> None:
        projects.save(Project(slug="packed", packs=("guardrails",)))
        p = projects.set_packs("packed", ("guardrails", "workflow"))
        self.assertEqual(p.packs, ("guardrails", "workflow"))
        self.assertEqual(projects.load("packed").packs, ("guardrails", "workflow"))
        projects.set_packs("packed", ())  # empty selection drops the key entirely
        self.assertNotIn("packs", (Path(self.tmp.name) / "projects" / "packed.toml").read_text())
        with self.assertRaises(ValidationError):
            projects.set_packs("packed", ("dup", "dup"))
        with self.assertRaises(ValidationError):
            projects.set_packs("packed", ("Bad Name",))

    def test_ports_roundtrip_and_terse_defaults(self) -> None:
        from claudeman.registry.schema import PortMapping
        projects.save(Project(slug="svc", ports=(
            PortMapping(container=5173),                                  # all defaults
            PortMapping(container=3000, host=3001, bind="0.0.0.0", proto="udp"),
        )))
        text = (Path(self.tmp.name) / "projects" / "svc.toml").read_text()
        self.assertIn("[[project.ports]]", text)
        self.assertNotIn("host = 5173", text)        # host==container omitted (terse)
        self.assertNotIn("bind = \"127.0.0.1\"", text)  # default bind omitted
        loaded = projects.load("svc").ports
        self.assertEqual(loaded[0].publish_arg(), "127.0.0.1:5173:5173/tcp")
        self.assertEqual(loaded[1].publish_arg(), "0.0.0.0:3001:3000/udp")

    def test_ports_omitted_when_none(self) -> None:
        path = projects.save(Project(slug="noports"))
        self.assertNotIn("project.ports", path.read_text())
        self.assertEqual(projects.load("noports").ports, ())

    def test_add_port_collision_rejected(self) -> None:
        from claudeman.registry.schema import PortMapping
        projects.save(Project(slug="p"))
        projects.add_port("p", PortMapping(container=5173, host=8080))
        with self.assertRaises(ValidationError):
            projects.add_port("p", PortMapping(container=9999, host=8080))  # same host port + proto
        # A different proto on the same host port is allowed.
        projects.add_port("p", PortMapping(container=9999, host=8080, proto="udp"))
        self.assertEqual(len(projects.load("p").ports), 2)

    def test_remove_port_by_host_and_proto(self) -> None:
        from claudeman.registry.schema import PortMapping
        projects.save(Project(slug="p"))
        projects.add_port("p", PortMapping(container=5173, host=8080))
        projects.add_port("p", PortMapping(container=9999, host=8080, proto="udp"))
        _, removed = projects.remove_port("p", "8080/udp")   # proto-pinned
        self.assertEqual(removed.proto, "udp")
        self.assertEqual([x.proto for x in projects.load("p").ports], ["tcp"])
        _, again = projects.remove_port("p", "8080")          # bare host matches the remaining tcp
        self.assertEqual(again.proto, "tcp")
        _, none = projects.remove_port("p", "8080")           # idempotent no-op
        self.assertIsNone(none)

    def test_flagged_port_with_string_host_roundtrips_and_is_removable(self) -> None:
        # A flagged entry (raw non-int host, e.g. hand-edited/rule-tightened TOML) must round-trip AND
        # stay removable by its target string — the lenient() contract. Regression: remove_port used to
        # int()-parse the target and fail on a non-numeric host, leaving the entry unremovable.
        from claudeman.registry.schema import PortMapping
        flagged = PortMapping.lenient(container=8080, host="not_a_number")
        self.assertTrue(flagged.error)
        self.assertEqual(flagged.target, "not_a_number/tcp")
        projects.save(Project(slug="p", ports=(flagged,)))
        loaded = projects.load("p").ports
        self.assertEqual(len(loaded), 1)
        self.assertTrue(loaded[0].error)                       # round-trips as flagged
        _, removed = projects.remove_port("p", loaded[0].target)  # the TUI removes by this key
        self.assertIsNotNone(removed)
        self.assertEqual(projects.load("p").ports, ())

    def test_claude_version_omitted_when_unset(self) -> None:
        path = projects.save(Project(slug="unpinned"))
        self.assertNotIn("claude_version", path.read_text())
        self.assertEqual(projects.load("unpinned").claude_version, "")  # absent -> default ""

    def test_sync_block_roundtrips(self) -> None:
        projects.save(Project(slug="synced", sync=Sync(
            enabled=False, workspace=("CLAUDE.md", "docs/"), claude=("skills",))))
        self.assertEqual(projects.load("synced").sync,
                         Sync(enabled=False, workspace=("CLAUDE.md", "docs/"), claude=("skills",)))

    def test_sync_defaults_omitted_from_save(self) -> None:
        path = projects.save(Project(slug="plain"))
        self.assertNotIn("[project.sync]", path.read_text())   # default Sync -> no clutter
        self.assertEqual(projects.load("plain").sync, Sync())  # absent block loads as defaults

    def test_invalid_sync_entry_rejected_on_load(self) -> None:
        toml = '[project]\nslug = "bad"\n\n[project.sync]\nworkspace = ["../escape"]\n'
        (Path(self.tmp.name) / "projects" / "bad.toml").write_text(toml)
        with self.assertRaises(ValidationError):
            projects.load("bad")

    def test_gh_token_in_project_env_rejected(self) -> None:
        # GH_TOKEN is a secret — it must never be declared in [project.env] (the git-versionable tier);
        # configure it via `config gh-token`. Schema rejects it loudly (like the ANTHROPIC_* keys).
        with self.assertRaises(ValidationError):
            Project(slug="x", env={"GH_TOKEN": "ghp_leak"})
        toml = '[project]\nslug = "ghbad"\n\n[project.env]\nGH_TOKEN = "ghp_leak"\n'
        (Path(self.tmp.name) / "projects" / "ghbad.toml").write_text(toml)
        with self.assertRaises(ValidationError):
            projects.load("ghbad")

    def test_load_profile_and_default(self) -> None:
        prof = profiles.load("home")
        self.assertTrue(prof.default)
        self.assertEqual(profiles.default_profile().name, "home")

    def test_resolve_inherits_default(self) -> None:
        p = Project(slug="noproj")  # no explicit profile
        self.assertEqual(profiles.resolve_for_project(p).name, "home")


class RepoMutationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.tmp.name
        (Path(self.tmp.name) / "projects").mkdir(parents=True)
        (Path(self.tmp.name) / "projects" / "landarna.toml").write_text(PROJECT_TOML)

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        self.tmp.cleanup()

    def test_add_repo_roundtrip_and_preserves_comments(self) -> None:
        # Seed a TOML carrying an operator comment, confirm add_repo keeps it (tomlkit save).
        (Path(self.tmp.name) / "projects" / "p.toml").write_text(
            '[project]\nslug = "p"\n# keep me\noverlay = "base"\n'
        )
        updated = projects.add_repo("p", "git@github.com:org/svc.git", branch="dev")
        self.assertEqual(len(updated.repos), 1)
        self.assertEqual(updated.repos[0].resolved_dir(), "svc")
        self.assertEqual(updated.repos[0].branch, "dev")
        reloaded = projects.load("p")
        self.assertEqual(reloaded.repos[0].url, "git@github.com:org/svc.git")
        self.assertIn("# keep me", (Path(self.tmp.name) / "projects" / "p.toml").read_text())

    def test_add_repo_duplicate_url_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            projects.add_repo("landarna", "git@github.com:3ADAPT/landarna-backend.git")

    def test_add_repo_dir_collision_rejected(self) -> None:
        # A different url that resolves to the same workspace subdir as the existing repo.
        with self.assertRaises(ValidationError):
            projects.add_repo("landarna", "git@example.com:x/landarna-backend.git")

    def test_add_repo_explicit_dir_avoids_collision(self) -> None:
        updated = projects.add_repo(
            "landarna", "git@example.com:x/landarna-backend.git", dir="backend-fork"
        )
        self.assertEqual([r.resolved_dir() for r in updated.repos], ["landarna-backend", "backend-fork"])

    def test_remove_repo_by_dir(self) -> None:
        updated, removed = projects.remove_repo("landarna", "landarna-backend")
        self.assertIsNotNone(removed)
        self.assertEqual(updated.repos, ())
        self.assertEqual(projects.load("landarna").repos, ())

    def test_remove_repo_by_url(self) -> None:
        _, removed = projects.remove_repo("landarna", "git@github.com:3ADAPT/landarna-backend.git")
        self.assertIsNotNone(removed)

    def test_remove_repo_idempotent_noop(self) -> None:
        updated, removed = projects.remove_repo("landarna", "does-not-exist")
        self.assertIsNone(removed)
        self.assertEqual(len(updated.repos), 1)  # unchanged


class EnvMountMutationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_MAN_CONFIG_HOME"] = self.tmp.name
        (Path(self.tmp.name) / "projects").mkdir(parents=True)
        (Path(self.tmp.name) / "projects" / "p.toml").write_text(
            '[project]\nslug = "p"\n# operator note\noverlay = "base"\n'
        )

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_MAN_CONFIG_HOME", None)
        self.tmp.cleanup()

    def test_add_mount_roundtrip_preserves_comments(self) -> None:
        projects.add_mount("p", EnvMount(kind="ssh"))
        projects.add_mount("p", EnvMount(kind="file", src="~/.netrc", dst="/home/agent/.netrc", ro=False))
        reloaded = projects.load("p")
        self.assertEqual([m.kind for m in reloaded.env_mount], ["ssh", "file"])
        self.assertEqual(reloaded.env_mount[1].dst, "/home/agent/.netrc")
        self.assertFalse(reloaded.env_mount[1].ro)
        self.assertIn("# operator note", (Path(self.tmp.name) / "projects" / "p.toml").read_text())

    def test_add_mount_duplicate_ssh_rejected(self) -> None:
        projects.add_mount("p", EnvMount(kind="ssh"))
        with self.assertRaises(ValidationError):
            projects.add_mount("p", EnvMount(kind="ssh"))

    def test_add_mount_dst_collision_rejected(self) -> None:
        projects.add_mount("p", EnvMount(kind="file", src="/a", dst="/home/agent/.netrc"))
        with self.assertRaises(ValidationError):
            projects.add_mount("p", EnvMount(kind="file", src="/b", dst="/home/agent/.netrc"))

    def test_load_with_now_invalid_mount_is_lenient_not_crash(self) -> None:
        # A mount valid when saved but invalidated by a tightened rule must LOAD (flagged), not crash.
        (Path(self.tmp.name) / "projects" / "old.toml").write_text(
            '[project]\nslug = "old"\n[[project.env_mount]]\nkind = "file"\n'
            'src = "~/Work/CLAUDE.md"\ndst = "/Workspace/CLAUDE.md"\n'
        )
        proj = projects.load("old")  # must NOT raise
        self.assertEqual(len(proj.env_mount), 1)
        self.assertTrue(proj.env_mount[0].error)
        # ...and it is removable by its target, self-healing the TOML.
        _, removed = projects.remove_mount("old", "/Workspace/CLAUDE.md")
        self.assertIsNotNone(removed)
        self.assertEqual(projects.load("old").env_mount, ())

    def test_remove_mount_by_dst_and_ssh_and_idempotent(self) -> None:
        projects.add_mount("p", EnvMount(kind="ssh"))
        projects.add_mount("p", EnvMount(kind="file", src="/a", dst="/home/agent/.gitconfig"))
        _, removed = projects.remove_mount("p", "/home/agent/.gitconfig")
        self.assertIsNotNone(removed)
        _, removed_ssh = projects.remove_mount("p", "ssh")
        self.assertEqual(removed_ssh.kind, "ssh")
        self.assertEqual(projects.load("p").env_mount, ())
        _, noop = projects.remove_mount("p", "nothing")
        self.assertIsNone(noop)


class EnvMountValidationTest(unittest.TestCase):
    def test_ssh_needs_no_paths(self) -> None:
        self.assertEqual(EnvMount(kind="ssh").target, "ssh")

    def test_bad_kind_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EnvMount(kind="nope")

    def test_file_needs_src(self) -> None:
        with self.assertRaises(ValidationError):
            EnvMount(kind="file", src="", dst="/home/agent/.netrc")

    def test_credentials_injection_rejected(self) -> None:
        # The load-bearing guard: a bind onto the config dir smuggles a working .credentials.json.
        with self.assertRaises(ValidationError):
            EnvMount(kind="file", src="/x", dst="/home/agent/.claude/.credentials.json")

    def test_managed_mount_dsts_rejected(self) -> None:
        for dst in (
            "/workspace", "/tmp", "/tmp/x", "/home/agent/.cache",
            "/home/agent/.cache/x", "/home/agent", "/", "/etc",
            "/home/agent/.claude.json",          # identity sibling
            "/home/agent/.ssh", "/home/agent/.ssh/id_rsa", "/ssh-agent",   # ssh tmpfs / socket
            "/home/agent/.local/bin/claude",     # the baked claude launcher (token-in-env)
        ):
            with self.assertRaises(ValidationError, msg=dst):
                EnvMount(kind="file", src="/x", dst=dst)

    def test_workspace_subpath_allowed(self) -> None:
        # A workspace-root CLAUDE.md is a primary use case — /workspace/<subpath> is allowed (the
        # nested mountpoint is pre-created operator-owned); only bare /workspace is reserved.
        self.assertEqual(EnvMount(kind="file", src="/x", dst="/workspace/CLAUDE.md").dst,
                         "/workspace/CLAUDE.md")

    def test_trailing_slash_dst_appends_basename(self) -> None:
        # `~/Work/CLAUDE.md` -> `/workspace/` becomes `/workspace/CLAUDE.md` (cp-style).
        self.assertEqual(EnvMount(kind="file", src="/h/Work/CLAUDE.md", dst="/workspace/").dst,
                         "/workspace/CLAUDE.md")
        self.assertEqual(EnvMount(kind="file", src="/h/.netrc", dst="/home/agent/").dst,
                         "/home/agent/.netrc")

    def test_leading_double_slash_bypass_rejected(self) -> None:
        # POSIX preserves a leading // which the kernel collapses — the denylist must too.
        for dst in ("//home/agent/.claude/.credentials.json", "//etc",
                    "//home/agent/.ssh/id_rsa"):
            with self.assertRaises(ValidationError, msg=dst):
                EnvMount(kind="file", src="/x", dst=dst)

    def test_case_typo_of_workspace_rejected_with_hint(self) -> None:
        # The real footgun: /Workspace (capital W) is a useless sibling of /workspace.
        with self.assertRaises(ValidationError) as cm:
            EnvMount(kind="file", src="/x", dst="/Workspace/CLAUDE.md")
        self.assertIn("/workspace/CLAUDE.md", str(cm.exception))  # the "did you mean" suggestion
        with self.assertRaises(ValidationError):
            EnvMount(kind="file", src="/x", dst="/Home/agent/.netrc")

    def test_relative_and_traversal_dst_rejected(self) -> None:
        for dst in ("relative/path", "/home/agent/../../etc/passwd"):
            with self.assertRaises(ValidationError):
                EnvMount(kind="file", src="/x", dst=dst)

    def test_relative_src_rejected(self) -> None:
        # A relative/unexpanded src would become a docker named volume, not a host bind.
        with self.assertRaises(ValidationError):
            EnvMount(kind="file", src="relative/file", dst="/home/agent/.netrc")
        with self.assertRaises(ValidationError):
            EnvMount(kind="file", src="$DEFINITELY_UNSET_VAR/x", dst="/home/agent/.netrc")

    def test_home_dotfile_allowed(self) -> None:
        self.assertEqual(EnvMount(kind="file", src="/x", dst="/home/agent/.netrc").dst,
                         "/home/agent/.netrc")
        self.assertEqual(EnvMount(kind="file", src="/x", dst="/home/agent/.gitconfig").dst,
                         "/home/agent/.gitconfig")

    def test_lenient_flags_invalid_instead_of_raising(self) -> None:
        # Strict construction raises; lenient (load path) flags + preserves the data, never raises.
        m = EnvMount.lenient(kind="file", src="/x", dst="/Workspace/CLAUDE.md")
        self.assertTrue(m.error)
        self.assertIn("did you mean", m.error)
        self.assertEqual(m.dst, "/Workspace/CLAUDE.md")   # preserved for display + round-trip
        self.assertEqual(m.target, "/Workspace/CLAUDE.md")  # removable by its target

    def test_lenient_valid_mount_has_no_error(self) -> None:
        self.assertEqual(EnvMount.lenient(kind="file", src="/x", dst="/home/agent/.netrc").error, "")
        self.assertEqual(EnvMount.lenient(kind="ssh").error, "")


class LaunchWorkdirTest(unittest.TestCase):
    def test_no_repos_is_workspace(self) -> None:
        self.assertEqual(Project(slug="p").launch_workdir, "/workspace")

    def test_lone_repo_is_workspace_too(self) -> None:
        # The lone-repo auto-cd was dropped (docs/PACKS.md): /workspace is the uniform anchor so
        # the injected CLAUDE.md is what you land on; an explicit workdir restores the old feel.
        p = Project(slug="p", repos=(Repo(url="git@github.com:o/svc.git"),))
        self.assertEqual(p.launch_workdir, "/workspace")

    def test_multiple_repos_is_workspace(self) -> None:
        p = Project(slug="p", repos=(Repo(url="git@github.com:o/a.git"),
                                     Repo(url="git@github.com:o/b.git")))
        self.assertEqual(p.launch_workdir, "/workspace")

    def test_explicit_workdir_relative_and_absolute(self) -> None:
        lone = (Repo(url="git@github.com:o/svc.git"),)
        self.assertEqual(Project(slug="p", workdir="api", repos=lone).launch_workdir, "/workspace/api")
        self.assertEqual(Project(slug="p", workdir="/workspace", repos=lone).launch_workdir, "/workspace")


class ValidationTest(unittest.TestCase):
    def test_bad_slug_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Project(slug="Bad Slug!")

    def test_forbidden_env_key_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Project(slug="x", env={"ANTHROPIC_API_KEY": "sk-leak"})

    def test_bad_overlay_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Project(slug="x", overlay="haskell")


class RepoContainmentTest(unittest.TestCase):
    """schema.Repo.__post_init__ is the load-bearing guard: it must reject any escaping dir on
    construction, so the TUI/CLI add paths and the TOML loader all inherit it."""

    def test_empty_url_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Repo(url="   ")

    def test_parent_traversal_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Repo(url="git@github.com:o/r.git", dir="../../etc")

    def test_absolute_dir_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Repo(url="git@github.com:o/r.git", dir="/etc/cron.d")

    def test_tilde_dir_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Repo(url="git@github.com:o/r.git", dir="~/secrets")

    def test_nested_dotdot_component_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Repo(url="git@github.com:o/r.git", dir="a/../../b")

    def test_plain_subdir_ok(self) -> None:
        self.assertEqual(Repo(url="git@github.com:o/r.git", dir="nested/path").resolved_dir(),
                         "nested/path")


if __name__ == "__main__":
    unittest.main()
