"""Read and write application settings."""

# IMPORTS
import re
from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from marathon.server.config import (
    ProxyConfig,
    check_proxy,
    normalize_public_url,
    normalize_trusted_proxies,
    trusted_proxies_error,
)


# CONFIG
_setting_key = "folder_dialog.server_address"
_proxy_setting_keys = ("proxy.public_url", "proxy.builtin", "proxy.trusted_proxies")
_server_address_pattern = re.compile(r"^[A-Za-z0-9._-]{1,253}$")


class SettingsValidationError(ValueError):
    """Raised when a setting does not meet its validation contract."""


def get_proxy_config(connection: Connection) -> ProxyConfig:
    """Return the persisted proxy configuration, using defaults for missing keys."""
    rows = connection.execute(
        text("SELECT key, value FROM settings WHERE key IN (:public_url, :builtin, :trusted_proxies)"),
        {
            "public_url": _proxy_setting_keys[0],
            "builtin": _proxy_setting_keys[1],
            "trusted_proxies": _proxy_setting_keys[2],
        },
    ).mappings().all()
    values = {row["key"]: row["value"] for row in rows}
    defaults = ProxyConfig()
    public_url = normalize_public_url(
        values[_proxy_setting_keys[0]] if _proxy_setting_keys[0] in values else defaults.public_url
    )
    builtin = values[_proxy_setting_keys[1]] if _proxy_setting_keys[1] in values else defaults.builtin
    if type(builtin) is not bool:
        raise ValueError(trusted_proxies_error)
    trusted_proxies = normalize_trusted_proxies(
        values[_proxy_setting_keys[2]]
        if _proxy_setting_keys[2] in values
        else defaults.trusted_proxies
    )
    return ProxyConfig(public_url, builtin, trusted_proxies)


def get_settings(connection: Connection) -> dict:
    """Return the folder-dialog and proxy settings, using defaults when unset."""
    server_address = connection.execute(
        text("SELECT value FROM settings WHERE key = :key"), {"key": _setting_key}
    ).scalar_one_or_none()
    proxy = get_proxy_config(connection)
    return {
        "folder_dialog": {"server_address": server_address or ""},
        "proxy": {
            "public_url": proxy.public_url,
            "builtin": proxy.builtin,
            "trusted_proxies": list(proxy.trusted_proxies),
        },
    }


def save_settings(
    connection: Connection,
    server_address: str,
    *,
    public_url: str,
    builtin: bool,
    trusted_proxies: Sequence[str],
) -> dict:
    """Validate and upsert folder-dialog and proxy settings."""
    server_address = server_address.strip()
    if server_address and not _server_address_pattern.fullmatch(server_address):
        raise SettingsValidationError(
            "Server-Adresse: nur Rechnername oder IP-Adresse, z. B. 10.0.77.11"
        )

    try:
        public_url = normalize_public_url(public_url)
    except ValueError as error:
        raise SettingsValidationError(str(error)) from error
    try:
        trusted_proxies = normalize_trusted_proxies(list(trusted_proxies))
    except ValueError as error:
        raise SettingsValidationError(str(error)) from error
    try:
        check_proxy(builtin, trusted_proxies)
    except ValueError as error:
        raise SettingsValidationError(str(error)) from error

    values = {
        _setting_key: server_address,
        _proxy_setting_keys[0]: public_url,
        _proxy_setting_keys[1]: builtin,
        _proxy_setting_keys[2]: list(trusted_proxies),
    }
    statement = text(
        "INSERT INTO settings (key, value) VALUES (:key, :value) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
    ).bindparams(bindparam("value", type_=JSONB))
    for key, value in values.items():
        connection.execute(statement, {"key": key, "value": value})

    return {
        "folder_dialog": {"server_address": server_address},
        "proxy": {
            "public_url": public_url,
            "builtin": builtin,
            "trusted_proxies": list(trusted_proxies),
        },
    }
