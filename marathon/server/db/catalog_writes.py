"""Transactional write operations for module catalog descriptors."""

# IMPORTS
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection


# FUNC
def sync_module_types(connection: Connection, specs: list[dict[str, Any]]) -> int:
    """Insert or update catalog entries without deleting existing module types."""
    connection.execute(text("SET LOCAL lock_timeout = '10s'"))
    statement = text(
        "INSERT INTO module_types "
        "(id, name, category, icon, description, has_input, outputs, settings, sort_order) "
        "VALUES (:id, :name, :category, :icon, :description, :has_input, :outputs, :settings, :sort_order) "
        "ON CONFLICT (id) DO UPDATE SET "
        "name = EXCLUDED.name, category = EXCLUDED.category, icon = EXCLUDED.icon, "
        "description = EXCLUDED.description, has_input = EXCLUDED.has_input, "
        "outputs = EXCLUDED.outputs, settings = EXCLUDED.settings, sort_order = EXCLUDED.sort_order"
    ).bindparams(bindparam("outputs", type_=JSONB), bindparam("settings", type_=JSONB))
    for spec in specs:
        connection.execute(statement, spec)
    return len(specs)
