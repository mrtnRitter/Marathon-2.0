"""Read and write application settings."""

import re

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection


_setting_key = "folder_dialog.server_address"
_server_address_pattern = re.compile(r"^[A-Za-z0-9._-]{1,253}$")


class SettingsValidationError(ValueError):
    """Raised when a setting does not meet its validation contract."""


def get_settings(connection: Connection) -> dict:
    """Return the folder-dialog settings, using an empty address when unset."""
    server_address = connection.execute(
        text("SELECT value FROM settings WHERE key = :key"), {"key": _setting_key}
    ).scalar_one_or_none()
    return {"folder_dialog": {"server_address": server_address or ""}}


def save_settings(connection: Connection, server_address: str) -> dict:
    """Validate and upsert the folder-dialog server address."""
    server_address = server_address.strip()
    if server_address and not _server_address_pattern.fullmatch(server_address):
        raise SettingsValidationError(
            "Server-Adresse: nur Rechnername oder IP-Adresse, z. B. 10.0.77.11"
        )

    connection.execute(
        text(
            "INSERT INTO settings (key, value) VALUES (:key, :value) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
        ).bindparams(bindparam("value", type_=JSONB)),
        {"key": _setting_key, "value": server_address},
    )
    return {"folder_dialog": {"server_address": server_address}}
