"""Add worker-client credentials, approvals, and control sessions."""

# IMPORTS
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# CONFIG
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


# FUNC
def upgrade() -> None:
    """Add worker client state and ephemeral control credentials."""
    op.drop_constraint("ck_workers_status", "workers", type_="check")
    op.create_check_constraint(
        "ck_workers_status", "workers", "status IN ('ready', 'draining', 'stopping', 'stopped')"
    )
    op.add_column("workers", sa.Column("token_hash", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_workers_token_hash", "workers", ["token_hash"])
    op.add_column(
        "workers", sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "workers",
        sa.Column(
            "availability_config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.add_column(
        "workers", sa.Column("availability_revision", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "workers", sa.Column("client_available", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "workers", sa.Column("availability_reason", sa.Text(), nullable=False, server_default="")
    )
    op.create_table(
        "worker_ephemeral_tokens",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], ondelete="CASCADE"),
        sa.CheckConstraint("purpose IN ('control_link', 'presence')", name="ck_worker_ephemeral_tokens_purpose"),
    )
    op.create_index(
        "ix_worker_ephemeral_tokens_worker_id_expires_at",
        "worker_ephemeral_tokens",
        ["worker_id", "expires_at"],
    )
    op.create_table(
        "worker_control_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_worker_control_sessions_token_hash"),
    )
    op.create_index("ix_worker_control_sessions_worker_id", "worker_control_sessions", ["worker_id"])


def downgrade() -> None:
    """Remove worker-client credentials and restore the previous worker states."""
    op.execute("UPDATE workers SET status = 'stopping' WHERE status = 'draining'")
    op.drop_constraint("ck_workers_status", "workers", type_="check")
    op.create_check_constraint(
        "ck_workers_status", "workers", "status IN ('ready', 'stopping', 'stopped')"
    )
    op.drop_index("ix_worker_control_sessions_worker_id", table_name="worker_control_sessions")
    op.drop_table("worker_control_sessions")
    op.drop_index("ix_worker_ephemeral_tokens_worker_id_expires_at", table_name="worker_ephemeral_tokens")
    op.drop_table("worker_ephemeral_tokens")
    op.drop_constraint("uq_workers_token_hash", "workers", type_="unique")
    op.drop_column("workers", "availability_reason")
    op.drop_column("workers", "client_available")
    op.drop_column("workers", "availability_revision")
    op.drop_column("workers", "availability_config")
    op.drop_column("workers", "approved")
    op.drop_column("workers", "token_hash")
