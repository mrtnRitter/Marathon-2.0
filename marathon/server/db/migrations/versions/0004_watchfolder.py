"""Create persistent watchfolder state tables."""

# IMPORTS
from alembic import op
import sqlalchemy as sa


# CONFIG
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


# FUNC
def upgrade() -> None:
    """Create watchfolder module and file state tables."""
    op.create_table(
        "watch_modules",
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("module_id", sa.Text(), nullable=False),
        sa.Column("next_scan_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workflow_id", "module_id"),
    )
    op.create_index("ix_watch_modules_next_scan_at", "watch_modules", ["next_scan_at"])
    op.create_table(
        "watch_files",
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("module_id", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False),
        sa.Column("stable_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_id", sa.Text(), nullable=True),
        sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workflow_id", "module_id"],
            ["watch_modules.workflow_id", "watch_modules.module_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("workflow_id", "module_id", "path"),
    )
    op.create_index("ix_watch_files_asset_id", "watch_files", ["asset_id"])


def downgrade() -> None:
    """Drop watchfolder state tables and indexes."""
    op.drop_index("ix_watch_files_asset_id", table_name="watch_files")
    op.drop_table("watch_files")
    op.drop_index("ix_watch_modules_next_scan_at", table_name="watch_modules")
    op.drop_table("watch_modules")
