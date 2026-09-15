"""Approved-tool registry — parsing + validation (tmp libraries), the shipped-library lint, the
requires closure, the Dockerfile render + content-addressed image name, and the config name parse.
Pure: no docker, no network."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claudeman import config  # noqa: E402
from claudeman.tools import library, render  # noqa: E402

SHA = "0" * 64
APT_HEAD = 'description = "jq"\nkind = "apt"\npackages = ["jq"]\n'
APT_SMOKE = '[[smoke]]\nname = "jq"\nargv = ["jq", "--version"]\n'
APT_TOOL = APT_HEAD + APT_SMOKE


def _apt(extra: str = "", pkg: str = "jq") -> str:
    """An apt entry with ``extra`` top-level keys/tables placed BEFORE the [[smoke]] table (a
    trailing key would otherwise nest inside it in TOML)."""
    return APT_HEAD.replace('"jq"', f'"{pkg}"') + extra + APT_SMOKE
BIN_TOOL = f'''description = "kubectl"
kind = "release"
version = "1.37.0"
install = "binary"
bin = "kubectl"
allowlist = [".amazonaws.com"]
[release.amd64]
url = "https://dl.k8s.io/amd64/kubectl"
sha256 = "{SHA}"
[release.arm64]
url = "https://dl.k8s.io/arm64/kubectl"
sha256 = "{SHA}"
[env]
KUBECONFIG = "/home/agent/.cache/kube/config"
[[smoke]]
name = "version"
argv = ["kubectl", "version", "--client"]
expect = "Client Version"
timeout = 20
'''
TAR_TOOL = f'''description = "helm"
kind = "release"
version = "3.22.0"
install = "tar"
build_deps = ["unzip"]
[release.amd64]
url = "https://get.helm.sh/helm-amd64.tar.gz"
sha256 = "{SHA}"
members = ["linux-amd64/helm"]
[release.arm64]
url = "https://get.helm.sh/helm-arm64.tar.gz"
sha256 = "{SHA}"
members = ["linux-arm64/helm"]
'''


def _mk(root: Path, name: str, text: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / library.TOOL_META).write_text(text)


class DiscoverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_root_is_empty(self) -> None:
        self.assertEqual(library.discover(self.root / "nope"), {})

    def test_parses_apt_binary_and_tar(self) -> None:
        _mk(self.root, "jq", APT_TOOL)
        _mk(self.root, "kubectl", BIN_TOOL)
        _mk(self.root, "helm", TAR_TOOL)
        lib = library.discover(self.root)
        self.assertEqual(list(lib), ["helm", "jq", "kubectl"])  # sorted by name
        jq, k, h = lib["jq"], lib["kubectl"], lib["helm"]
        self.assertEqual((jq.kind, jq.packages, jq.summary), ("apt", ("jq",), "apt: jq"))
        self.assertEqual((k.kind, k.install, k.bin, k.version), ("release", "binary", "kubectl", "1.37.0"))
        self.assertEqual(k.release["arm64"].url, "https://dl.k8s.io/arm64/kubectl")
        self.assertEqual(k.env, {"KUBECONFIG": "/home/agent/.cache/kube/config"})
        self.assertEqual(k.allowlist, (".amazonaws.com",))
        self.assertEqual(k.smoke[0].timeout, 20)
        self.assertEqual(k.smoke[0].expect, "Client Version")
        self.assertEqual(h.release["amd64"].members, ("linux-amd64/helm",))
        self.assertEqual(h.build_deps, ("unzip",))
        self.assertEqual(h.summary, "release 3.22.0")

    def _bad(self, name: str, text: str, msg: str) -> None:
        with tempfile.TemporaryDirectory() as t:
            _mk(Path(t), name, text)
            with self.assertRaises(library.LibraryError, msg=msg) as ctx:
                library.discover(Path(t))
            self.assertIn(msg, str(ctx.exception))

    def test_validation_rejects_malformed_entries(self) -> None:
        self._bad("Bad Name", APT_TOOL, "invalid tool name")
        self._bad("x", 'kind = "apt"\npackages = ["jq"]\n', "description")
        self._bad("x", 'description = "d"\nkind = "snap"\n', "kind must be")
        self._bad("x", 'description = "d"\nkind = "apt"\n', "must list packages")
        self._bad("x", 'description = "d"\nkind = "apt"\npackages = ["jq; rm -rf /"]\n', "invalid apt package")
        self._bad("x", 'description = "d"\nkind = "apt"\npackages = ["jq"]\nversion = "1"\n', "only valid for kind")
        self._bad("x", BIN_TOOL.replace('version = "1.37.0"\n', ""), "pin a version")
        self._bad("x", BIN_TOOL.replace('install = "binary"', 'install = "rpm"'), "install must be")
        self._bad("x", BIN_TOOL.replace('bin = "kubectl"\n', ""), "needs a valid `bin`")
        self._bad("x", BIN_TOOL.replace("[release.arm64]", "[release.riscv]"), "missing [release.arm64]")
        self._bad("x", BIN_TOOL.replace("https://dl.k8s.io/amd64", "http://dl.k8s.io/amd64"), "plain https URL")
        self._bad("x", BIN_TOOL.replace("https://dl.k8s.io/amd64/kubectl", "https://x/'; rm -rf /"), "plain https URL")
        self._bad("x", BIN_TOOL.replace(SHA, "abc"), "64-hex sha256")
        self._bad("x", TAR_TOOL.replace('members = ["linux-amd64/helm"]\n', ""), "needs `members`")
        self._bad("x", TAR_TOOL.replace('"linux-amd64/helm"', '"../../etc/helm"'), "bad tar member")
        self._bad("x", BIN_TOOL.replace("sha256 = ", 'members = ["a"]\nsha256 = '), "only valid for install")
        self._bad("x", _apt('requires = ["x"]\n'), "requires itself")
        self._bad("x", _apt('requires = ["ghost"]\n'), "requires unknown tool")
        self._bad("x", _apt('[env]\nHOME = "/x"\n'), "reserved")
        self._bad("x", _apt('[env]\nANTHROPIC_API_KEY = "sk"\n'), "reserved")
        self._bad("x", _apt('[env]\nlower = "v"\n'), "invalid env key")
        self._bad("x", APT_TOOL + '[[smoke]]\nname = "n"\nargv = []\n', "non-empty argv")
        self._bad("x", APT_TOOL + '[[smoke]]\nname = "n"\nargv = ["a"]\ntimeout = 0\n', "positive integer")

    def test_requires_cycle_rejected(self) -> None:
        _mk(self.root, "a", _apt('requires = ["b"]\n'))
        _mk(self.root, "b", _apt('requires = ["a"]\n'))
        with self.assertRaises(library.LibraryError) as ctx:
            library.discover(self.root)
        self.assertIn("cycle", str(ctx.exception))


class ResolveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        _mk(root, "python3", _apt(pkg="python3"))
        _mk(root, "python3-yaml", _apt('requires = ["python3"]\n', pkg="python3-yaml"))
        _mk(root, "jq", APT_TOOL)
        self.lib = library.discover(root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_closure_in_library_order(self) -> None:
        got = [t.name for t in library.resolve(("python3-yaml",), self.lib)]
        self.assertEqual(got, ["python3", "python3-yaml"])  # dependency pulled in, sorted
        got = [t.name for t in library.resolve(("jq", "python3-yaml", "python3"), self.lib)]
        self.assertEqual(got, ["jq", "python3", "python3-yaml"])  # same set -> same order

    def test_unknown_name_names_it(self) -> None:
        with self.assertRaises(library.LibraryError) as ctx:
            library.resolve(("jq", "ghost"), self.lib)
        self.assertIn("ghost", str(ctx.exception))

    def test_merged_env_conflict_is_an_error(self) -> None:
        a = library.Tool(name="a", description="a", kind="apt", path=Path("/x"), packages=("a",),
                         env={"K": "1"})
        b = library.Tool(name="b", description="b", kind="apt", path=Path("/x"), packages=("b",),
                         env={"K": "2", "Z": "z"})
        same = library.Tool(name="c", description="c", kind="apt", path=Path("/x"), packages=("c",),
                            env={"K": "1"})
        self.assertEqual(library.merged_env((a, same)), {"K": "1"})
        with self.assertRaises(library.LibraryError):
            library.merged_env((a, b))


class RenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _mk(self.root, "jq", APT_TOOL)
        _mk(self.root, "kubectl", BIN_TOOL)
        _mk(self.root, "helm", TAR_TOOL)
        _mk(self.root, "ssm", BIN_TOOL.replace('install = "binary"\nbin = "kubectl"\n', 'install = "deb"\n')
            .replace('description = "kubectl"', 'description = "ssm"'))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dockerfile_shape(self) -> None:
        name, text, tools = render.plan("terraform", ("kubectl", "helm", "jq", "ssm"), root=self.root)
        self.assertEqual([t.name for t in tools], ["helm", "jq", "kubectl", "ssm"])
        self.assertTrue(text.startswith("# claude-man tools layer"))
        self.assertIn("FROM claude-man:terraform\n", text)
        self.assertIn('ENV KUBECONFIG="/home/agent/.cache/kube/config"', text)
        # apt packages + build-only deps in ONE install, deps purged at the end.
        self.assertIn("apt-get install -y --no-install-recommends jq unzip;", text)
        self.assertIn("apt-get purge -y unzip;", text)
        # every release artefact is sha256-verified before install; arch-aware.
        self.assertEqual(text.count("sha256sum -c -"), 3)
        self.assertIn('arch="$(dpkg --print-architecture)"', text)
        self.assertIn("install -m 0755 /tmp/claude-man-tool-kubectl.dl /usr/local/bin/kubectl", text)
        self.assertIn("members='linux-amd64/helm'", text)
        self.assertIn('install -m 0755 "/tmp/claude-man-tool-helm.x/$m"', text)
        self.assertIn("apt-get install -y --no-install-recommends /tmp/claude-man-tool-ssm.deb", text)
        self.assertIn('LABEL claude-man.overlay="terraform" claude-man.tools="helm,jq,kubectl,ssm"', text)
        self.assertTrue(text.rstrip().endswith("USER agent"))
        # content-addressed name: overlay + sep + 12 hex of the text
        self.assertTrue(name.startswith("terraform" + config.TOOLS_IMAGE_SEP))
        self.assertEqual(config.tools_image_overlay(name), "terraform")

    def test_name_is_deterministic_and_selection_sensitive(self) -> None:
        n1, _, _ = render.plan("base", ("jq", "kubectl"), root=self.root)
        n2, _, _ = render.plan("base", ("kubectl", "jq"), root=self.root)  # order irrelevant
        n3, _, _ = render.plan("base", ("jq",), root=self.root)
        n4, _, _ = render.plan("node", ("jq", "kubectl"), root=self.root)
        self.assertEqual(n1, n2)
        self.assertNotEqual(n1, n3)
        self.assertNotEqual(n1, n4)
        # a version bump changes the render, hence the tag (stale images never collide)
        (self.root / "kubectl" / library.TOOL_META).write_text(BIN_TOOL.replace("1.37.0", "1.38.0"))
        n5, _, _ = render.plan("base", ("jq", "kubectl"), root=self.root)
        self.assertNotEqual(n1, n5)

    def test_render_refuses_bad_inputs(self) -> None:
        with self.assertRaises(library.LibraryError):
            render.plan("haskell", ("jq",), root=self.root)
        with self.assertRaises(library.LibraryError):
            render.plan("base", ("ghost",), root=self.root)
        with self.assertRaises(library.LibraryError):
            render.render_dockerfile("base", ())

    def test_materialize_writes_state_tier_dockerfile(self) -> None:
        with tempfile.TemporaryDirectory() as state:
            os.environ["CLAUDE_MAN_STATE_HOME"] = state
            try:
                name = render.materialize("base", ("jq",), root=self.root)
                path = config.tools_dockerfile_path(name)
                self.assertTrue(path.is_file())
                self.assertEqual(config.image_dockerfile(name), path)
                text = path.read_text()
                self.assertEqual(render.materialize("base", ("jq",), root=self.root), name)  # idempotent
                self.assertEqual(path.read_text(), text)
            finally:
                os.environ.pop("CLAUDE_MAN_STATE_HOME", None)


class ImageNameParseTest(unittest.TestCase):
    def test_tools_image_overlay(self) -> None:
        self.assertEqual(config.tools_image_overlay("python-node-t-0123456789ab"), "python-node")
        self.assertEqual(config.tools_image_overlay("base-t-0123456789ab"), "base")
        self.assertIsNone(config.tools_image_overlay("base"))
        self.assertIsNone(config.tools_image_overlay("python-node"))
        self.assertIsNone(config.tools_image_overlay("haskell-t-0123456789ab"))
        self.assertIsNone(config.tools_image_overlay("base-t-0123"))          # wrong length
        self.assertIsNone(config.tools_image_overlay("base-t-0123456789ZZ"))  # not hex
        self.assertIsNone(config.tools_image_overlay(config.PROXY_IMAGE))

    def test_plain_dockerfiles_unchanged(self) -> None:
        self.assertTrue(str(config.image_dockerfile("node")).endswith("images/overlays/node.Dockerfile"))


class ShippedLibraryLintTest(unittest.TestCase):
    """The in-repo registry must parse, resolve, and render cleanly (the curation lint)."""

    def test_shipped_library_is_valid(self) -> None:
        lib = library.discover()
        self.assertTrue(lib, "library/tools is empty?")
        for tool in lib.values():
            self.assertTrue(tool.smoke, f"{tool.name}: every shipped tool needs a smoke probe")
            if tool.kind == "release":
                for arch in library.ARCHES:
                    self.assertIn(arch, tool.release)
        # the infra selection from the issue renders on every overlay it could sit on
        for overlay in ("base", "terraform"):
            name, text, _ = render.plan(overlay, tuple(lib), )
            self.assertEqual(config.tools_image_overlay(name), overlay)
            self.assertIn("sha256sum -c -", text)

    def test_shipped_entries_carry_the_floor_redirects(self) -> None:
        lib = library.discover()
        self.assertIn("KUBECONFIG", lib["kubectl"].env)
        self.assertIn("HELM_CONFIG_HOME", lib["helm"].env)
        self.assertEqual(lib["python3-yaml"].requires, ("python3",))


if __name__ == "__main__":
    unittest.main()
