"""Add users, authentication, sessions, and workflow access shares."""

# IMPORTS
from alembic import op
import sqlalchemy as sa


# CONFIG
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None
live_tables = ("users", "workflow_shares")


# FUNC
def upgrade() -> None:
    """Create user authentication tables and workflow ownership/shares."""
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("external_subject", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("external_subject", name="uq_users_external_subject"),
        sa.CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
    )
    op.create_table(
        "auth_codes",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_auth_codes_email_created_at", "auth_codes", ["email", "created_at"])
    op.create_table(
        "auth_failures",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_auth_failures_email_failed_at", "auth_failures", ["email", "failed_at"])
    op.create_table(
        "auth_locks",
        sa.Column("email", sa.Text(), primary_key=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("user_agent", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "workflow_shares",
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("access", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], name="fk_workflow_shares_workflow_id_workflows", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_workflow_shares_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workflow_id", "user_id", name="pk_workflow_shares"),
        sa.CheckConstraint("access IN ('view', 'run', 'edit')", name="ck_workflow_shares_access"),
    )
    op.add_column(
        "workflows",
        sa.Column("owner_user_id", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workflows_owner_user_id_users", "workflows", "users", ["owner_user_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_workflows_owner_user_id", "workflows", ["owner_user_id"])

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
    """Remove user tables, workflow ownership, and live-change triggers."""
    for table in reversed(live_tables):
        op.execute(f"DROP TRIGGER IF EXISTS marathon_live_{table}_write ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS marathon_live_{table}_update ON {table}")

    op.drop_index("ix_workflows_owner_user_id", table_name="workflows")
    op.drop_constraint("fk_workflows_owner_user_id_users", "workflows", type_="foreignkey")
    op.drop_column("workflows", "owner_user_id")
    op.drop_table("workflow_shares")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("auth_locks")
    op.drop_index("ix_auth_failures_email_failed_at", table_name="auth_failures")
    op.drop_table("auth_failures")
    op.drop_index("ix_auth_codes_email_created_at", table_name="auth_codes")
    op.drop_table("auth_codes")
    op.drop_table("users")
