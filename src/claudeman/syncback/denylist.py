"""The sync-back denylist — the security boundary for what may leave a container.

Enforced BEFORE any read (so a secret is never even loaded into memory) and AGAIN at
git-staging time (so nothing slips into the audit repo). See CLAUDE.md invariant 5.

Nothing here depends on docker, textual, or the filesystem — it is pure policy, unit-tested.
"""

from __future__ import annotations

import fnmatch
import re

# ---------------------------------------------------------------------------
# Path policy (relative to a CLAUDE_CONFIG_DIR). Names and glob patterns.
# ---------------------------------------------------------------------------
# Never read or sync these — credentials, identity-bearing state, transcripts,
# machine-local caches. Matched against the path RELATIVE to the config dir.
DENY_PATHS: tuple[str, ...] = (
    ".credentials.json",
    ".claude.json",          # identity + per-project state; never wholesale
    ".config.json",
    "history.jsonl",
    "sessions",
    "sessions/*",
    "projects/*/*.jsonl",    # session transcripts
    "shell-snapshots",
    "shell-snapshots/*",
    "statsig",
    "statsig/*",
    "cache",
    "cache/*",
    "file-history",
    "file-history/*",
    "tasks",
    "tasks/*",
    "plans",
    "plans/*",
    "session-env",
    "session-env/*",
    "paste-cache",
    "paste-cache/*",
    "downloads",
    "downloads/*",
    "ide",
    "ide/*",
    "backups",
    "backups/*",
    "*-cache.json",
    ".last-cleanup",
    ".last-update-result.json",
    "mcp-needs-auth-cache.json",
)

# The artifacts that ARE eligible for sync-back (the allowlist), relative to the
# config dir. Each maps to a "kind" the diff/merge layer understands.
SYNC_ARTIFACTS: dict[str, str] = {
    "agents": "tree",            # ~/.claude/agents/*.md
    "skills": "tree-symlink",    # may contain symlinks; preserve them
    "commands": "tree",          # NEW target vs the legacy sync-claude.sh
    "settings.json": "json-keys",
    # MCP servers are applied via `claude mcp add/remove`, not file copy:
    "__mcp__": "mcp",
    # memory + CLAUDE.md are handled per-project (see artifacts.py).
}

# ---------------------------------------------------------------------------
# JSON key policy (for settings.json / any JSON we ever touch).
# ---------------------------------------------------------------------------
# Never copy these keys even from an otherwise-allowed JSON file.
DENY_JSON_KEYS: tuple[str, ...] = (
    "oauthAccount",
    "userID",
    "accountUuid",
    "organizationUuid",
)
# Key prefixes that are machine-local / telemetry and must never be synced.
DENY_JSON_KEY_PREFIXES: tuple[str, ...] = ("last", "cached", "telemetry")

# settings.json keys that are host-structural and must NEVER be clobbered by a merge.
STRUCTURAL_IMMUNE_KEYS: tuple[str, ...] = ("hooks", "statusLine")

# ---------------------------------------------------------------------------
# Secret masking for diffs.
# ---------------------------------------------------------------------------
_SECRET_KEY_RE = re.compile(
    r"(token|key|secret|password|authorization|bearer)", re.IGNORECASE
)
_BEARER_RE = re.compile(r"\b(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)


def is_denied_path(rel_path: str) -> bool:
    """True if ``rel_path`` (relative to the config dir) must never be read/synced."""
    rel = rel_path.lstrip("/")
    for pattern in DENY_PATHS:
        if rel == pattern or fnmatch.fnmatch(rel, pattern):
            return True
        # also block anything *under* a denied directory name
        first = rel.split("/", 1)[0]
        if first == pattern:
            return True
    return False


def is_denied_json_key(key: str) -> bool:
    if key in DENY_JSON_KEYS:
        return True
    low = key.lower()
    return any(low.startswith(p) for p in DENY_JSON_KEY_PREFIXES)


def is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key))


def mask_value(value: str) -> str:
    """Redact a value that looks secret, preserving only its length."""
    return f"<redacted: {len(value)} bytes>"


def mask_line(line: str) -> str:
    """Mask Bearer tokens and obvious ``key: secret`` pairs in a diff line."""
    line = _BEARER_RE.sub(r"\1<redacted>", line)
    # crude key:value / key=value masking when the key looks secret
    m = re.match(r'^(?P<pre>[\s+\-]*["\']?(?P<key>[\w.-]+)["\']?\s*[:=]\s*)(?P<val>.+)$', line)
    if m and is_secret_key(m.group("key")):
        return m.group("pre") + mask_value(m.group("val").strip().strip('",'))
    return line
