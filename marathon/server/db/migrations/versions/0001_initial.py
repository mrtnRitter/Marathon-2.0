"""Create the initial Marathon database schema."""

# IMPORTS
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# CONFIG
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


# FUNC
def upgrade() -> None:
    """Create all initial Marathon tables and indexes."""
    op.create_table(
        "module_types",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("icon", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("has_input", sa.Boolean(), nullable=False),
        sa.Column("outputs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "workflows",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "workflow_versions",
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workflow_id", "version"),
    )
    op.create_table(
        "workflow_modules",
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("type_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("position_x", sa.Float(), nullable=False),
        sa.Column("position_y", sa.Float(), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["type_id"], ["module_types.id"]),
        sa.ForeignKeyConstraint(
            ["workflow_id", "version"],
            ["workflow_versions.workflow_id", "workflow_versions.version"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workflow_id", "version", "id"),
    )
    op.create_table(
        "workflow_connections",
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_module_id", sa.Text(), nullable=False),
        sa.Column("source_output_id", sa.Text(), nullable=False),
        sa.Column("target_module_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_id", "version", "source_module_id"],
            ["workflow_modules.workflow_id", "workflow_modules.version", "workflow_modules.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "version", "target_module_id"],
            ["workflow_modules.workflow_id", "workflow_modules.version", "workflow_modules.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workflow_id", "version", "source_module_id", "source_output_id"),
    )
    op.create_table(
        "workers",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("ip_address", sa.Text(), nullable=True),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("max_parallel", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("client_version", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('new', 'ready', 'busy', 'offline', 'locked', 'updating')", name="ck_workers_status"),
        sa.CheckConstraint("max_parallel >= 1", name="ck_workers_max_parallel"),
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_module_id", sa.Text(), nullable=True),
        sa.Column("step_status", sa.Text(), nullable=True),
        sa.Column("list_position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('pending', 'in_progress', 'done', 'failed', 'on_hold')", name="ck_assets_status"),
        sa.CheckConstraint("step_status IS NULL OR step_status IN ('waiting', 'running')", name="ck_assets_step_status"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_assets_workflow_id_status", "assets", ["workflow_id", "status"])
    op.create_index("ix_assets_workflow_id_current_module_id", "assets", ["workflow_id", "current_module_id"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("module_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'assigned', 'succeeded', 'failed', 'cancelled')", name="ck_jobs_status"),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_jobs_progress"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_jobs_workflow_id_module_id", "jobs", ["workflow_id", "module_id"])
    op.create_index("ix_jobs_worker_id_status", "jobs", ["worker_id", "status"])
    op.create_table(
        "job_assets",
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("asset_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "asset_id"),
    )
    op.create_index("ix_job_assets_asset_id", "job_assets", ["asset_id"])
    op.create_table(
        "asset_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("asset_id", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.CheckConstraint("kind IN ('success', 'error', 'info')", name="ck_asset_history_kind"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_asset_history_asset_id_ts", "asset_history", ["asset_id", "ts"])
    op.create_table(
        "log_lines",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("module_id", sa.Text(), nullable=True),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.CheckConstraint("level IN ('debug', 'info', 'warn', 'error')", name="ck_log_lines_level"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_log_lines_job_id", "log_lines", ["job_id"])
    op.create_index("ix_log_lines_workflow_id_module_id_ts", "log_lines", ["workflow_id", "module_id", "ts"])


def downgrade() -> None:
    """Drop the initial Marathon schema in reverse dependency order."""
    for table in (
        "log_lines", "asset_history", "job_assets", "jobs", "assets", "workers",
        "workflow_connections", "workflow_modules", "workflow_versions", "workflows", "module_types",
    ):
        op.drop_table(table)
