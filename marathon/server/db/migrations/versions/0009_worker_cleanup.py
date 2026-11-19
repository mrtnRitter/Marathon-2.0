"""Remove legacy worker settings and normalize worker states."""

# IMPORTS
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# CONFIG
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


# FUNC
def upgrade() -> None:
    """Normalize worker states and remove legacy worker settings."""
    op.execute(
        "UPDATE workers SET status = CASE status "
        "WHEN 'new' THEN 'stopped' WHEN 'locked' THEN 'stopped' "
        "WHEN 'busy' THEN 'ready' WHEN 'offline' THEN 'ready' "
        "WHEN 'updating' THEN 'ready' ELSE status END"
    )
    op.drop_constraint("ck_workers_status", "workers", type_="check")
    op.create_check_constraint(
        "ck_workers_status", "workers", "status IN ('ready', 'stopping', 'stopped')"
    )
    op.drop_constraint("ck_workers_max_parallel", "workers", type_="check")
    op.drop_constraint("ck_workers_client_slots", "workers", type_="check")
    op.drop_column("workers", "priorities")
    op.drop_column("workers", "max_parallel")
    op.drop_column("workers", "client_slots")


def downgrade() -> None:
    """Restore legacy worker settings and the 0008 worker states."""
    op.add_column(
        "workers",
        sa.Column("priorities", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("workers", sa.Column("max_parallel", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("workers", sa.Column("client_slots", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint("ck_workers_max_parallel", "workers", "max_parallel >= 1")
    op.create_check_constraint("ck_workers_client_slots", "workers", "client_slots >= 1")
    op.drop_constraint("ck_workers_status", "workers", type_="check")
    op.execute("UPDATE workers SET status = 'locked' WHERE status IN ('stopping', 'stopped')")
    op.create_check_constraint(
        "ck_workers_status",
        "workers",
        "status IN ('new', 'ready', 'busy', 'offline', 'locked', 'updating', 'stopping', 'stopped')",
    )
