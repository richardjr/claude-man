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
    "settings.local.json",   # machine-local permission grants; never synced
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
    "todos",            # current dir name (legacy was tasks/); per-session todo state
    "todos/*",
    "debug",            # diagnostic logs + a host-absolute `latest` symlink
    "debug/*",
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
    r"(token|key|secret|password|passwd|pwd|cred|credential|apikey|api_key|access_key|"
    r"private_key|client_secret|authorization|bearer)", re.IGNORECASE
)
_BEARER_RE = re.compile(r"\b(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)

# BUG-4: value-SHAPE secret scan. The key-name heuristic above misses a secret stored under a
# benign key (e.g. ``"x": "sk-ant-…"`` or a bare token on its own line). These patterns redact a
# value that LOOKS like a known credential, regardless of its key. Each is a SPECIFIC provider/shape
# so ordinary content (model ids like ``claude-opus-4-8``, paths like ``/home/agent/.claude/skills``)
# is never touched; the generic catch-all stays at a high length so it can't eat a path.
_SECRET_VALUE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk[-_][A-Za-z0-9._-]{12,}"),     # Anthropic / OpenAI / Stripe (sk-ant-…, sk-…, sk_live_…)
    re.compile(r"rk_[A-Za-z0-9]{12,}"),           # Stripe restricted keys
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),    # GitHub PAT / OAuth / app / refresh tokens
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),              # AWS access key id
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),         # Google API key
    re.compile(r"eyJ[A-Za-z0-9._-]{20,}"),        # JWT (the base64 of '{"' header → eyJ…)
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),          # generic hex token / md5-sha-shaped secret (or a sha — safe to over-redact)
    re.compile(r"[A-Za-z0-9+/_-]{40,}={0,2}"),    # long base64-ish / high-entropy token run
)


def is_denied_path(rel_path: str) -> bool:
    """True if ``rel_path`` (relative to the config dir) must never be read/synced.

    A denied NAME is matched at ANY depth, not just the first segment — so a denied basename smuggled
    inside an allowlisted tree (``skills/foo/.credentials.json``, ``agents/sessions/x``) is caught.
    The git-staging re-assert (``merge._audit_commit``) relies on this nested matching."""
    rel = rel_path.lstrip("/")
    segments = rel.split("/")
    for pattern in DENY_PATHS:
        if rel == pattern or fnmatch.fnmatch(rel, pattern):
            return True
        if any(seg == pattern or fnmatch.fnmatch(seg, pattern) for seg in segments):
            return True
    return False


def is_denied_json_key(key: str) -> bool:
    low = key.lower()
    if any(low == d.lower() for d in DENY_JSON_KEYS):  # case-insensitive: OauthAccount / userId evade exact
        return True
    return any(low.startswith(p) for p in DENY_JSON_KEY_PREFIXES)


def is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key))


def mask_value(value: str) -> str:
    """Redact a value that looks secret, preserving only its length."""
    return f"<redacted: {len(value)} bytes>"


def mask_line(line: str) -> str:
    """Mask Bearer tokens, ``key: secret`` pairs, AND token-shaped bare values in a diff line."""
    line = _BEARER_RE.sub(r"\1<redacted>", line)
    # crude key:value / key=value masking when the KEY looks secret
    m = re.match(r'^(?P<pre>[\s+\-]*["\']?(?P<key>[\w.-]+)["\']?\s*[:=]\s*)(?P<val>.+)$', line)
    if m and is_secret_key(m.group("key")):
        return m.group("pre") + mask_value(m.group("val").strip().strip('",'))
    # BUG-4: redact a token-shaped VALUE even under a non-secret (or absent) key.
    for rx in _SECRET_VALUE_RES:
        line = rx.sub("<redacted>", line)
    return line
