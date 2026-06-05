"""Mint / renew a profile's long-lived OAuth token via host ``claude setup-token`` (Phase 2).

Flow (all on the HOST — the token is born here and only ever leaves as ``CLAUDE_CODE_OAUTH_TOKEN``
injected at container launch; it is never written into an image layer or committed):

  1. (optional) ``claude auth login [--sso|--console] [--email]`` to point the host session at the
     account this profile should represent (work SSO seat vs personal subscription).
  2. ``claude setup-token`` — the interactive subscription flow; it prints a ~1-year token.
  3. The operator pastes the token back; we store it ``0600`` and resolve the account email from
     ``claude auth status --json`` for the display name + the switch-time mismatch guard.

The token CANNOT self-refresh, so its file mtime is the mint time (``profiles.token_age_days``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

from .. import config
from ..registry import profiles as registry
from ..registry.schema import Profile

#: setup-token OAuth tokens carry this prefix; used only for a soft sanity warning.
_TOKEN_PREFIX = "sk-ant-oat"


def _run_interactive(argv: list[str], *, env: dict | None = None) -> int:
    """Run a claude subcommand with all stdio inherited so its browser/code flow works."""
    return subprocess.run(argv, env=env).returncode


def _extract_email(data: object) -> str:
    """Pull an account email out of an ``auth status --json`` payload (schema not pinned)."""
    for path in (
        ("account", "email"),
        ("oauthAccount", "emailAddress"),
        ("user", "email"),
        ("email",),
        ("emailAddress",),
    ):
        cur: object = data
        for key in path:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                cur = None
                break
        if isinstance(cur, str) and "@" in cur:
            return cur
    return ""


def _account_email() -> str:
    """Best-effort: read the currently-authenticated account email from ``auth status --json``."""
    cp = subprocess.run(
        ["claude", "auth", "status", "--json"], capture_output=True, text=True, check=False
    )
    if cp.returncode != 0 or not cp.stdout.strip():
        return ""
    try:
        return _extract_email(json.loads(cp.stdout))
    except json.JSONDecodeError:
        return ""


def _mint_token() -> str:
    """Run ``claude setup-token`` interactively and return the pasted token.

    Mints with ``CLAUDE_CODE_OAUTH_SCOPES="user:profile user:inference"`` so the token can BOTH run
    inference (in the container) AND read the account's subscription usage (``/api/oauth/usage`` — the
    5h/weekly bars). The default ``user:inference``-only token 403s on the usage endpoint.
    """
    print("→ minting a long-lived token (claude setup-token).", file=sys.stderr)
    print("  Complete the flow in the browser; the token is printed at the end — copy it.",
          file=sys.stderr)
    env = dict(os.environ)
    env["CLAUDE_CODE_OAUTH_SCOPES"] = config.OAUTH_USAGE_SCOPES
    rc = _run_interactive(["claude", "setup-token"], env=env)
    if rc != 0:
        raise RuntimeError(f"`claude setup-token` exited {rc} (cancelled or failed)")
    token = input("Paste the long-lived token: ").strip()
    if not token:
        raise RuntimeError("no token provided")
    if not token.startswith(_TOKEN_PREFIX):
        print(f"warning: token does not start with {_TOKEN_PREFIX!r}; storing anyway",
              file=sys.stderr)
    return token


def _store_token(name: str, token: str) -> None:
    """Write the token 0600 (dir 0700) under the profile state dir."""
    path = config.profile_token_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_text(token + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def mint(
    name: str,
    *,
    sso: bool = False,
    login: bool = False,
    console: bool = False,
    email: str | None = None,
    default: bool = False,
    display_name: str = "",
) -> Profile:
    """Create a new profile: (optionally) log in, mint + store a token, write the profile TOML."""
    if login or sso or console:
        argv = ["claude", "auth", "login"]
        if sso:
            argv.append("--sso")
        if console:
            argv.append("--console")
        if email:
            argv += ["--email", email]
        print(f"→ signing in for profile {name!r} (claude auth login) ...", file=sys.stderr)
        if _run_interactive(argv) != 0:
            raise RuntimeError("`claude auth login` failed or was cancelled")

    token = _mint_token()
    _store_token(name, token)

    resolved_email = email or _account_email()
    profile = Profile(
        name=name,
        display_name=display_name or name,
        account_email=resolved_email,
        default=default,
    )
    registry.save(profile, make_default=default)
    return profile


def renew(name: str) -> Profile:
    """Re-mint the token for an existing profile, preserving its identity/default settings."""
    profile = registry.load(name)  # raises FileNotFoundError if the profile doesn't exist
    token = _mint_token()
    _store_token(name, token)
    return profile


def account_info(token: str) -> dict:
    """Resolve the account a token authenticates as, the same way a container would.

    Runs a one-shot ``claude auth status --json`` on the host with the token injected and a FRESH,
    empty ``CLAUDE_CONFIG_DIR`` — so there are no stored host credentials to fall back to and the
    answer reflects the token itself (mirrors the container: env-token auth, no ``.credentials.json``).
    ``ANTHROPIC_*`` is scrubbed so it can't outrank the token.
    """
    env = dict(os.environ)
    for key in config.SCRUBBED_ENV_KEYS:
        env.pop(key, None)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    with tempfile.TemporaryDirectory(prefix="claude-man-verify-") as tmp:
        env["CLAUDE_CONFIG_DIR"] = tmp
        cp = subprocess.run(
            ["claude", "auth", "status", "--json"],
            env=env, capture_output=True, text=True, check=False,
        )
    if cp.returncode != 0:
        raise RuntimeError(
            f"`claude auth status` exited {cp.returncode}: {cp.stderr.strip() or cp.stdout.strip()}"
        )
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"could not parse auth status output: {exc}") from exc


def verify(name: str) -> dict:
    """Return the live account info a profile's stored token authenticates as."""
    token = registry.load_token(name)
    if not token:
        raise RuntimeError(
            f"profile {name!r} has no token — run `claudemanctl profile add {name}` "
            f"or `claudemanctl profile renew {name}`"
        )
    return account_info(token)
