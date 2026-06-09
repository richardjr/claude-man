"""Build claude-man container images and check which ones exist locally.

Pulled out of ``cli.py`` so the lifecycle pre-flight can auto-build a missing image before
``docker create`` (which otherwise fails opaquely with "Unable to find image 'claude-man:node'
locally … pull access denied" — there is no remote ``claude-man`` repo; the tags are local-only).

Importable without ``textual``. The build can stream docker's output line-by-line to a callback so
the TUI surfaces progress in its log pane and the CLI prints it; ``build_argv`` is a pure renderer
so the build command can be unit-tested without a daemon (same pattern as ``runner.build_create_argv``).

Two distinct verbs, deliberately:
  * ``build_one``   — always (re)builds exactly one overlay. The explicit ``image build`` semantics.
  * ``ensure_chain``— builds only the *missing* images in the base→overlay chain. The pre-flight
    used by the lifecycle, so an existing image is never needlessly rebuilt.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from .. import config
from . import labels

# A progress sink: each docker-output (or milestone) line is handed to it. The TUI forwards to its
# RichLog via ``call_from_thread``; the CLI uses ``print``.
ProgressFn = Callable[[str], None]

# Serialize concurrent builds (the TUI runs create/up/recreate on threads). Without it, starting two
# projects on different overlays inside the one-time base-build window would have both check
# ``image_exists('base')`` -> False and each spawn ``docker build -t claude-man:base`` — wasted CPU
# plus a dangling untagged image. Holding it across the whole chain makes check-then-build atomic so
# a base layer shared by several overlays is built exactly once (review).
_BUILD_LOCK = threading.Lock()


@dataclass
class BuildResult:
    ok: bool
    built: list[str] = field(default_factory=list)  # overlays actually (re)built this call
    detail: str = ""


def build_chain(overlay: str) -> list[str]:
    """The overlays that must exist for ``overlay``, base-first.

    Overlay images are ``FROM claude-man:base`` (see images/overlays/*.Dockerfile), so the base
    layer must be built before any overlay. ``base`` needs only itself.
    """
    return ["base"] if overlay == "base" else ["base", overlay]


def build_argv(overlay: str, claude_version: str = config.DEFAULT_CLAUDE_VERSION) -> list[str]:
    """Pure renderer for the ``docker build`` argv (no daemon, no IO) — unit-testable.

    Uses absolute, package-relative paths for the Dockerfile and build context so the command is
    CWD-independent. The Dockerfiles ``COPY`` nothing, so the context is unused beyond being valid.
    """
    return [
        "docker", "build",
        "-f", str(config.image_dockerfile(overlay)),
        "--build-arg", f"CLAUDE_VERSION={claude_version}",
        "-t", config.image_tag(overlay),
        str(config.repo_root()),
    ]


def image_exists(overlay: str) -> bool:
    """True iff ``claude-man:<overlay>`` is present locally (``docker image inspect``).

    Returns False (rather than raising) when docker isn't installed, so callers can treat
    "no docker" the same as "not built" and surface one clear message.
    """
    if shutil.which("docker") is None:
        return False
    return subprocess.run(
        ["docker", "image", "inspect", config.image_tag(overlay)],
        capture_output=True, check=False,
    ).returncode == 0


def image_claude_version(overlay: str) -> str | None:
    """The claude version baked into ``claude-man:<overlay>`` (its ``claude-man.claude-version`` label),
    or ``None`` if the image / label / docker is absent.

    The label is the source of truth for "what claude a container created from this image runs".
    Overlays inherit the label from their base (they don't re-declare it), so this works for any
    overlay. Used by the lifecycle to stamp the container's version truthfully and to decide the
    on-start update (compare against the channel's latest)."""
    if shutil.which("docker") is None:
        return None
    cp = subprocess.run(
        ["docker", "image", "inspect", config.image_tag(overlay),
         "--format", '{{ index .Config.Labels "%s" }}' % labels.IMAGE_VERSION],
        capture_output=True, text=True, check=False,
    )
    if cp.returncode != 0:
        return None
    v = cp.stdout.strip()
    # Go's `index` prints "<no value>" for a missing key; treat that (and empty) as unknown.
    return v if v and v != "<no value>" else None


def build_one(
    overlay: str,
    *,
    claude_version: str = config.DEFAULT_CLAUDE_VERSION,
    dry_run: bool = False,
    on_line: ProgressFn | None = None,
) -> int:
    """Build exactly ``claude-man:<overlay>`` (always, even if it already exists). Returns the rc.

    With ``on_line`` set, docker's combined output is captured and streamed line-by-line to it
    (BuildKit auto-selects plain, line-oriented output when stdout isn't a TTY). Without it, docker
    inherits the parent stdio so an interactive operator keeps the normal progress UI.
    """
    argv = build_argv(overlay, claude_version)
    emit = on_line or print
    emit("+ " + " ".join(argv))
    if dry_run:
        return 0
    if shutil.which("docker") is None:
        emit("docker not found on PATH")
        return 127
    if on_line is None:
        return subprocess.run(argv, check=False).returncode
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line.rstrip("\n"))
    return proc.wait()


def ensure_chain(
    overlay: str,
    *,
    claude_version: str = config.DEFAULT_CLAUDE_VERSION,
    on_line: ProgressFn | None = None,
) -> BuildResult:
    """Build any *missing* image in the base→``overlay`` chain; leave existing ones untouched.

    This is the lifecycle pre-flight: it makes the project's image exist before ``docker create`` so
    the operator never has to run ``image build`` by hand. Already-present images are skipped (no
    needless rebuild). Returns ``ok=False`` with a clear detail when docker is absent or a build fails.
    """
    if shutil.which("docker") is None:
        return BuildResult(False, detail="docker not found on PATH — cannot build images")
    built: list[str] = []
    # Held across the loop so the existence check + build is atomic vs. a concurrent worker (see
    # ``_BUILD_LOCK``). A waiting thread re-checks ``image_exists`` after the holder finishes and
    # skips the now-present layer.
    with _BUILD_LOCK:
        for ov in build_chain(overlay):
            if image_exists(ov):
                continue
            if on_line:
                on_line(f"image {config.image_tag(ov)} not built — building it now "
                        f"(one-time, may take a minute) …")
            rc = build_one(ov, claude_version=claude_version, on_line=on_line)
            if rc != 0:
                return BuildResult(
                    False, built,
                    detail=f"failed to build {config.image_tag(ov)} (docker build exited {rc})",
                )
            built.append(ov)
    if built:
        return BuildResult(True, built,
                           detail="built " + ", ".join(config.image_tag(b) for b in built))
    return BuildResult(True, built, detail="images already present")


def rebuild_chain(
    overlay: str,
    *,
    claude_version: str,
    on_line: ProgressFn | None = None,
) -> BuildResult:
    """Force-rebuild the base→``overlay`` chain pinned to ``claude_version`` — even when the images
    already exist (unlike ``ensure_chain``, which only builds *missing* ones).

    The on-start update path: a newer claude lands by rebuilding the base (its ``CLAUDE_VERSION``
    build-arg) then the overlay (so its ``FROM claude-man:base`` picks up the freshly-built base). Held
    under ``_BUILD_LOCK`` so it can't race a concurrent create/up. ``docker build`` only repoints the
    tag on SUCCESS, so a mid-chain failure leaves the prior image intact — the caller falls open and
    starts on it. Returns ``ok=False`` with a clear detail when docker is absent or a build fails."""
    if shutil.which("docker") is None:
        return BuildResult(False, detail="docker not found on PATH — cannot rebuild image")
    built: list[str] = []
    with _BUILD_LOCK:
        for ov in build_chain(overlay):
            if on_line:
                on_line(f"rebuilding {config.image_tag(ov)} → claude {claude_version} …")
            rc = build_one(ov, claude_version=claude_version, on_line=on_line)
            if rc != 0:
                return BuildResult(
                    False, built,
                    detail=f"failed to rebuild {config.image_tag(ov)} (docker build exited {rc})",
                )
            built.append(ov)
    return BuildResult(True, built,
                       detail=f"rebuilt {', '.join(config.image_tag(b) for b in built)} "
                       f"→ claude {claude_version}")
