"""The ``codex`` provider — the OpenAI Codex CLI (Phase 7c; docs/AGENTS.md § Codex — every value
below was verified by the 2026-09-26 login-mode spike under the exact hardened floor).

Install: NOT baked into the base image — the ``codex`` entry of the approved-tool registry
(``library/tools/codex/tool.toml``, the full ``codex-package`` tarball as a bundle under
``/opt/codex``) is pulled into a codex project's image automatically via ``ImageSpec.tools``, so
the image is ``<overlay>-t-<hash>`` and the version comes from the layer's ``codex-version`` label.
Auth: ``token`` = an OpenAI API key (``OPENAI_API_KEY``, API-billed; ``profile add --agent codex``
prompts hidden / ``--stdin``); ``login`` = ``codex login --device-auth`` in-container mints
``$CODEX_HOME/auth.json`` (ChatGPT plan, self-refreshing, survives recreate). Its bundled bubblewrap
sandbox can't namespace under cap-drop ALL, so the seeded ``config.toml`` turns it off — our
container IS the sandbox (invariant 2 unchanged). No sync-back policy yet (7d) → ``syncback=False``.
"""

from __future__ import annotations

from . import run
from .base import AgentProvider, AuthSpec, ImageSpec, RunSpec

REQUIRED_HOSTS = (
    ".openai.com",     # auth.openai.com (device auth + refresh), api.openai.com (/v1/responses)
    ".chatgpt.com",    # the ChatGPT account surface the login references
)

CONFIG_SEED = (
    ("config.toml",
     '# seeded by claude-man (docs/AGENTS.md § Codex) — edit freely; never overwritten\n'
     '# our hardened container IS the sandbox: bubblewrap cannot create namespaces under cap-drop ALL\n'
     'sandbox_mode = "danger-full-access"\n'
     '# no keyring in-container: the login-minted credential lives in this bind (auth.json, 0600)\n'
     'cli_auth_credentials_store = "file"\n'),
)

PROVIDER = AgentProvider(
    id="codex",
    display_name="OpenAI Codex",
    binary="codex",
    proc_comm="codex",
    config_dir="/home/agent/.codex",
    config_dir_env="CODEX_HOME",
    auth=AuthSpec(
        token_env="OPENAI_API_KEY",
        scrub_env=("CODEX_API_KEY",),        # honoured by `codex exec` — same billing hazard
        credential_file="auth.json",
        identity_file="",                    # no identity stub: auth.json carries the account
        token_kind="api-key",
        login_hint=("run `codex login --device-auth` inside the container (`claudemanctl project "
                    "shell {slug}`), open the URL it prints and enter the one-time code — device-code "
                    "auth must be enabled in the ChatGPT account's security settings"),
        token_hint="paste an OpenAI API key via `claudemanctl profile add <name> --agent codex`",
    ),
    image=ImageSpec(
        version_build_arg="CODEX_VERSION",   # informational: the version is pinned by the tool entry
        version_label="codex-version",       # stamped on the tools layer by the registry entry
        default_version="0.157.1",
        tools=("codex",),
    ),
    updates=None,                            # pinned via the registry entry; no release-pointer check
    required_hosts=REQUIRED_HOSTS,
    run=RunSpec(argv=run.codex_argv, parse=run.codex_parse),
    config_seed=CONFIG_SEED,
    syncback=False,
)
