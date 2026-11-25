"""Read and write application settings."""

# IMPORTS
import re
from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from marathon.server.config import (
    ProxyConfig,
    SmtpConfig,
    smtp_encryptions,
    check_proxy,
    normalize_email,
    normalize_domains,
    normalize_public_url,
    normalize_trusted_proxies,
    trusted_proxies_error,
)


# CONFIG
_setting_key = "folder_dialog.server_address"
_proxy_setting_keys = ("proxy.public_url", "proxy.builtin", "proxy.trusted_proxies")
_auth_setting_key = "auth.allowed_domains"
_smtp_setting_key = "smtp"
_smtp_host_pattern = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
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
        raise ValueError("Einstellung proxy.builtin muss true oder false sein")
    trusted_proxies = normalize_trusted_proxies(
        values[_proxy_setting_keys[2]]
        if _proxy_setting_keys[2] in values
        else defaults.trusted_proxies
    )
    return ProxyConfig(public_url, builtin, trusted_proxies)


def get_settings(connection: Connection) -> dict:
    """Return folder-dialog, proxy, and authentication settings, using defaults when unset."""
    server_address = connection.execute(
        text("SELECT value FROM settings WHERE key = :key"), {"key": _setting_key}
    ).scalar_one_or_none()
    proxy = get_proxy_config(connection)
    allowed_domains = connection.execute(
        text("SELECT value FROM settings WHERE key = :key"), {"key": _auth_setting_key}
    ).scalar_one_or_none()
    return {
        "folder_dialog": {"server_address": server_address or ""},
        "proxy": {
            "public_url": proxy.public_url,
            "builtin": proxy.builtin,
            "trusted_proxies": list(proxy.trusted_proxies),
        },
        "auth": {"allowed_domains": allowed_domains or []},
    }


def save_settings(
    connection: Connection,
    server_address: str,
    *,
    public_url: str,
    builtin: bool,
    trusted_proxies: Sequence[str],
    allowed_domains: Sequence[str] = (),
) -> dict:
    """Validate and upsert folder-dialog, proxy, and authentication settings."""
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
    try:
        allowed_domains = normalize_domains(list(allowed_domains))
    except ValueError as error:
        raise SettingsValidationError(str(error)) from error

    values = {
        _setting_key: server_address,
        _proxy_setting_keys[0]: public_url,
        _proxy_setting_keys[1]: builtin,
        _proxy_setting_keys[2]: list(trusted_proxies),
        _auth_setting_key: list(allowed_domains),
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
        "auth": {"allowed_domains": list(allowed_domains)},
    }


def get_smtp_config(connection: Connection) -> SmtpConfig:
    """Return the persisted SMTP configuration, using defaults when unset."""
    value = connection.execute(
        text("SELECT value FROM settings WHERE key = :key"), {"key": _smtp_setting_key}
    ).scalar_one_or_none()
    defaults = SmtpConfig()
    if value is None:
        return defaults
    if not isinstance(value, dict):
        raise ValueError("Einstellung smtp ist ungültig")
    if "encryption" in value:
        encryption = value["encryption"]
        if not isinstance(encryption, str) or encryption not in smtp_encryptions:
            raise ValueError("Einstellung smtp ist ungültig")
    else:
        use_tls = value.get("use_tls", True)
        if type(use_tls) is not bool:
            raise ValueError("Einstellung smtp ist ungültig")
        encryption = "starttls" if use_tls else "none"
    config = SmtpConfig(
        host=value.get("host", defaults.host),
        port=value.get("port", defaults.port),
        username=value.get("username", defaults.username),
        encryption=encryption,
        from_address=value.get("from_address", defaults.from_address),
        from_name=value.get("from_name", defaults.from_name),
    )
    if (
        not isinstance(config.host, str)
        or type(config.port) is not int
        or not isinstance(config.username, str)
        or not isinstance(config.from_address, str)
        or not isinstance(config.from_name, str)
    ):
        raise ValueError("Einstellung smtp ist ungültig")
    return config


def save_smtp_config(
    connection: Connection,
    *,
    host: str,
    port: int,
    username: str,
    encryption: str,
    from_address: str,
    from_name: str,
) -> SmtpConfig:
    """Validate and persist SMTP settings without storing the password."""
    host = host.strip()
    username = username.strip()
    from_address = from_address.strip()
    from_name = from_name.strip()
    if encryption not in smtp_encryptions:
        raise SettingsValidationError("Mailversand: Verschlüsselung muss none, starttls oder tls sein")
    if host and not _smtp_host_pattern.fullmatch(host):
        raise SettingsValidationError(
            "Mailversand: Server nur als Rechnername oder IP-Adresse, z. B. smtp.office365.com"
        )
    if type(port) is not int or not 1 <= port <= 65535:
        raise SettingsValidationError("Mailversand: Port muss zwischen 1 und 65535 liegen")
    if host and not from_address:
        raise SettingsValidationError("Mailversand: Absenderadresse fehlt")
    if from_address:
        try:
            from_address = normalize_email(from_address)
        except ValueError as error:
            raise SettingsValidationError("Mailversand: Absenderadresse ungültig") from error
    if len(from_name) > 100:
        raise SettingsValidationError("Mailversand: Absendername ist zu lang (max. 100 Zeichen)")
    config = SmtpConfig(host, port, username, encryption, from_address, from_name)
    statement = text(
        "INSERT INTO settings (key, value) VALUES (:key, :value) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
    ).bindparams(bindparam("value", type_=JSONB))
    connection.execute(
        statement,
        {
            "key": _smtp_setting_key,
            "value": {
                "host": config.host,
                "port": config.port,
                "username": config.username,
                "encryption": config.encryption,
                "from_address": config.from_address,
                "from_name": config.from_name,
            },
        },
    )
    return config
