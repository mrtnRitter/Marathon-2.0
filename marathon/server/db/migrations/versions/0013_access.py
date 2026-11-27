"""Unify workflow and macro access data."""

# IMPORTS
from alembic import op
import sqlalchemy as sa


# CONFIG
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


# FUNC
def _create_live_triggers(table: str) -> None:
    """Attach the standard live-change triggers to a table."""
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


def _drop_live_triggers(table: str) -> None:
    """Remove the standard live-change triggers from a table."""
    op.execute(f"DROP TRIGGER IF EXISTS marathon_live_{table}_write ON {table}")
    op.execute(f"DROP TRIGGER IF EXISTS marathon_live_{table}_update ON {table}")


def upgrade() -> None:
    """Replace workflow shares with unified access tables."""
    op.create_table(
        "object_shares",
        sa.Column("object_type", sa.Text(), nullable=False),
        sa.Column("object_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("access", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_object_shares_user_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "object_type IN ('workflow', 'macro')", name="ck_object_shares_object_type"
        ),
        sa.CheckConstraint(
            "access IN ('view', 'use', 'edit')", name="ck_object_shares_access"
        ),
    )
    op.create_index(
        "ux_object_shares_target",
        "object_shares",
        [sa.text("object_type"), sa.text("object_id"), sa.text("COALESCE(user_id, '')")],
        unique=True,
    )
    op.create_index("ix_object_shares_user_id", "object_shares", ["user_id"])
    op.create_table(
        "edit_locks",
        sa.Column("object_type", sa.Text(), nullable=False),
        sa.Column("object_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_edit_locks_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("object_type", "object_id", name="pk_edit_locks"),
        sa.CheckConstraint(
            "object_type IN ('workflow', 'macro')", name="ck_edit_locks_object_type"
        ),
    )
    op.create_table(
        "object_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("object_type", sa.Text(), nullable=False),
        sa.Column("object_id", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("actor_user_id", sa.Text(), nullable=True),
        sa.Column("actor_name", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name="fk_object_events_actor_user_id_users", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_object_events_object", "object_events", ["object_type", "object_id", "ts"])
    op.add_column("asset_history", sa.Column("module_id", sa.Text(), nullable=True))

    op.execute(sa.text("""
        INSERT INTO object_shares (object_type, object_id, user_id, access, updated_at)
        SELECT 'workflow', workflow_id, user_id,
               CASE access WHEN 'run' THEN 'use' ELSE access END,
               updated_at
        FROM workflow_shares
    """))
    op.execute(sa.text("""
        INSERT INTO object_shares (object_type, object_id, user_id, access)
        SELECT 'macro', id, NULL, 'use'
        FROM macros
        WHERE archived_at IS NULL
    """))
    op.drop_table("workflow_shares")
    _create_live_triggers("object_shares")


def downgrade() -> None:
    """Restore workflow shares and remove unified access data."""
    op.create_table(
        "workflow_shares",
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("access", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"],
            name="fk_workflow_shares_workflow_id_workflows", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_workflow_shares_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("workflow_id", "user_id", name="pk_workflow_shares"),
        sa.CheckConstraint("access IN ('view', 'run', 'edit')", name="ck_workflow_shares_access"),
    )
    op.execute(sa.text("""
        INSERT INTO workflow_shares (workflow_id, user_id, access, updated_at)
        SELECT object_id, user_id,
               CASE access WHEN 'use' THEN 'run' ELSE access END,
               updated_at
        FROM object_shares
        WHERE object_type = 'workflow' AND user_id IS NOT NULL
    """))
    _create_live_triggers("workflow_shares")

    _drop_live_triggers("object_shares")
    op.drop_table("edit_locks")
    op.drop_table("object_events")
    op.drop_table("object_shares")
    op.drop_column("asset_history", "module_id")
