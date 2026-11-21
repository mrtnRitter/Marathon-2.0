"""Insert default settings and the built-in server worker."""

# IMPORTS
import json

from sqlalchemy import text
from sqlalchemy.engine import Connection

from marathon.server.config import ServerConfig


# FUNC
def seed_defaults(connection: Connection, config: ServerConfig) -> None:
    """Insert database defaults without replacing existing values."""
    defaults = {
        "folder_dialog.server_address": config.folder_dialog.server_address,
        "proxy.public_url": config.proxy.public_url,
        "proxy.builtin": config.proxy.builtin,
        "proxy.trusted_proxies": list(config.proxy.trusted_proxies),
    }
    for key, value in defaults.items():
        connection.execute(
            text(
                "INSERT INTO settings (key, value) VALUES (:key, CAST(:value AS jsonb)) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {"key": key, "value": json.dumps(value)},
        )
    connection.execute(
        text(
            "INSERT INTO workers (id, name, status, online, skills, capabilities, client_version) "
            "VALUES (:id, :name, 'ready', false, CAST(:skills AS jsonb), '[]'::jsonb, '') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": "server-worker",
            "name": "Server-Worker",
            "skills": json.dumps(list(config.server_worker.skills)),
        },
    )
