"""Token-usage stats parsed from each project's transcript JSONL (read-only, host-side).

A local analytics read of OUR OWN state-dir transcripts — only the per-message token-count fields
(``input_tokens`` / ``output_tokens`` / ``cache_*``), never message content. This is entirely
separate from sync-back: nothing is written to the host ``~/.claude`` or the setups repo, so it does
not cross the denylist boundary. Usage is attributed to each project's current effective profile, so
the operator can watch per-account consumption (the metric that matters for subscription limits).

Scope note: this counts only usage produced *inside claude-man containers*, not the operator's own
host ``~/.claude`` usage — claude-man can only account for what it manages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import config
from .registry import profiles as profiles_registry
from .registry import projects as projects_registry


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    messages: int = 0
    sessions: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_creation + self.cache_read

    def add(self, other: "Usage") -> None:
        self.input += other.input
        self.output += other.output
        self.cache_creation += other.cache_creation
        self.cache_read += other.cache_read
        self.messages += other.messages
        self.sessions += other.sessions


def _scan_file(path) -> Usage:
    u = Usage()
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                usage = (rec.get("message") or {}).get("usage")
                if not isinstance(usage, dict):
                    continue
                u.input += int(usage.get("input_tokens") or 0)
                u.output += int(usage.get("output_tokens") or 0)
                u.cache_creation += int(usage.get("cache_creation_input_tokens") or 0)
                u.cache_read += int(usage.get("cache_read_input_tokens") or 0)
                u.messages += 1
    except OSError:
        return Usage()
    return u


def project_usage(slug: str) -> Usage:
    """Sum token usage across all of a project's container transcripts."""
    root = config.claude_config_dir(slug) / "projects"
    total = Usage()
    if not root.exists():
        return total
    files = list(root.rglob("*.jsonl"))
    for path in files:
        total.add(_scan_file(path))
    total.sessions += len(files)
    return total


def usage_by_profile() -> dict[str, Usage]:
    """Aggregate every project's usage under its effective profile name.

    Profiles with no usage are still included (zeroed) so they show up in listings.
    """
    default = profiles_registry.default_profile()
    out: dict[str, Usage] = {name: Usage() for name in profiles_registry.list_names()}
    for project in projects_registry.list_projects():
        pname = project.profile or (default.name if default else "(none)")
        out.setdefault(pname, Usage()).add(project_usage(project.slug))
    return out


def human(n: int) -> str:
    """Compact token count: 1234567 -> '1.2M', 12345 -> '12.3k'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)
