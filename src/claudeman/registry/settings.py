"""Load / save the global claude-man settings (``~/.config/claude-man/config.toml``).

The "general features" config tier — read with stdlib ``tomllib``, written comment-preserving with
``tomlkit`` (mirrors ``profiles.py``). A missing file is not an error: it resolves to default
``Settings()``. Secret-free (ssh key PATHS only), so it round-trips through git like the project /
profile definitions.

Canonical shape::

    [ssh]
    keys = ["~/.ssh/id_ed25519"]
    auto_load = true
"""

from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

from .. import config
from .schema import Settings, ValidationError


def _parse(data: dict) -> Settings:
    ssh = data.get("ssh", {}) or {}
    keys = ssh.get("keys", [])
    if not isinstance(keys, list):
        raise ValidationError("config ssh.keys must be a list of host key paths")
    git = data.get("git", {}) or {}
    image = data.get("image", {}) or {}
    # Coerce an unknown/typo'd channel back to the default rather than raising — a bad value in a
    # hand-edited config must not brick the TUI/CLI (the schema stays strict for programmatic writes).
    channel = str(image.get("claude_channel", config.DEFAULT_CLAUDE_CHANNEL) or config.DEFAULT_CLAUDE_CHANNEL)
    if channel not in config.CLAUDE_CHANNELS:
        channel = config.DEFAULT_CLAUDE_CHANNEL
    return Settings(
        ssh_keys=tuple(str(k) for k in keys),
        ssh_auto_load=bool(ssh.get("auto_load", True)),
        git_user_name=str(git.get("user_name", "") or ""),
        git_user_email=str(git.get("user_email", "") or ""),
        image_update_check=bool(image.get("update_check", True)),
        claude_channel=channel,
        claude_version_pin=str(image.get("claude_version_pin", "") or ""),
    )


def load() -> Settings:
    """The global settings, or default ``Settings()`` when the file doesn't exist yet."""
    path = config.settings_toml_path()
    if not path.exists():
        return Settings()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return _parse(data)


def save(settings: Settings) -> Path:
    try:
        import tomlkit
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on env
        raise RuntimeError("writing config TOML requires the 'tomlkit' dependency") from exc

    doc = tomlkit.document()
    ssh = tomlkit.table()
    ssh["keys"] = list(settings.ssh_keys)
    ssh["auto_load"] = bool(settings.ssh_auto_load)
    doc["ssh"] = ssh
    git = tomlkit.table()
    git["user_name"] = settings.git_user_name
    git["user_email"] = settings.git_user_email
    doc["git"] = git
    image = tomlkit.table()
    image["update_check"] = bool(settings.image_update_check)
    image["claude_channel"] = settings.claude_channel
    image["claude_version_pin"] = settings.claude_version_pin
    doc["image"] = image

    path = config.settings_toml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc))
    return path


def add_ssh_key(path: str) -> tuple[Settings, bool]:
    """Append a key path to the settings (dedup, order-preserving). Returns (settings, added)."""
    norm = path.strip()
    if not norm:
        raise ValidationError("ssh key path must be a non-empty string")
    current = load()
    if norm in current.ssh_keys:
        return current, False
    updated = dataclasses.replace(current, ssh_keys=current.ssh_keys + (norm,))
    save(updated)
    return updated, True


def remove_ssh_key(path: str) -> tuple[Settings, bool]:
    """Drop a key path from the settings. Returns (settings, removed)."""
    norm = path.strip()  # normalise identically to add_ssh_key so a stored path always matches
    current = load()
    if norm not in current.ssh_keys:
        return current, False
    updated = dataclasses.replace(
        current, ssh_keys=tuple(k for k in current.ssh_keys if k != norm)
    )
    save(updated)
    return updated, True


def set_git_identity(name: str, email: str) -> Settings:
    """Set (or clear, with empty strings) the git author identity injected into containers."""
    updated = dataclasses.replace(
        load(), git_user_name=name.strip(), git_user_email=email.strip()
    )
    save(updated)
    return updated


def set_image_settings(
    *,
    update_check: bool | None = None,
    claude_channel: str | None = None,
    claude_version_pin: str | None = None,
) -> Settings:
    """Update the on-start claude-version settings (each ``None`` arg is left unchanged). Raises
    ``ValidationError`` (via ``Settings.__post_init__``) for an unknown channel, so a bad
    ``config image --channel`` fails loudly rather than silently writing junk."""
    cur = load()
    updated = dataclasses.replace(
        cur,
        image_update_check=cur.image_update_check if update_check is None else bool(update_check),
        claude_channel=cur.claude_channel if claude_channel is None else claude_channel,
        claude_version_pin=(cur.claude_version_pin if claude_version_pin is None
                            else claude_version_pin.strip()),
    )
    save(updated)
    return updated
