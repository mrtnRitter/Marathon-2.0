"""Migrate module types and workflows to versioned macros."""

# IMPORTS
import json
from typing import Any, Mapping

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# CONFIG
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


# FUNC
def _standard_definition(module_type: Mapping[str, Any]) -> dict[str, Any]:
    """Build the standard macro definition for one legacy module type."""
    outputs = [
        output for output in module_type["outputs"]
        if isinstance(output, dict) and "id" in output
    ]
    settings = [
        setting for setting in module_type["settings"]
        if isinstance(setting, dict) and "key" in setting
    ]
    return {
        "name": module_type["name"],
        "category": module_type["category"],
        "icon": module_type["icon"],
        "description": module_type["description"],
        "sort_order": module_type["sort_order"],
        "blocks": [{
            "id": "main",
            "type_id": module_type["id"],
            "name": module_type["name"],
            "position": {"x": 0, "y": 0},
            "settings": {},
        }],
        "connections": [],
        "entry_block_id": "main" if module_type["has_input"] else None,
        "outputs": [{
            "id": output["id"],
            "label": output["label"],
            "description": output.get("description", ""),
            "sources": [{"block_id": "main", "output_id": output["id"]}],
        } for output in outputs],
        "settings": [{
            "key": setting["key"],
            "block_id": "main",
            "setting_key": setting["key"],
            "label": setting["label"],
            "help": setting.get("help", ""),
        } for setting in settings],
    }


def _output_exits(module_type: Mapping[str, Any]) -> dict[str, str]:
    """Map valid legacy output IDs to runtime exit IDs."""
    return {
        output["id"]: output["id"]
        for output in module_type["outputs"]
        if isinstance(output, dict) and "id" in output
    }


def _blocking_foreign_keys() -> list[dict[str, str]]:
    """Find foreign keys referencing tables whose module IDs will change."""
    rows = op.get_bind().execute(sa.text("""
        SELECT
            child_namespace.nspname AS schema_name,
            child_table.relname AS table_name,
            fk.conname AS constraint_name,
            pg_get_constraintdef(fk.oid) AS definition
        FROM pg_constraint AS fk
        JOIN pg_class AS child_table ON child_table.oid = fk.conrelid
        JOIN pg_namespace AS child_namespace ON child_namespace.oid = child_table.relnamespace
        JOIN pg_class AS parent_table ON parent_table.oid = fk.confrelid
        JOIN pg_namespace AS parent_namespace ON parent_namespace.oid = parent_table.relnamespace
        WHERE fk.contype = 'f'
          AND parent_namespace.nspname = current_schema()
          AND parent_table.relname IN ('workflow_modules', 'watch_modules')
        ORDER BY child_namespace.nspname, child_table.relname, fk.conname
    """)).mappings()
    return [dict(row) for row in rows]


def _drop_foreign_keys(foreign_keys: list[dict[str, str]]) -> None:
    """Drop discovered foreign keys while preserving their catalog definitions."""
    for foreign_key in foreign_keys:
        op.drop_constraint(
            foreign_key["constraint_name"],
            foreign_key["table_name"],
            type_="foreignkey",
            schema=foreign_key["schema_name"],
        )


def _restore_foreign_keys(foreign_keys: list[dict[str, str]]) -> None:
    """Recreate discovered foreign keys with their original names and rules."""
    connection = op.get_bind()
    preparer = connection.dialect.identifier_preparer
    for foreign_key in foreign_keys:
        table = ".".join((
            preparer.quote_schema(foreign_key["schema_name"]),
            preparer.quote(foreign_key["table_name"]),
        ))
        name = preparer.quote(foreign_key["constraint_name"])
        connection.execute(sa.text(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} {foreign_key['definition']}"
        ))


def _rename_references() -> None:
    """Append the standard block ID to legacy module references once."""
    for table, columns in {
        "workflow_connections": ("source_module_id", "target_module_id"),
        "assets": ("current_module_id", "from_module_id"),
        "jobs": ("module_id",),
        "log_lines": ("module_id",),
        "watch_modules": ("module_id",),
        "watch_files": ("module_id",),
    }.items():
        for column in columns:
            op.execute(sa.text(
                f"UPDATE {table} SET {column} = {column} || '.main' "
                f"WHERE {column} IS NOT NULL AND strpos({column}, '.') = 0"
            ))


def _restore_references() -> None:
    """Remove the appended standard block ID from migrated references."""
    for table, columns in {
        "workflow_connections": ("source_module_id", "target_module_id"),
        "assets": ("current_module_id", "from_module_id"),
        "jobs": ("module_id",),
        "log_lines": ("module_id",),
        "watch_modules": ("module_id",),
        "watch_files": ("module_id",),
    }.items():
        for column in columns:
            op.execute(sa.text(
                f"UPDATE {table} SET {column} = left({column}, length({column}) - 5) "
                f"WHERE {column} IS NOT NULL AND right({column}, 5) = '.main' "
                f"AND EXISTS (SELECT 1 FROM workflow_modules AS runtime_module "
                f"WHERE runtime_module.workflow_id = {table}.workflow_id "
                f"AND runtime_module.id = {table}.{column} "
                "AND runtime_module.instance_id <> runtime_module.id)"
            ))


def upgrade() -> None:
    """Create macros and migrate legacy definitions, instances, and references."""
    op.create_table(
        "macros",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("owner_user_id", sa.Text(), nullable=True),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_macros_owner_user_id_users", ondelete="SET NULL"
        ),
    )
    op.create_table(
        "macro_versions",
        sa.Column("macro_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("created_by_user_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["macro_id"], ["macros.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("macro_id", "version"),
    )
    op.create_table(
        "workflow_instances",
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("macro_id", sa.Text(), nullable=False),
        sa.Column("macro_version", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("position_x", sa.Float(), nullable=False),
        sa.Column("position_y", sa.Float(), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(
            ["workflow_id", "version"], ["workflow_versions.workflow_id", "workflow_versions.version"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["macro_id", "macro_version"], ["macro_versions.macro_id", "macro_versions.version"]),
        sa.PrimaryKeyConstraint("workflow_id", "version", "id"),
    )
    op.create_index(
        "ix_workflow_instances_macro_id_macro_version",
        "workflow_instances",
        ["macro_id", "macro_version"],
    )
    op.create_table(
        "workflow_instance_connections",
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("source_output_id", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_id", "version", "source_id"],
            ["workflow_instances.workflow_id", "workflow_instances.version", "workflow_instances.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "version", "target_id"],
            ["workflow_instances.workflow_id", "workflow_instances.version", "workflow_instances.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workflow_id", "version", "source_id", "source_output_id"),
    )
    op.add_column("workflow_modules", sa.Column("instance_id", sa.Text(), nullable=True))
    op.add_column("workflow_modules", sa.Column("block_id", sa.Text(), nullable=True))
    op.add_column(
        "workflow_modules",
        sa.Column("exits", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    connection = op.get_bind()
    module_types = connection.execute(sa.text(
        "SELECT id, name, category, icon, description, has_input, outputs, settings, sort_order "
        "FROM module_types ORDER BY id"
    )).mappings().all()
    exits_by_type: dict[str, dict[str, str]] = {}
    for module_type in module_types:
        definition = _standard_definition(module_type)
        exits_by_type[module_type["id"]] = _output_exits(module_type)
        connection.execute(sa.text(
            "INSERT INTO macros (id, owner_user_id, current_version) VALUES (:id, NULL, 1)"
        ), {"id": module_type["id"]})
        connection.execute(sa.text(
            "INSERT INTO macro_versions (macro_id, version, definition) "
            "VALUES (:id, 1, CAST(:definition AS jsonb))"
        ), {"id": module_type["id"], "definition": json.dumps(definition)})

    connection.execute(sa.text("""
        INSERT INTO workflow_instances (
            workflow_id, version, id, macro_id, macro_version, name, description,
            position_x, position_y, settings, locked
        )
        SELECT workflow_id, version, id, type_id, 1, name, description,
               position_x, position_y, settings, locked
        FROM workflow_modules
    """))
    connection.execute(sa.text("""
        INSERT INTO workflow_instance_connections (
            workflow_id, version, source_id, source_output_id, target_id
        )
        SELECT workflow_id, version, source_module_id, source_output_id, target_module_id
        FROM workflow_connections
    """))
    for type_id, exits in exits_by_type.items():
        connection.execute(sa.text(
            "UPDATE workflow_modules SET instance_id = id, block_id = 'main', "
            "exits = CAST(:exits AS jsonb) WHERE type_id = :type_id"
        ), {"type_id": type_id, "exits": json.dumps(exits)})

    foreign_keys = _blocking_foreign_keys()
    _drop_foreign_keys(foreign_keys)
    connection.execute(sa.text(
        "UPDATE workflow_modules SET id = id || '.main' WHERE strpos(id, '.') = 0"
    ))
    _rename_references()
    _restore_foreign_keys(foreign_keys)


def downgrade() -> None:
    """Restore legacy module IDs and remove the macro schema."""
    connection = op.get_bind()
    has_built_modules = connection.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM workflow_modules WHERE block_id <> 'main')"
    )).scalar_one()
    if has_built_modules:
        raise RuntimeError("Downgrade nicht möglich: Workflows verwenden gebaute Module")

    foreign_keys = _blocking_foreign_keys()
    _drop_foreign_keys(foreign_keys)
    _restore_references()
    connection.execute(sa.text(
        "UPDATE workflow_modules SET id = left(id, length(id) - 5) "
        "WHERE right(id, 5) = '.main' AND instance_id <> id"
    ))
    _restore_foreign_keys(foreign_keys)

    op.drop_column("workflow_modules", "exits")
    op.drop_column("workflow_modules", "block_id")
    op.drop_column("workflow_modules", "instance_id")
    op.drop_table("workflow_instance_connections")
    op.drop_index("ix_workflow_instances_macro_id_macro_version", table_name="workflow_instances")
    op.drop_table("workflow_instances")
    op.drop_table("macro_versions")
    op.drop_table("macros")
