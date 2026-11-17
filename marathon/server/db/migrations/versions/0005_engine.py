"""Add release-engine schema fields."""

# IMPORTS
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# CONFIG
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


# FUNC
def upgrade() -> None:
    """Add engine state, module execution modes, and extended job statuses."""
    op.add_column(
        "module_types",
        sa.Column("execution", sa.Text(), nullable=False, server_default="none"),
    )
    op.create_check_constraint(
        "ck_module_types_execution",
        "module_types",
        "execution IN ('worker', 'engine', 'none')",
    )

    op.add_column("workflows", sa.Column("pausing", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("workflows", sa.Column("last_assigned_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("assets", sa.Column("from_module_id", sa.Text(), nullable=True))
    op.add_column("assets", sa.Column("from_output_id", sa.Text(), nullable=True))
    op.add_column("assets", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("assets", sa.Column("wait_until", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_assets_attempts", "assets", "attempts >= 0")

    op.add_column("jobs", sa.Column("type_id", sa.Text(), nullable=False, server_default=""))
    op.add_column("jobs", sa.Column("version", sa.Integer(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("jobs", sa.Column("pickup_deadline", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("error", sa.Text(), nullable=True))
    op.drop_constraint("ck_jobs_status", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_status",
        "jobs",
        "status IN ('open', 'assigned', 'running', 'cancelling', 'succeeded', 'failed', 'cancelled', 'interrupted')",
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])

    op.add_column("job_assets", sa.Column("output_id", sa.Text(), nullable=True))

    op.add_column(
        "workers",
        sa.Column("priorities", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("workers", sa.Column("client_slots", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint("ck_workers_client_slots", "workers", "client_slots >= 1")


def downgrade() -> None:
    """Remove release-engine fields and restore the prior job status contract."""
    op.execute(
        "UPDATE jobs SET status = 'cancelled' "
        "WHERE status IN ('running', 'cancelling', 'interrupted')"
    )
    op.drop_constraint("ck_jobs_status", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_status",
        "jobs",
        "status IN ('open', 'assigned', 'succeeded', 'failed', 'cancelled')",
    )
    op.drop_index("ix_jobs_status", table_name="jobs")

    op.drop_constraint("ck_workers_client_slots", "workers", type_="check")
    op.drop_column("workers", "client_slots")
    op.drop_column("workers", "priorities")

    op.drop_column("job_assets", "output_id")

    op.drop_column("jobs", "error")
    op.drop_column("jobs", "pickup_deadline")
    op.drop_column("jobs", "settings")
    op.drop_column("jobs", "version")
    op.drop_column("jobs", "type_id")

    op.drop_constraint("ck_assets_attempts", "assets", type_="check")
    op.drop_column("assets", "wait_until")
    op.drop_column("assets", "attempts")
    op.drop_column("assets", "from_output_id")
    op.drop_column("assets", "from_module_id")

    op.drop_column("workflows", "last_assigned_at")
    op.drop_column("workflows", "pausing")

    op.drop_constraint("ck_module_types_execution", "module_types", type_="check")
    op.drop_column("module_types", "execution")
