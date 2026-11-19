"""Insert default settings and the built-in server worker."""

# IMPORTS
import json

from sqlalchemy import text
from sqlalchemy.engine import Connection

from marathon.server.config import ServerConfig


# FUNC
def seed_defaults(connection: Connection, config: ServerConfig) -> None:
    """Insert database defaults without replacing existing values."""
    connection.execute(
        text(
            "INSERT INTO settings (key, value) VALUES (:key, CAST(:value AS jsonb)) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {
            "key": "folder_dialog.server_address",
            "value": json.dumps(config.folder_dialog.server_address),
        },
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
