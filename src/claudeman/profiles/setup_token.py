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

from .. import agents, config
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

    Uses ``setup-token``'s default scope (``user:inference``) — enough to run inference inside the
    container, which is all claude-man needs.
    """
    print("→ minting a long-lived token (claude setup-token).", file=sys.stderr)
    print("  Complete the flow in the browser; the token is printed at the end — copy it.",
          file=sys.stderr)
    rc = _run_interactive(["claude", "setup-token"])
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
    """Write the token 0600 (dir 0700) under the profile state dir (no world-readable window)."""
    config.write_secret_file(config.profile_token_path(name), token + "\n")


def mint(
    name: str,
    *,
    sso: bool = False,
    login: bool = False,
    console: bool = False,
    email: str | None = None,
    default: bool = False,
    display_name: str = "",
    agent: str = agents.DEFAULT_ID,
    login_only: bool = False,
    api_key: str | None = None,
) -> Profile:
    """Create a new profile: (optionally) log in, mint + store a token, write the profile TOML.

    ``agent`` scopes the profile to a provider (Phase 7-auth). How the token is minted follows the
    provider's ``auth.token_kind``: ``oauth-token`` (claude — the interactive ``setup-token`` flow
    below) or ``api-key`` (a pasted key — ``api_key``, else a hidden prompt — stored ``0600`` exactly
    like the OAuth token and injected as the provider's ``token_env``). ``login_only`` writes the
    profile record WITHOUT a token: the account identity for projects that run in ``login`` auth
    mode (the credential is minted in-container, so the profile needs none)."""
    provider = agents.resolve(agent)
    if login_only:
        profile = Profile(name=name, agent=agent, display_name=display_name or name,
                          account_email=email or "", default=default)
        registry.save(profile, make_default=default)
        return profile
    if provider.auth.token_kind == "api-key":
        key = (api_key if api_key is not None else _prompt_api_key(provider)).strip()
        if not key or any(c.isspace() for c in key):
            raise RuntimeError(f"{provider.display_name} API key must be a single non-empty token")
        _store_token(name, key)
        profile = Profile(name=name, agent=agent, display_name=display_name or name,
                          account_email=email or "", default=default)
        registry.save(profile, make_default=default)
        return profile
    if provider is not agents.CLAUDE:
        raise RuntimeError(f"{provider.display_name}: no host mint flow for token_kind "
                           f"{provider.auth.token_kind!r} (use --login-only)")
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
        agent=agent,
        display_name=display_name or name,
        account_email=resolved_email,
        default=default,
    )
    registry.save(profile, make_default=default)
    return profile


def _prompt_api_key(provider) -> str:
    """Hidden prompt for an API-key-kind provider token (never echoed; never argv)."""
    import getpass
    if not sys.stdin.isatty():
        raise RuntimeError(f"{provider.display_name} API key: pass --stdin or run on a TTY")
    return getpass.getpass(f"{provider.display_name} API key ({provider.auth.token_env}): ")


def renew(name: str, *, api_key: str | None = None) -> Profile:
    """Re-mint the token for an existing profile, preserving its identity/default settings. An
    ``api-key``-kind profile re-prompts (or takes ``api_key``); an OAuth one re-runs setup-token."""
    profile = registry.load(name)  # raises FileNotFoundError if the profile doesn't exist
    provider = agents.resolve(profile.agent)
    if provider.auth.token_kind == "api-key":
        key = (api_key if api_key is not None else _prompt_api_key(provider)).strip()
        if not key or any(c.isspace() for c in key):
            raise RuntimeError(f"{provider.display_name} API key must be a single non-empty token")
        _store_token(name, key)
        return profile
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
    for key in (*config.SCRUBBED_ENV_KEYS, *agents.credential_env_names()):
        env.pop(key, None)
    provider = agents.CLAUDE   # this IS the claude `auth status` probe
    env[provider.auth.token_env] = token
    with tempfile.TemporaryDirectory(prefix="claude-man-verify-") as tmp:
        env[provider.config_dir_env] = tmp
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
