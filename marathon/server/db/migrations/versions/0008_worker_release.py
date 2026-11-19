"""Add worker release settings and per-asset outcomes."""

# IMPORTS
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# CONFIG
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


# FUNC
def upgrade() -> None:
    """Add worker skills, release states, settings, and asset outcomes."""
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.add_column(
        "workers",
        sa.Column("skills", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("workers", sa.Column("online", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(
        "UPDATE workers AS worker SET skills = COALESCE(("
        "SELECT jsonb_agg(capability.skill ORDER BY "
        "CASE COALESCE(worker.priorities ->> capability.skill, 'normal') "
        "WHEN 'preferred' THEN 0 WHEN 'last' THEN 2 ELSE 1 END, capability.ordinality) "
        "FROM jsonb_array_elements_text(worker.capabilities) WITH ORDINALITY "
        "AS capability(skill, ordinality) "
        "WHERE COALESCE(worker.priorities ->> capability.skill, 'normal') <> 'off'"
        "), '[]'::jsonb), online = status <> 'offline'"
    )
    op.drop_constraint("ck_workers_status", "workers", type_="check")
    op.create_check_constraint(
        "ck_workers_status",
        "workers",
        "status IN ('new', 'ready', 'busy', 'offline', 'locked', 'updating', 'stopping', 'stopped')",
    )

    op.add_column("job_assets", sa.Column("outcome", sa.Text(), nullable=True))
    op.add_column("job_assets", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_assets", sa.Column("error", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_job_assets_outcome",
        "job_assets",
        "outcome IS NULL OR outcome IN ('result', 'error', 'cancelled', 'interrupted')",
    )
    op.execute(
        "UPDATE job_assets AS job_asset SET "
        "outcome = CASE job.status "
        "WHEN 'succeeded' THEN 'result' WHEN 'failed' THEN 'error' "
        "WHEN 'cancelled' THEN 'cancelled' WHEN 'interrupted' THEN 'interrupted' END, "
        "finished_at = job.finished_at "
        "FROM jobs AS job WHERE job_asset.job_id = job.id "
        "AND job.status IN ('succeeded', 'failed', 'cancelled', 'interrupted')"
    )

    op.add_column("log_lines", sa.Column("asset_id", sa.Text(), nullable=True))
    op.create_index("ix_log_lines_asset_id", "log_lines", ["asset_id"])


def downgrade() -> None:
    """Remove release fields and restore the previous worker states."""
    op.execute("UPDATE workers SET status = 'locked' WHERE status IN ('stopping', 'stopped')")
    op.drop_constraint("ck_workers_status", "workers", type_="check")
    op.create_check_constraint(
        "ck_workers_status",
        "workers",
        "status IN ('new', 'ready', 'busy', 'offline', 'locked', 'updating')",
    )

    op.drop_index("ix_log_lines_asset_id", table_name="log_lines")
    op.drop_column("log_lines", "asset_id")

    op.drop_constraint("ck_job_assets_outcome", "job_assets", type_="check")
    op.drop_column("job_assets", "error")
    op.drop_column("job_assets", "finished_at")
    op.drop_column("job_assets", "outcome")

    op.drop_column("workers", "online")
    op.drop_column("workers", "skills")
    op.drop_table("settings")
