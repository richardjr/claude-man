"""Change detection (Phase 5) — the three-way classifier feeding the review gate.

``detect_changes(slug)`` fingerprints the current container config bind (the denylist is enforced
INSIDE ``baseline.snapshot_container`` — before any content is read), then diffs each allowlisted
artifact against the start-of-session baseline at the granularity the kind warrants: per-FILE for
trees, per-KEY for ``settings.json``, per-SERVER for MCP. A unit the container changed is reported
ONLY when applying it would actually write something:

  * **no-op subtraction** — if the container's new value already equals the host's CURRENT value,
    nothing would be written, so it is dropped (covers "both sides changed to the same thing").
  * **claude-man's own writes** — a tree file byte-equal to the asset/pack SOURCE was written by
    sync-in / pack materialize, not the agent, so it is dropped (avoids flooding the gate after a
    mid-session ``packs add``).

A unit the HOST also changed since the baseline is flagged ``conflict=True`` (forced to a reject
default); a deletion likewise defaults to reject. Everything else inherits ``Artifact.default_decision``
(authored text → accept; settings.json / MCP → reject). With no baseline (first ever session, or a
deleted manifest) the asset/pack source is used as an implicit reference so only genuine agent adds
surface — never an auto-accept flood — and a fresh real baseline is written for next time.
"""

from __future__ import annotations

import filecmp
from dataclasses import dataclass

from .. import config
from . import artifacts, baseline


@dataclass(frozen=True)
class Change:
    artifact: str          # the artifact name: "skills" | "agents" | "commands" | "settings.json" | "mcp"
    kind: str              # tree | tree-symlink | json-keys | mcp | file
    scope: str             # user | project | repo
    change_type: str       # added | modified | deleted
    rel: str               # the per-unit id: tree → relpath; json-keys → top key; mcp → server; file → ""
    conflict: bool         # host ALSO changed this unit since the baseline → forced reject default
    default_decision: str  # accept | reject

    @property
    def label(self) -> str:
        """A compact human identifier for the gate (artifact + unit)."""
        return f"{self.artifact}/{self.rel}" if self.rel else self.artifact


def detect_changes(slug: str) -> list[Change]:
    """Classify every agent-authored change in ``slug``'s container config vs the baseline."""
    container = baseline.snapshot_container(slug)
    host_now = baseline.snapshot_host()
    base = baseline.read_baseline(slug)
    have_baseline = bool(base)
    if have_baseline:
        base_container = base.get("container", {})
        base_host = base.get("host", {})
    else:
        # No baseline (first ever session, or a deleted manifest): the implicit reference is what
        # claude-man itself wrote (asset/pack source for trees; current for settings/MCP), and the
        # host is its own reference (no drift). Persisting THIS (not the current agent-edited state)
        # keeps the agent's changes detectable on a follow-up apply that re-detects.
        base_container = {a.name: _implicit_baseline(slug, a, container.get(a.name, {}))
                          for a in artifacts.default_artifacts()}
        base_host = host_now

    changes: list[Change] = []
    for art in artifacts.default_artifacts():
        changes += _diff_artifact(
            slug, art,
            container.get(art.name, {}), base_container.get(art.name, {}),
            host_now.get(art.name, {}), base_host.get(art.name, {}),
        )

    if not have_baseline:
        try:
            baseline.write_manifest(slug, base_container, base_host)  # persist the implicit reference
        except OSError:
            pass
    return changes


def _implicit_baseline(slug: str, art: artifacts.Artifact, cur: dict) -> dict:
    """No-baseline reference for one artifact: the asset/pack SOURCE for trees (so claude-man's own
    synced files aren't flagged as agent adds), else the CURRENT container value for settings.json /
    MCP (don't add-flood the one-shot degraded path — those default to reject anyway)."""
    if art.kind in ("tree", "tree-symlink"):
        src = config.project_assets_claude_dir(slug) / art.container_rel
        return baseline.snapshot_tree(src, art.kind)
    return cur


def _diff_artifact(slug: str, art: artifacts.Artifact, cur: dict, base_c: dict,
                   host_c: dict, base_h: dict) -> list[Change]:
    kind = art.kind
    cur_u = _units(cur, kind)
    base_c_u = _units(base_c, kind)
    host_u = _units(host_c, kind)
    base_h_u = _units(base_h, kind)

    changes: list[Change] = []
    for unit in sorted(set(cur_u) | set(base_c_u)):
        before = base_c_u.get(unit)
        now = cur_u.get(unit)
        if before == now:
            continue  # the container did not change this unit — not agent-authored
        change_type = "added" if before is None else "deleted" if now is None else "modified"
        if now == host_u.get(unit):
            continue  # container's new value already equals the host's current → nothing to write
        if change_type in ("added", "modified") and kind in ("tree", "tree-symlink") \
                and _is_managed_write(slug, art, unit):
            continue  # byte-equal to the asset/pack source ⇒ claude-man wrote it, not the agent
        conflict = base_h_u.get(unit) != host_u.get(unit)  # host also moved since the baseline
        default = "reject" if (conflict or change_type == "deleted") else art.default_decision
        changes.append(Change(art.name, kind, art.scope, change_type, unit, conflict, default))
    return changes


def _units(entry: dict, kind: str) -> dict:
    """Normalise an artifact manifest entry to ``{unit_id: fingerprint}`` for the kind."""
    if kind in ("tree", "tree-symlink"):
        return entry.get("files", {})
    if kind == "json-keys":
        return entry.get("keys", {})
    if kind == "mcp":
        return entry.get("servers", {})
    if kind == "file":
        h = entry.get("hash", "")
        return {"": h} if h else {}
    return {}


def _is_managed_write(slug: str, art: artifacts.Artifact, rel: str) -> bool:
    """True if the container tree file at ``rel`` is byte-identical to the asset/pack SOURCE file —
    i.e. claude-man's own sync-in / materialize wrote it, not the agent. Symlinks are never
    subtracted (rare; the fingerprint already records the target)."""
    src = config.project_assets_claude_dir(slug) / art.container_rel / rel
    dst = config.claude_config_dir(slug) / art.container_rel / rel
    if src.is_symlink() or dst.is_symlink():
        return False
    if not (src.is_file() and dst.is_file()):
        return False
    try:
        return filecmp.cmp(src, dst, shallow=False)
    except OSError:
        return False
