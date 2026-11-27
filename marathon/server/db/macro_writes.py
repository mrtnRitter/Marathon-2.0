"""Transactional reads and writes for versioned macros."""

# IMPORTS
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from marathon.server.auth.sessions import AuthUser
from marathon.server.macros.definition import (
    macro_hull,
    standard_definition,
    validate_definition,
)


# EXCEPTIONS
class MacroForbidden(Exception):
    """Raised when a user cannot inspect or change a macro."""


class MacroConflict(Exception):
    """Raised when a macro was updated from a stale version."""


class MacroInUse(Exception):
    """Raised when current workflows still use a macro."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(", ".join(names))


class StandardMacroDelete(Exception):
    """Raised when attempting to archive a standard macro."""


# QUERIES
def can_edit_macro(owner_user_id: str | None, user: AuthUser) -> bool:
    """Return whether the user is an administrator or the macro owner."""
    return user.is_admin or (owner_user_id is not None and owner_user_id == user.id)


def module_type_map(connection: Connection) -> dict[str, dict[str, Any]]:
    """Load every module-type row from the database, keyed by its ID."""
    rows = connection.execute(text(
        "SELECT id, name, category, icon, description, has_input, outputs, settings, sort_order "
        "FROM module_types ORDER BY id"
    )).mappings()
    return {row["id"]: dict(row) for row in rows}


def load_macros(connection: Connection, macro_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Load macro metadata by ID, including archived macros."""
    ids = list(dict.fromkeys(macro_ids))
    if not ids:
        return {}
    statement = text(
        "SELECT id, owner_user_id, current_version, archived_at FROM macros WHERE id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    rows = connection.execute(statement, {"ids": ids}).mappings()
    return {row["id"]: dict(row) for row in rows}


def load_definitions(
    connection: Connection,
    keys: Iterable[tuple[str, int]],
) -> dict[tuple[str, int], dict[str, Any]]:
    """Load the requested macro-version definitions."""
    unique_keys = list(dict.fromkeys(keys))
    if not unique_keys:
        return {}
    predicates = []
    parameters: dict[str, Any] = {}
    for index, (macro_id, version) in enumerate(unique_keys):
        macro_key = f"macro_id_{index}"
        version_key = f"version_{index}"
        predicates.append(f"(macro_id = :{macro_key} AND version = :{version_key})")
        parameters[macro_key] = macro_id
        parameters[version_key] = version
    rows = connection.execute(text(
        "SELECT macro_id, version, definition FROM macro_versions WHERE " + " OR ".join(predicates)
    ), parameters).mappings()
    return {(row["macro_id"], row["version"]): row["definition"] for row in rows}


def _macro_query(connection: Connection, macro_id: str | None = None) -> list[dict[str, Any]]:
    """Load current macro definitions and their list metadata."""
    filters = "WHERE macro.archived_at IS NULL"
    parameters: dict[str, Any] = {}
    if macro_id is not None:
        filters += " AND macro.id = :macro_id"
        parameters["macro_id"] = macro_id
    rows = connection.execute(text(f"""
        SELECT macro.id, macro.owner_user_id, macro.current_version, macro.archived_at,
               macro.updated_at, version_row.definition,
               owner.id AS joined_owner_id, owner.email AS owner_email,
               owner.display_name AS owner_display_name,
               (
                   SELECT count(DISTINCT usage_workflow.id)
                   FROM workflow_instances AS instance
                   JOIN workflows AS usage_workflow
                     ON usage_workflow.id = instance.workflow_id
                    AND usage_workflow.current_version = instance.version
                   WHERE instance.macro_id = macro.id
               ) AS usage
        FROM macros AS macro
        JOIN macro_versions AS version_row
          ON version_row.macro_id = macro.id
         AND version_row.version = macro.current_version
        LEFT JOIN users AS owner ON owner.id = macro.owner_user_id
        {filters}
        ORDER BY (version_row.definition ->> 'sort_order')::integer,
                 version_row.definition ->> 'name', macro.id
    """), parameters).mappings()
    return [dict(row) for row in rows]


def _macro_payload(
    row: Mapping[str, Any],
    module_types: Mapping[str, Mapping[str, Any]],
    user: AuthUser,
    include_definition: bool = False,
) -> dict[str, Any]:
    """Build a public macro hull with owner, access, usage, and timestamp."""
    payload = macro_hull(row["id"], row["current_version"], row["definition"], module_types)
    owner = None
    if row["joined_owner_id"] is not None:
        owner = {
            "id": row["joined_owner_id"],
            "email": row["owner_email"],
            "display_name": row["owner_display_name"],
        }
    payload.update({
        "owner": owner,
        "can_edit": can_edit_macro(row["owner_user_id"], user),
        "usage": row["usage"],
        "updated_at": row["updated_at"].isoformat(),
    })
    if include_definition:
        payload["definition"] = row["definition"]
    return payload


def list_macros(connection: Connection, user: AuthUser) -> list[dict[str, Any]]:
    """List all non-archived modules as sorted hulls."""
    module_types = module_type_map(connection)
    return [
        _macro_payload(row, module_types, user)
        for row in _macro_query(connection)
    ]


def get_macro(connection: Connection, macro_id: str, user: AuthUser) -> dict[str, Any] | None:
    """Return a macro's editable detail or None when missing or archived."""
    rows = _macro_query(connection, macro_id)
    if not rows:
        return None
    row = rows[0]
    if not can_edit_macro(row["owner_user_id"], user):
        raise MacroForbidden
    return _macro_payload(row, module_type_map(connection), user, include_definition=True)


# WRITES
def ensure_standard_macros(connection: Connection) -> int:
    """Insert version-one standard macros for module types without a matching macro."""
    module_types = module_type_map(connection)
    insert_macro = text(
        "INSERT INTO macros (id, owner_user_id, current_version) "
        "VALUES (:id, NULL, 1) ON CONFLICT (id) DO NOTHING RETURNING id"
    )
    insert_version = text(
        "INSERT INTO macro_versions (macro_id, version, definition, created_by_user_id) "
        "VALUES (:macro_id, 1, :definition, NULL)"
    ).bindparams(bindparam("definition", type_=JSONB))
    created = 0
    for type_id, module_type in module_types.items():
        inserted_id = connection.execute(insert_macro, {"id": type_id}).scalar_one_or_none()
        if inserted_id is None:
            continue
        connection.execute(insert_version, {
            "macro_id": type_id,
            "definition": standard_definition(module_type),
        })
        created += 1
    return created


def create_macro(connection: Connection, definition: Mapping[str, Any], user: AuthUser) -> str:
    """Validate and create a user-owned macro with its initial version."""
    validate_definition(definition, module_type_map(connection))
    macro_id = "mc-" + uuid4().hex[:12]
    connection.execute(text(
        "INSERT INTO macros (id, owner_user_id, current_version) "
        "VALUES (:id, :owner_user_id, 1)"
    ), {"id": macro_id, "owner_user_id": user.id})
    statement = text(
        "INSERT INTO macro_versions (macro_id, version, definition, created_by_user_id) "
        "VALUES (:macro_id, 1, :definition, :created_by_user_id)"
    ).bindparams(bindparam("definition", type_=JSONB))
    connection.execute(statement, {
        "macro_id": macro_id,
        "definition": dict(definition),
        "created_by_user_id": user.id,
    })
    return macro_id


def update_macro(
    connection: Connection,
    macro_id: str,
    base_version: int,
    definition: Mapping[str, Any],
    user: AuthUser,
) -> int | None:
    """Append a new validated version if the supplied base version is current."""
    row = connection.execute(text(
        "SELECT id, owner_user_id, current_version, archived_at "
        "FROM macros WHERE id = :macro_id FOR UPDATE"
    ), {"macro_id": macro_id}).mappings().one_or_none()
    if row is None or row["archived_at"] is not None:
        return None
    if not can_edit_macro(row["owner_user_id"], user):
        raise MacroForbidden
    if base_version != row["current_version"]:
        raise MacroConflict
    validate_definition(definition, module_type_map(connection))
    new_version = row["current_version"] + 1
    statement = text(
        "INSERT INTO macro_versions (macro_id, version, definition, created_by_user_id) "
        "VALUES (:macro_id, :version, :definition, :created_by_user_id)"
    ).bindparams(bindparam("definition", type_=JSONB))
    connection.execute(statement, {
        "macro_id": macro_id,
        "version": new_version,
        "definition": dict(definition),
        "created_by_user_id": user.id,
    })
    connection.execute(text(
        "UPDATE macros SET current_version = :version, updated_at = now() WHERE id = :macro_id"
    ), {"macro_id": macro_id, "version": new_version})
    return new_version


def _current_workflow_names(connection: Connection, macro_id: str) -> list[str]:
    """Return sorted distinct names of workflows currently using a macro."""
    names = connection.execute(text("""
        SELECT DISTINCT workflow.name, workflow.id
        FROM workflow_instances AS instance
        JOIN workflows AS workflow
          ON workflow.id = instance.workflow_id
         AND workflow.current_version = instance.version
        WHERE instance.macro_id = :macro_id
        ORDER BY workflow.name, workflow.id
    """), {"macro_id": macro_id}).all()
    return [row[0] for row in names]


def archive_macro(connection: Connection, macro_id: str, user: AuthUser) -> bool:
    """Soft-delete an editable user macro only when no current workflow uses it."""
    row = connection.execute(text(
        "SELECT id, owner_user_id, archived_at FROM macros WHERE id = :macro_id FOR UPDATE"
    ), {"macro_id": macro_id}).mappings().one_or_none()
    if row is None or row["archived_at"] is not None:
        return False
    if not can_edit_macro(row["owner_user_id"], user):
        raise MacroForbidden
    if row["owner_user_id"] is None:
        raise StandardMacroDelete
    names = _current_workflow_names(connection, macro_id)
    if names:
        raise MacroInUse(names)
    connection.execute(text(
        "UPDATE macros SET archived_at = now(), updated_at = now() WHERE id = :macro_id"
    ), {"macro_id": macro_id})
    return True


def outdated_workflows(connection: Connection, macro_id: str, user: AuthUser) -> list[dict[str, str]]:
    """List the user's current workflows that use an older macro version."""
    macro = load_macros(connection, [macro_id]).get(macro_id)
    if macro is None or macro["archived_at"] is not None:
        return []
    if not can_edit_macro(macro["owner_user_id"], user):
        raise MacroForbidden
    rows = connection.execute(text("""
        SELECT DISTINCT workflow.id, workflow.name
        FROM workflows AS workflow
        JOIN workflow_instances AS instance
          ON instance.workflow_id = workflow.id
         AND instance.version = workflow.current_version
        WHERE workflow.owner_user_id = :owner_user_id
          AND instance.macro_id = :macro_id
          AND instance.macro_version < :current_version
        ORDER BY workflow.name, workflow.id
    """), {
        "owner_user_id": user.id,
        "macro_id": macro_id,
        "current_version": macro["current_version"],
    }).mappings()
    return [{"id": row["id"], "name": row["name"]} for row in rows]
