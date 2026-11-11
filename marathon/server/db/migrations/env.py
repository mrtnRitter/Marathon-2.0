"""Alembic environment using a caller-provided database connection."""

# IMPORTS
from alembic import context
from sqlalchemy.engine import Connection


# CONFIG
connection = context.config.attributes.get("connection")


# FUNC
def _run_migrations(active_connection: Connection | None) -> None:
    if active_connection is None:
        raise RuntimeError("Migrationen nur über marathon.server.db ausführen")
    context.configure(
        connection=active_connection,
        target_metadata=None,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


_run_migrations(connection)
