"""Add PostgreSQL notifications for live table changes."""

# IMPORTS
from alembic import op


# CONFIG
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None
live_tables = (
    "module_types", "workflows", "workflow_versions", "workflow_modules",
    "workflow_connections", "workers", "assets", "jobs", "job_assets",
    "asset_history", "log_lines",
)


# FUNC
def upgrade() -> None:
    """Notify the live channel after changes to each live table."""
    op.execute(
        "CREATE FUNCTION marathon_notify_change() RETURNS trigger AS $$ "
        "BEGIN PERFORM pg_notify('marathon_changes', TG_TABLE_NAME); "
        "RETURN NULL; END; $$ LANGUAGE plpgsql"
    )
    for table in live_tables:
        op.execute(
            f"CREATE TRIGGER marathon_live_{table}_write "
            f"AFTER INSERT OR DELETE ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION marathon_notify_change()"
        )
        op.execute(
            f"CREATE TRIGGER marathon_live_{table}_update "
            f"AFTER UPDATE ON {table} FOR EACH ROW "
            "WHEN (OLD.* IS DISTINCT FROM NEW.*) "
            "EXECUTE FUNCTION marathon_notify_change()"
        )


def downgrade() -> None:
    """Remove live-change triggers and their notification function."""
    for table in live_tables:
        op.execute(f"DROP TRIGGER IF EXISTS marathon_live_{table}_write ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS marathon_live_{table}_update ON {table}")
    op.execute("DROP FUNCTION IF EXISTS marathon_notify_change()")
