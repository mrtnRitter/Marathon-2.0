"""Unified access checks and sharing helpers for workflows and macros."""

# IMPORTS
from typing import Any, Iterable, Mapping, Sequence

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from marathon.server.auth.sessions import AuthUser


# CONFIG
app_name = "marathon-object-access"
app_version = "1.0"
object_types = ("workflow", "macro")
access_levels = ("view", "use", "edit", "owner")
share_levels = ("view", "use", "edit")
_object_tables = {"workflow": "workflows", "macro": "macros"}
_access_ranks = {level: rank for rank, level in enumerate(access_levels, start=1)}


class ShareValidationError(ValueError):
    """A share or owner value violates the access-management contract."""


# FUNC
def _object_table(object_type: str) -> str:
    """Return the table for a supported object type."""
    try:
        return _object_tables[object_type]
    except KeyError as error:
        raise ValueError(f"Unbekannter Objekttyp: {object_type}") from error


def _validate_minimum(minimum: str) -> int:
    """Return a minimum access rank or reject an unknown level."""
    try:
        return _access_ranks[minimum]
    except KeyError as error:
        raise ValueError(f"Unbekannte Mindeststufe: {minimum}") from error


def access_level_sql(user: AuthUser | None, object_type: str, alias: str) -> tuple[str, dict[str, Any]]:
    """Return a row expression for the effective access level or NULL."""
    _object_table(object_type)
    if user is None or user.is_admin:
        return "'owner'", {}
    user_parameter = f"{alias}_access_user_id"
    type_parameter = f"{alias}_access_object_type"
    return (
        f"CASE WHEN {alias}.owner_user_id = :{user_parameter} THEN 'owner' ELSE ("
        "SELECT share.access FROM object_shares AS share "
        f"WHERE share.object_type = :{type_parameter} AND share.object_id = {alias}.id "
        f"AND (share.user_id = :{user_parameter} OR share.user_id IS NULL) "
        "ORDER BY CASE share.access WHEN 'view' THEN 1 WHEN 'use' THEN 2 WHEN 'edit' THEN 3 ELSE 0 END DESC "
        "LIMIT 1) END",
        {user_parameter: user.id, type_parameter: object_type},
    )


def object_access_map(
    connection: Connection,
    user: AuthUser | None,
    object_type: str,
    object_ids: Iterable[str],
) -> dict[str, str | None]:
    """Resolve access for many IDs with a single database query."""
    table = _object_table(object_type)
    ids = list(dict.fromkeys(object_ids))
    if not ids:
        return {}
    level_sql, parameters = access_level_sql(user, object_type, "target")
    query = text(
        f"SELECT target.id, {level_sql} AS access FROM {table} AS target "
        "WHERE target.id IN :object_access_ids"
    ).bindparams(bindparam("object_access_ids", expanding=True))
    rows = connection.execute(query, {**parameters, "object_access_ids": ids}).mappings()
    result: dict[str, str | None] = {object_id: None for object_id in ids}
    result.update({row["id"]: row["access"] for row in rows})
    return result


def object_access(connection: Connection, user: AuthUser | None, object_type: str, object_id: str) -> str | None:
    """Return an object's effective access level, or None if it is hidden."""
    return object_access_map(connection, user, object_type, [object_id])[object_id]


def require_access(
    connection: Connection,
    user: AuthUser | None,
    object_type: str,
    object_id: str,
    minimum: str,
) -> str:
    """Require an access level while hiding objects without any access."""
    _object_table(object_type)
    minimum_rank = _validate_minimum(minimum)
    access = object_access(connection, user, object_type, object_id)
    if access is None:
        detail = "Workflow nicht gefunden" if object_type == "workflow" else "Modul nicht gefunden"
        raise HTTPException(status_code=404, detail=detail)
    if _access_ranks[access] < minimum_rank:
        detail = "Keine Berechtigung für diesen Workflow" if object_type == "workflow" else "Keine Berechtigung für dieses Modul"
        raise HTTPException(status_code=403, detail=detail)
    return access


def access_filter_sql(
    user: AuthUser | None,
    object_type: str,
    alias: str,
    minimum: str = "view",
) -> tuple[str, dict[str, Any]]:
    """Return a SQL predicate for rows meeting the minimum access level."""
    _object_table(object_type)
    minimum_rank = _validate_minimum(minimum)
    if user is None or user.is_admin:
        return "TRUE", {}
    level_sql, parameters = access_level_sql(user, object_type, alias)
    predicate = (
        f"(CASE ({level_sql}) WHEN 'view' THEN 1 WHEN 'use' THEN 2 "
        f"WHEN 'edit' THEN 3 WHEN 'owner' THEN 4 ELSE 0 END >= {minimum_rank})"
    )
    return predicate, parameters


def owner_record(connection: Connection, object_type: str, object_id: str) -> dict[str, str] | None:
    """Return the user owner details, or None for ownerless or missing objects."""
    table = _object_table(object_type)
    row = connection.execute(
        text(
            f"SELECT owner.id, owner.email, owner.display_name FROM {table} AS object "
            "JOIN users AS owner ON owner.id = object.owner_user_id WHERE object.id = :object_id"
        ),
        {"object_id": object_id},
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def owner_display(owner: Mapping[str, Any] | None) -> str:
    """Format a user's owner label, using Admin for ownerless objects."""
    if owner is None:
        return "Admin"
    return str(owner.get("display_name") or owner.get("email") or "")


def actor_name(user: AuthUser) -> str:
    """Return the display name used in the object event history."""
    if user.id == "emergency":
        return "Notfallzugang"
    return user.display_name or user.email


def list_shares(connection: Connection, object_type: str, object_id: str) -> list[dict[str, Any]]:
    """List global access first, followed by user shares ordered by email."""
    _object_table(object_type)
    rows = connection.execute(
        text(
            "SELECT share.user_id, shared_user.email, shared_user.display_name, share.access "
            "FROM object_shares AS share LEFT JOIN users AS shared_user ON shared_user.id = share.user_id "
            "WHERE share.object_type = :object_type AND share.object_id = :object_id "
            "ORDER BY CASE WHEN share.user_id IS NULL THEN 0 ELSE 1 END, shared_user.email, share.user_id"
        ),
        {"object_type": object_type, "object_id": object_id},
    ).mappings()
    shares = []
    for row in rows:
        share = dict(row)
        if share["user_id"] is None:
            share.update(email="", display_name="Alle")
        shares.append(share)
    return shares


def _owner_user_id(connection: Connection, object_type: str, object_id: str) -> str | None:
    """Return the user owner ID for a supported object."""
    table = _object_table(object_type)
    return connection.execute(
        text(f"SELECT owner_user_id FROM {table} WHERE id = :object_id"),
        {"object_id": object_id},
    ).scalar_one_or_none()


def _user_exists(connection: Connection, user_id: str) -> bool:
    """Return whether a share recipient or owner account exists."""
    return connection.execute(
        text("SELECT 1 FROM users WHERE id = :user_id"), {"user_id": user_id}
    ).scalar_one_or_none() is not None


def replace_shares(
    connection: Connection,
    object_type: str,
    object_id: str,
    shares: Sequence[Mapping[str, Any]],
) -> None:
    """Validate every share before replacing all shares for an object."""
    _object_table(object_type)
    owner_id = _owner_user_id(connection, object_type, object_id)
    user_ids = [share.get("user_id") for share in shares]
    seen: set[str | None] = set()
    for user_id in user_ids:
        if user_id in seen:
            if user_id is None:
                raise ShareValidationError("„Alle“ doppelt")
            raise ShareValidationError(f"Nutzer doppelt: {user_id}")
        seen.add(user_id)

    for share in shares:
        access = share.get("access")
        if access not in share_levels:
            raise ShareValidationError(f"Ungültige Freigabestufe: {access}")

    if any(user_id is not None and user_id == owner_id for user_id in user_ids):
        raise ShareValidationError("Der Besitzer kann nicht freigegeben werden")

    for user_id in user_ids:
        if user_id is not None and (user_id == "emergency" or not _user_exists(connection, user_id)):
            raise ShareValidationError(f"Unbekannter Nutzer: {user_id}")

    validated = [
        {"user_id": share.get("user_id"), "access": share.get("access")}
        for share in shares
    ]

    connection.execute(
        text("DELETE FROM object_shares WHERE object_type = :object_type AND object_id = :object_id"),
        {"object_type": object_type, "object_id": object_id},
    )
    if validated:
        connection.execute(
            text(
                "INSERT INTO object_shares (object_type, object_id, user_id, access, updated_at) "
                "VALUES (:object_type, :object_id, :user_id, :access, now())"
            ),
            [{"object_type": object_type, "object_id": object_id, **share} for share in validated],
        )


def set_owner(connection: Connection, object_type: str, object_id: str, user_id: str | None) -> None:
    """Set a user owner after validation and remove their redundant share."""
    table = _object_table(object_type)
    if user_id is not None and (user_id == "emergency" or not _user_exists(connection, user_id)):
        raise ShareValidationError(f"Unbekannter Nutzer: {user_id}")
    if user_id is not None:
        connection.execute(
            text(
                "DELETE FROM object_shares WHERE object_type = :object_type "
                "AND object_id = :object_id AND user_id = :user_id"
            ),
            {"object_type": object_type, "object_id": object_id, "user_id": user_id},
        )
    connection.execute(
        text(f"UPDATE {table} SET owner_user_id = :user_id, updated_at = now() WHERE id = :object_id"),
        {"user_id": user_id, "object_id": object_id},
    )


def delete_object_access(connection: Connection, object_type: str, object_id: str) -> None:
    """Remove shares and editing locks for an object."""
    _object_table(object_type)
    parameters = {"object_type": object_type, "object_id": object_id}
    connection.execute(
        text("DELETE FROM object_shares WHERE object_type = :object_type AND object_id = :object_id"), parameters
    )
    connection.execute(
        text("DELETE FROM edit_locks WHERE object_type = :object_type AND object_id = :object_id"), parameters
    )


def log_event(connection: Connection, actor: AuthUser, object_type: str, object_id: str, text_value: str) -> None:
    """Append an event to an object's history."""
    _object_table(object_type)
    connection.execute(
        text(
            "INSERT INTO object_events (object_type, object_id, actor_user_id, actor_name, text) "
            "VALUES (:object_type, :object_id, :actor_user_id, :actor_name, :text)"
        ),
        {
            "object_type": object_type,
            "object_id": object_id,
            "actor_user_id": actor.id,
            "actor_name": actor_name(actor),
            "text": text_value,
        },
    )


def log_intervention(connection: Connection, actor: AuthUser, object_type: str, object_id: str, text_value: str) -> bool:
    """Log an admin intervention only when another user owns the object."""
    _object_table(object_type)
    owner_id = _owner_user_id(connection, object_type, object_id)
    if not actor.is_admin or owner_id is None or owner_id == actor.id:
        return False
    log_event(connection, actor, object_type, object_id, text_value)
    return True


def list_events(
    connection: Connection,
    object_type: str,
    object_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List newest events first, capped at 200 entries."""
    _object_table(object_type)
    bounded_limit = max(0, min(limit, 200))
    rows = connection.execute(
        text(
            "SELECT ts, actor_name AS actor, text FROM object_events "
            "WHERE object_type = :object_type AND object_id = :object_id "
            "ORDER BY ts DESC, id DESC LIMIT :limit"
        ),
        {"object_type": object_type, "object_id": object_id, "limit": bounded_limit},
    ).mappings()
    return [dict(row) for row in rows]
