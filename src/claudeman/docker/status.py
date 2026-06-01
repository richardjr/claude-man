"""Live container status, read fresh from ``docker ps`` and JOINed with the registry.

The registry answers *what a project is*; docker answers *what state its container
is in right now*. Status is NEVER cached. A project with a TOML and no container is
DEFINED; with a stopped container STOPPED; running UP.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from . import labels

DEFINED = "DEFINED"   # registry has it, no container
STOPPED = "STOPPED"   # container exists, not running
UP = "UP"             # container running


@dataclass(frozen=True)
class ContainerStatus:
    slug: str
    state: str           # docker State: "running", "exited", "created", ...
    status_text: str     # docker Status: "Up 6 hours", "Exited (0) 2m ago", ...
    profile: str = ""
    overlay: str = ""
    egress: str = ""
    repos: str = ""
    version: str = ""

    @property
    def kind(self) -> str:
        return UP if self.state == "running" else STOPPED


@dataclass(frozen=True)
class Row:
    """One joined row for the TUI table."""
    slug: str
    kind: str            # DEFINED | STOPPED | UP
    profile: str
    egress: str
    repos: str
    version: str
    status_text: str


def query_containers() -> dict[str, ContainerStatus]:
    """Map slug -> ContainerStatus for every claude-man container (running or not)."""
    cp = subprocess.run(
        ["docker", "ps", "-a", "--filter", labels.SELECTOR, "--format", "{{json .}}"],
        capture_output=True, text=True, check=False,
    )
    out: dict[str, ContainerStatus] = {}
    if cp.returncode != 0:
        return out
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        lbls = _parse_label_csv(rec.get("Labels", ""))
        slug = lbls.get(labels.SLUG)
        if not slug:
            continue
        out[slug] = ContainerStatus(
            slug=slug,
            state=rec.get("State", ""),
            status_text=rec.get("Status", ""),
            profile=lbls.get(labels.PROFILE, ""),
            overlay=lbls.get(labels.OVERLAY, ""),
            egress=lbls.get(labels.EGRESS, ""),
            repos=lbls.get(labels.REPOS, ""),
            version=lbls.get(labels.VERSION, ""),
        )
    return out


def _parse_label_csv(raw: str) -> dict[str, str]:
    """`docker ps` flattens labels to a CSV of k=v pairs. Parse the claude-man.* ones."""
    result: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k] = v
    return labels.parse(result)


def join(defined_slugs, containers: dict[str, ContainerStatus]) -> list[Row]:
    """Outer-join the registry's defined projects with live container status.

    ``defined_slugs`` is an iterable of (slug, profile, egress, repos_count) tuples
    from the registry, so DEFINED projects with no container still appear.
    """
    rows: list[Row] = []
    seen: set[str] = set()
    for slug, profile, egress, repos in defined_slugs:
        seen.add(slug)
        cs = containers.get(slug)
        if cs is None:
            rows.append(Row(slug, DEFINED, profile or "", egress or "", str(repos), "", ""))
        else:
            rows.append(
                Row(slug, cs.kind, cs.profile or profile or "", cs.egress or egress or "",
                    cs.repos or str(repos), cs.version, cs.status_text)
            )
    # Containers with no registry entry (orphans) — surface them so they can be reconciled.
    for slug, cs in containers.items():
        if slug not in seen:
            rows.append(
                Row(slug, cs.kind, cs.profile, cs.egress, cs.repos, cs.version,
                    cs.status_text + " (orphan: no registry entry)")
            )
    rows.sort(key=lambda r: r.slug)
    return rows
