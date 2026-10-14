"""Approved-tool registry — parsing + validation (tmp libraries), the shipped-library lint, the
requires closure, the Dockerfile render + content-addressed image name, and the config name parse.
Pure: no docker, no network."""

from __future__ import annotations

import hashlib
import json
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
BUNDLE_TOOL = f'''description = "aws"
kind = "release"
version = "2.36.47"
install = "bundle"
tree = "aws/dist"
bins = ["aws", "aws_completer"]
build_deps = ["unzip"]
[release.amd64]
url = "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.36.47.zip"
sha256 = "{SHA}"
[release.arm64]
url = "https://awscli.amazonaws.com/awscli-exe-linux-aarch64-2.36.47.zip"
sha256 = "{SHA}"
[env]
AWS_CONFIG_FILE = "/home/agent/.cache/aws/config"
AWS_PAGER = ""
'''


NPM_TOOL = """description = "mmdc"
kind = "npm"
package = "@scope/cli"
version = "1.2.3"
files = ["bin/run"]
bins = ["bin/run"]
[[smoke]]
name = "v"
argv = ["run", "--version"]
"""


def _mk(root: Path, name: str, text: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / library.TOOL_META).write_text(text)


def _lock(deps: dict, entries: dict | None = None) -> dict:
    """A lockfile-v3 shape for ``deps`` — every entry registry-resolved with a sha512 integrity."""
    pk = {"": {"dependencies": deps}}
    for dep, ver in deps.items():
        pk[f"node_modules/{dep}"] = {"version": ver, "integrity": "sha512-x",
                                     "resolved": f"{library.NPM_REGISTRY}{dep}/-/x-{ver}.tgz"}
    pk.update(entries or {})
    return {"name": "t", "lockfileVersion": 3, "requires": True, "packages": pk}


def _mk_npm(root: Path, name: str = "mmdc", text: str = NPM_TOOL, manifest: dict | None = None,
            lock: dict | None = None, launcher: bool = True) -> Path:
    deps = {"@scope/cli": "1.2.3", "peer": "4.5.6"}
    manifest = {"private": True, "dependencies": deps} if manifest is None else manifest
    _mk(root, name, text)
    d = root / name
    (d / "package.json").write_text(json.dumps(manifest))
    (d / "package-lock.json").write_text(json.dumps(_lock(deps) if lock is None else lock))
    if launcher:
        (d / "bin").mkdir()
        (d / "bin" / "run").write_text("#!/bin/sh\nexec node_modules/.bin/cli \"$@\"\n")
    return d


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

    def test_parses_bundle(self) -> None:
        _mk(self.root, "aws-cli", BUNDLE_TOOL)
        a = library.discover(self.root)["aws-cli"]
        self.assertEqual(a.env["AWS_PAGER"], "")  # empty env values are legal
        self.assertEqual((a.kind, a.install, a.version), ("release", "bundle", "2.36.47"))
        self.assertEqual(a.tree, "aws/dist")
        self.assertEqual(a.bins, ("aws", "aws_completer"))
        self.assertEqual(a.build_deps, ("unzip",))
        self.assertEqual(a.release["amd64"].members, ())
        self.assertEqual(a.summary, "release 2.36.47")

    def test_bundle_validation(self) -> None:
        self._bad("x", BUNDLE_TOOL.replace('tree = "aws/dist"\n', ""), "needs a valid archive `tree`")
        self._bad("x", BUNDLE_TOOL.replace('tree = "aws/dist"', 'tree = "../etc"'), "needs a valid archive `tree`")
        self._bad("x", BUNDLE_TOOL.replace('tree = "aws/dist"', 'tree = "/aws/dist"'), "needs a valid archive `tree`")
        self._bad("x", BUNDLE_TOOL.replace('bins = ["aws", "aws_completer"]\n', ""),
                  "needs `bins`")
        self._bad("x", BUNDLE_TOOL.replace('"aws_completer"', '"../../bin/sh"'), "bad bundle bin")
        self._bad("x", BUNDLE_TOOL.replace('"aws_completer"', '"$(id)"'), "bad bundle bin")
        # a bundle is a zip: the builder needs unzip, purged after (build_deps)
        self._bad("x", BUNDLE_TOOL.replace('build_deps = ["unzip"]\n', ""), 'build_deps = ["unzip"]')
        # tree/bins are bundle-only; members are tar-only
        self._bad("x", BIN_TOOL.replace('bin = "kubectl"\n', 'bin = "kubectl"\ntree = "a"\n'),
                  "only valid for install")
        self._bad("x", BUNDLE_TOOL.replace("sha256 = ", 'members = ["a"]\nsha256 = '), "only valid for install")
        self._bad("x", _apt('tree = "a"\n'), "not valid for kind")

    def test_validation_rejects_malformed_entries(self) -> None:
        self._bad("Bad Name", APT_TOOL, "invalid tool name")
        self._bad("x", 'kind = "apt"\npackages = ["jq"]\n', "description")
        self._bad("x", 'description = "d"\nkind = "snap"\n', "kind must be")
        self._bad("x", 'description = "d"\nkind = "apt"\n', "must list packages")
        self._bad("x", 'description = "d"\nkind = "apt"\npackages = ["jq; rm -rf /"]\n', "invalid apt package")
        self._bad("x", 'description = "d"\nkind = "apt"\npackages = ["jq"]\nversion = "1"\n', "not valid for kind")
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
        self._bad("x", _apt('[env]\nK = 1\n'), "single-line string")
        self._bad("x", _apt('[env]\nK = "a\\nb"\n'), "single-line string")
        self._bad("x", APT_TOOL + '[[smoke]]\nname = "n"\nargv = []\n', "non-empty argv")
        self._bad("x", APT_TOOL + '[[smoke]]\nname = "n"\nargv = ["a"]\ntimeout = 0\n', "positive integer")

    def test_parses_npm(self) -> None:
        d = _mk_npm(self.root)
        m = library.discover(self.root)["mmdc"]
        self.assertEqual((m.kind, m.package, m.version, m.bins), ("npm", "@scope/cli", "1.2.3", ("bin/run",)))
        self.assertEqual(m.summary, "npm 1.2.3")
        # the manifests are always shipped, the declared extras after them; each with its sha256
        self.assertEqual([rel for rel, _ in m.files], ["package.json", "package-lock.json", "bin/run"])
        self.assertEqual(dict(m.files)["package-lock.json"],
                         hashlib.sha256((d / "package-lock.json").read_bytes()).hexdigest())

    def _bad_npm(self, msg: str, **kw) -> None:
        with tempfile.TemporaryDirectory() as t:
            _mk_npm(Path(t), **kw)
            with self.assertRaises(library.LibraryError, msg=msg) as ctx:
                library.discover(Path(t))
            self.assertIn(msg, str(ctx.exception))

    def test_npm_validation(self) -> None:
        deps = {"@scope/cli": "1.2.3", "peer": "4.5.6"}
        self._bad_npm("exact x.y.z `version`", text=NPM_TOOL.replace('"1.2.3"', '"^1.2.3"'))
        self._bad_npm("valid `package`", text=NPM_TOOL.replace('"@scope/cli"', '"Bad Name"'))
        self._bad_npm("not valid for kind", text=NPM_TOOL.replace('kind = "npm"\n', 'kind = "npm"\ninstall = "tar"\n'))
        self._bad_npm("needs `bins`", text=NPM_TOOL.replace('bins = ["bin/run"]\n', ""))
        # a bin must be a shipped file or an npm-linked executable — never an arbitrary path
        self._bad_npm("must be under node_modules/.bin/", text=NPM_TOOL.replace('bins = ["bin/run"]', 'bins = ["bin/other"]'))
        self._bad_npm("missing or unsafe shipped file 'bin/run'", launcher=False)
        self._bad_npm("unsafe shipped file", text=NPM_TOOL.replace('files = ["bin/run"]', 'files = ["../x"]'))
        # package.json: exact pins only, the tool's own pin must match, no scripts/extra keys
        self._bad_npm("exact x.y.z only", manifest={"dependencies": {**deps, "peer": "^4.5.6"}})
        self._bad_npm("must pin @scope/cli = '1.2.3'", manifest={"dependencies": {**deps, "@scope/cli": "1.2.4"}})
        self._bad_npm("only `private` + `dependencies`", manifest={"dependencies": deps, "scripts": {"x": "y"}})
        # the lockfile must be in step with package.json and fully registry + sha512 pinned
        self._bad_npm("stale vs package.json", lock=_lock({"@scope/cli": "1.2.3"}))
        self._bad_npm("sha512 integrity", lock=_lock(deps, {"node_modules/x": {"version": "1.0.0",
                      "resolved": "https://evil.example/x.tgz", "integrity": "sha512-x"}}))
        self._bad_npm("sha512 integrity", lock=_lock(deps, {"node_modules/x": {"version": "1.0.0",
                      "resolved": f"{library.NPM_REGISTRY}x/-/x-1.0.0.tgz", "integrity": "sha1-x"}}))
        self._bad_npm("sha512 integrity", lock=_lock(deps, {"node_modules/x": {"link": True}}))
        with tempfile.TemporaryDirectory() as t:
            (_mk_npm(Path(t)) / "package-lock.json").write_text("{not json")
            with self.assertRaises(library.LibraryError):
                library.discover(Path(t))

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
        _mk(self.root, "aws-cli", BUNDLE_TOOL)
        _mk_npm(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_npm_render(self) -> None:
        name, text, _ = render.plan("base", ("mmdc", "kubectl"), root=self.root)
        lib = library.discover(self.root)
        # each shipped file COPYd to the same relative path under /opt/<name>, then its bytes re-verified
        self.assertIn("COPY library/tools/mmdc/package-lock.json /opt/mmdc/package-lock.json\n", text)
        self.assertIn("COPY library/tools/mmdc/bin/run /opt/mmdc/bin/run\n", text)
        for rel, sha in lib["mmdc"].files:
            self.assertIn(f"'{sha}' '{rel}'", text)
        # lockfile-pinned install with NO package scripts run at build; the cache never ships
        self.assertIn("npm ci --omit=dev --ignore-scripts --no-audit --no-fund --cache /tmp/claude-man-tool-mmdc.npm", text)
        self.assertIn('ln -sfn "/opt/mmdc/bin/run" "/usr/local/bin/run"', text)
        # npm tools install after the release tools (a release-installed runtime could serve them)
        self.assertLess(text.index("kubectl 1.37.0 (release"), text.index("mmdc 1.2.3 (npm"))
        # the lockfile's sha is in the text, so changing it is a new content-addressed tag
        lock = self.root / "mmdc" / "package-lock.json"
        lock.write_text(lock.read_text().replace('"t"', '"t2"'))
        self.assertNotEqual(render.plan("base", ("mmdc", "kubectl"), root=self.root)[0], name)

    def test_dockerfile_shape(self) -> None:
        name, text, tools = render.plan("terraform", ("kubectl", "helm", "jq", "ssm", "aws-cli"),
                                        root=self.root)
        self.assertEqual([t.name for t in tools], ["aws-cli", "helm", "jq", "kubectl", "ssm"])
        self.assertTrue(text.startswith("# claude-man tools layer"))
        self.assertIn("FROM claude-man:terraform\n", text)
        self.assertIn('    KUBECONFIG="/home/agent/.cache/kube/config"', text)
        # apt packages + build-only deps in ONE install, deps purged at the end.
        self.assertIn("apt-get install -y --no-install-recommends jq unzip;", text)
        self.assertIn("apt-get purge -y unzip;", text)
        # every release artefact is sha256-verified before install; arch-aware.
        self.assertEqual(text.count("sha256sum -c -"), 4)
        self.assertIn('arch="$(dpkg --print-architecture)"', text)
        self.assertIn("install -m 0755 /tmp/claude-man-tool-kubectl.dl /usr/local/bin/kubectl", text)
        self.assertIn("members='linux-amd64/helm'", text)
        self.assertIn('install -m 0755 "/tmp/claude-man-tool-helm.x/$m"', text)
        self.assertIn("apt-get install -y --no-install-recommends /tmp/claude-man-tool-ssm.deb", text)
        # bundle: unzip the verified zip, the tree lands whole under /opt/<name>, bins symlinked by basename
        self.assertIn("unzip -q /tmp/claude-man-tool-aws-cli.dl -d /tmp/claude-man-tool-aws-cli.x", text)
        self.assertIn('cp -a "/tmp/claude-man-tool-aws-cli.x/aws/dist" /opt/aws-cli', text)
        # bins are TREE-relative: a missing one fails the build (test -x) instead of dangling
        self.assertIn('test -x "/opt/aws-cli/aws"; ln -sfn "/opt/aws-cli/aws" "/usr/local/bin/aws"', text)
        self.assertIn('ln -sfn "/opt/aws-cli/aws_completer" "/usr/local/bin/aws_completer"', text)
        self.assertIn('ENV AWS_CONFIG_FILE="/home/agent/.cache/aws/config" \\', text)
        # an empty value is legal (set-but-empty — AWS_PAGER="" disables the pager) and renders quoted
        self.assertIn('    AWS_PAGER="" \\', text)
        self.assertIn('LABEL claude-man.overlay="terraform" claude-man.tools="aws-cli,helm,jq,kubectl,ssm"',
                      text)
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
        # aws-cli: the bundle kind; its config/creds redirects match the terraform overlay's ENV values
        # (the two coexist on that overlay — merged_env would flag a mismatch only across tools, so pin
        # the overlay parity here).
        aws = lib["aws-cli"]
        self.assertEqual(aws.install, "bundle")
        self.assertEqual(aws.env, {"AWS_CONFIG_FILE": "/home/agent/.cache/aws/config",
                                   "AWS_SHARED_CREDENTIALS_FILE": "/home/agent/.cache/aws/credentials",
                                   "AWS_PAGER": ""})  # issue #41: no `less` in the image
        overlay = (Path(__file__).resolve().parents[1] / "images/overlays/terraform.Dockerfile").read_text()
        for key, value in aws.env.items():
            self.assertIn(f"{key}={value}" if value else f'{key}=""', overlay)
        # the TTY probe is what catches a pager regression (a plain exec never pages)
        self.assertTrue(any(s.argv[0] == "script" for s in aws.smoke), aws.smoke)
        # k9s rides kubectl's KUBECONFIG redirect and keeps its own state off the read-only ~/.config
        self.assertEqual(lib["k9s"].requires, ("kubectl",))
        self.assertIn("K9S_CONFIG_DIR", lib["k9s"].env)
        # mermaid (mmdr) rasterises PNG text, which needs a font the slim base doesn't have
        self.assertEqual(lib["mermaid"].requires, ("fonts-dejavu",))
        # mermaid-cli: the npm kind on the headless shell; Chromium has no usable sandbox under the
        # floor, so the launcher must always hand mmdc the --no-sandbox puppeteer config
        mmdc = lib["mermaid-cli"]
        self.assertEqual((mmdc.kind, mmdc.requires), ("npm", ("chromium-headless-shell",)))
        self.assertEqual(lib["chromium-headless-shell"].env["PUPPETEER_EXECUTABLE_PATH"],
                         "/usr/bin/chromium-headless-shell")
        self.assertIn("--no-sandbox", json.loads((mmdc.path / "puppeteer-config.json").read_text())["args"])
        self.assertIn("-p /opt/mermaid-cli/puppeteer-config.json", (mmdc.path / "bin" / "mmdc").read_text())


if __name__ == "__main__":
    unittest.main()
