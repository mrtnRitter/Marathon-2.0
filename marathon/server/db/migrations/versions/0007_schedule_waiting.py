"""Replace scheduled activation with persistent run and waiting state."""

# IMPORTS
from alembic import op
import sqlalchemy as sa


# CONFIG
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


# FUNC
def upgrade() -> None:
    """Store scheduled-run state and scan timestamps."""
    op.add_column(
        "workflows",
        sa.Column("schedule_waiting", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("workflows", sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("watch_modules", sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE workflows SET active = true, schedule_waiting = true "
        "WHERE schedule_every IS NOT NULL AND NOT active AND NOT pausing"
    )
    op.execute(
        "UPDATE workflows SET schedule_waiting = false, run_started_at = now() "
        "WHERE active AND scheduled_active"
    )
    op.drop_column("workflows", "scheduled_active")


def downgrade() -> None:
    """Restore the legacy scheduled-activation flag."""
    op.add_column(
        "workflows",
        sa.Column("scheduled_active", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE workflows SET active = false WHERE active AND schedule_waiting")
    op.execute(
        "UPDATE workflows SET scheduled_active = true "
        "WHERE active AND schedule_every IS NOT NULL AND NOT schedule_waiting"
    )
    op.drop_column("watch_modules", "last_scan_at")
    op.drop_column("workflows", "run_started_at")
    op.drop_column("workflows", "schedule_waiting")
