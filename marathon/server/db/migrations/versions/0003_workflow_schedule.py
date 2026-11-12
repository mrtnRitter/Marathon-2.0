"""Remove workflow priority and add schedule metadata."""

# IMPORTS
from alembic import op
import sqlalchemy as sa


# CONFIG
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


# FUNC
def upgrade() -> None:
    """Replace workflow priority with validated scheduling fields."""
    op.drop_column("workflows", "priority")
    op.add_column("workflows", sa.Column("schedule_every", sa.Integer(), nullable=True))
    op.add_column("workflows", sa.Column("schedule_unit", sa.Text(), nullable=True))
    op.add_column("workflows", sa.Column("schedule_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workflows", sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "workflows",
        sa.Column("scheduled_active", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_check_constraint(
        "ck_workflows_schedule_unit",
        "workflows",
        "schedule_unit IS NULL OR schedule_unit IN ('minutes', 'hours', 'days', 'weeks', 'months')",
    )
    op.create_check_constraint(
        "ck_workflows_schedule_every",
        "workflows",
        "schedule_every IS NULL OR schedule_every >= 1",
    )
    op.create_check_constraint(
        "ck_workflows_schedule_complete",
        "workflows",
        "(schedule_every IS NULL AND schedule_unit IS NULL AND schedule_start IS NULL AND next_run_at IS NULL) "
        "OR (schedule_every IS NOT NULL AND schedule_unit IS NOT NULL "
        "AND schedule_start IS NOT NULL AND next_run_at IS NOT NULL)",
    )
    op.create_index("ix_workflows_next_run_at", "workflows", ["next_run_at"])


def downgrade() -> None:
    """Remove schedule metadata and restore workflow priority."""
    op.drop_index("ix_workflows_next_run_at", table_name="workflows")
    op.drop_constraint("ck_workflows_schedule_complete", "workflows", type_="check")
    op.drop_constraint("ck_workflows_schedule_every", "workflows", type_="check")
    op.drop_constraint("ck_workflows_schedule_unit", "workflows", type_="check")
    op.drop_column("workflows", "scheduled_active")
    op.drop_column("workflows", "next_run_at")
    op.drop_column("workflows", "schedule_start")
    op.drop_column("workflows", "schedule_unit")
    op.drop_column("workflows", "schedule_every")
    op.add_column(
        "workflows",
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
    )
