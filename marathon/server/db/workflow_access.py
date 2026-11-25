"""Workflow access checks and share-management queries."""

from typing import Any, Mapping, Sequence

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from marathon.server.auth.sessions import AuthUser


access_levels = ("view", "run", "edit", "owner", "admin")


def workflow_access(connection: Connection, user: AuthUser, workflow_id: str) -> str | None:
    """Return a user's effective workflow access, or None if the workflow is hidden."""
    workflow = connection.execute(
        text("SELECT owner_user_id FROM workflows WHERE id = :workflow_id"),
        {"workflow_id": workflow_id},
    ).mappings().one_or_none()
    if workflow is None:
        return None
    owner_user_id = workflow["owner_user_id"]
    if user.is_admin:
        return "admin"
    if owner_user_id == user.id:
        return "owner"
    shared_access = connection.execute(
        text("SELECT access FROM workflow_shares WHERE workflow_id = :workflow_id AND user_id = :user_id"),
        {"workflow_id": workflow_id, "user_id": user.id},
    ).scalar_one_or_none()
    return str(shared_access) if shared_access is not None else None


def require_workflow_access(
    connection: Connection,
    user: AuthUser,
    workflow_id: str,
    minimum: str,
) -> str:
    """Require a minimum access level, hiding workflows without any access."""
    if minimum not in access_levels:
        raise ValueError(f"Unbekannte Mindeststufe: {minimum}")
    access = workflow_access(connection, user, workflow_id)
    if access is None:
        raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    if access_levels.index(access) < access_levels.index(minimum):
        raise HTTPException(status_code=403, detail="Keine Berechtigung für diesen Workflow")
    return access


def workflow_access_filter(user: AuthUser | None) -> tuple[str, dict[str, str]]:
    """Return a SQL predicate for visible rows joined as the `workflow` alias."""
    if user is None or user.is_admin:
        return "TRUE", {}
    return (
        "(workflow.owner_user_id = :workflow_access_user_id OR EXISTS ("
        "SELECT 1 FROM workflow_shares AS workflow_share "
        "WHERE workflow_share.workflow_id = workflow.id "
        "AND workflow_share.user_id = :workflow_access_user_id))",
        {"workflow_access_user_id": user.id},
    )


def workflow_owner_id(connection: Connection, workflow_id: str) -> str | None:
    """Return the workflow owner ID, or None for a missing or ownerless workflow."""
    return connection.execute(
        text("SELECT owner_user_id FROM workflows WHERE id = :workflow_id"),
        {"workflow_id": workflow_id},
    ).scalar_one_or_none()


def user_exists(connection: Connection, user_id: str) -> bool:
    """Return whether a user ID exists."""
    return connection.execute(
        text("SELECT 1 FROM users WHERE id = :user_id"), {"user_id": user_id}
    ).scalar_one_or_none() is not None


def list_workflow_shares(connection: Connection, workflow_id: str) -> list[dict[str, Any]]:
    """List workflow shares alphabetically by email."""
    rows = connection.execute(
        text(
            "SELECT share.user_id, shared_user.email, shared_user.display_name, share.access "
            "FROM workflow_shares AS share JOIN users AS shared_user ON shared_user.id = share.user_id "
            "WHERE share.workflow_id = :workflow_id ORDER BY shared_user.email, share.user_id"
        ),
        {"workflow_id": workflow_id},
    ).mappings()
    return [dict(row) for row in rows]


def replace_workflow_shares(
    connection: Connection,
    workflow_id: str,
    shares: Sequence[Mapping[str, str]],
) -> None:
    """Replace every share for a workflow with the supplied validated rows."""
    connection.execute(
        text("DELETE FROM workflow_shares WHERE workflow_id = :workflow_id"),
        {"workflow_id": workflow_id},
    )
    if shares:
        connection.execute(
            text(
                "INSERT INTO workflow_shares (workflow_id, user_id, access) "
                "VALUES (:workflow_id, :user_id, :access)"
            ),
            [{"workflow_id": workflow_id, **share} for share in shares],
        )


def set_workflow_owner(connection: Connection, workflow_id: str, user_id: str) -> bool:
    """Set an existing workflow owner and remove that user's share entry."""
    connection.execute(
        text("DELETE FROM workflow_shares WHERE workflow_id = :workflow_id AND user_id = :user_id"),
        {"workflow_id": workflow_id, "user_id": user_id},
    )
    result = connection.execute(
        text("UPDATE workflows SET owner_user_id = :user_id, updated_at = now() WHERE id = :workflow_id"),
        {"workflow_id": workflow_id, "user_id": user_id},
    )
    return result.rowcount > 0
