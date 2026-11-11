"""Add descriptions to workflow modules."""

# IMPORTS
from alembic import op
import sqlalchemy as sa


# CONFIG
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


# FUNC
def upgrade() -> None:
    """Add the module description column."""
    op.add_column("workflow_modules", sa.Column("description", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    """Remove the module description column."""
    op.drop_column("workflow_modules", "description")
