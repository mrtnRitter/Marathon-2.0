"""Database writes and queries for administrator-managed users."""

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from marathon.server.auth.codes import emergency_user_id, unlock


class UserNotFound(Exception):
    """Raised when a requested account does not exist."""


class UserProtected(Exception):
    """Raised when the fixed emergency account cannot be changed."""


class LastAdmin(Exception):
    """Raised when an operation would remove the last administrator."""


class SessionNotFound(Exception):
    """Raised when a session does not belong to the requested user."""


_USER_SELECT = """
SELECT u.id, u.email, u.display_name, u.role, u.created_at, u.last_login_at,
       count(s.id) FILTER (WHERE s.expires_at > now()) AS session_count,
       CASE WHEN l.locked_until > now() THEN l.locked_until ELSE NULL END AS locked_until
FROM users AS u
LEFT JOIN sessions AS s ON s.user_id = u.id
LEFT JOIN auth_locks AS l ON l.email = u.email
"""


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _user_payload(row: Mapping[str, object]) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "role": row["role"],
        "protected": row["id"] == emergency_user_id,
        "created_at": _timestamp(row["created_at"]),
        "last_login_at": _timestamp(row["last_login_at"]),
        "session_count": row["session_count"],
        "locked_until": _timestamp(row["locked_until"]),
    }


def user_exists(connection: Connection, user_id: str) -> bool:
    """Return whether an account exists."""
    return bool(connection.execute(text("SELECT EXISTS (SELECT 1 FROM users WHERE id = :id)"), {"id": user_id}).scalar_one())


def get_user(connection: Connection, user_id: str) -> dict | None:
    """Return a user record with its current session and lock summary."""
    row = connection.execute(
        text(_USER_SELECT + "WHERE u.id = :id GROUP BY u.id, l.locked_until"), {"id": user_id}
    ).mappings().one_or_none()
    return _user_payload(row) if row is not None else None


def list_users(connection: Connection) -> list[dict]:
    """List users by email with active-session and lock summaries."""
    rows = connection.execute(
        text(_USER_SELECT + "GROUP BY u.id, l.locked_until ORDER BY u.email ASC")
    ).mappings()
    return [_user_payload(row) for row in rows]


def set_user_role(connection: Connection, user_id: str, role: str) -> dict:
    """Set an account role while protecting the emergency and last administrators."""
    connection.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
    row = connection.execute(
        text("SELECT id, email, role FROM users WHERE id = :id FOR UPDATE"), {"id": user_id}
    ).mappings().one_or_none()
    if row is None:
        raise UserNotFound
    if row["id"] == emergency_user_id:
        raise UserProtected
    if role == "user" and row["role"] == "admin":
        admin_count = connection.execute(
            text("SELECT count(*) FROM users WHERE role = 'admin' AND id <> :emergency_id"),
            {"emergency_id": emergency_user_id},
        ).scalar_one()
        if admin_count <= 1:
            raise LastAdmin
    connection.execute(text("UPDATE users SET role = :role WHERE id = :id"), {"role": role, "id": user_id})
    return get_user(connection, user_id)


def delete_user(connection: Connection, user_id: str) -> None:
    """Delete an account unless it is the emergency or last administrator."""
    connection.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
    row = connection.execute(
        text("SELECT id, email, role FROM users WHERE id = :id FOR UPDATE"), {"id": user_id}
    ).mappings().one_or_none()
    if row is None:
        raise UserNotFound
    if row["id"] == emergency_user_id:
        raise UserProtected
    if row["role"] == "admin":
        admin_count = connection.execute(
            text("SELECT count(*) FROM users WHERE role = 'admin' AND id <> :emergency_id"),
            {"emergency_id": emergency_user_id},
        ).scalar_one()
        if admin_count <= 1:
            raise LastAdmin
    connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


def unlock_user(connection: Connection, user_id: str) -> bool:
    """Clear login lock and failure history for an existing user."""
    email = connection.execute(text("SELECT email FROM users WHERE id = :id"), {"id": user_id}).scalar_one_or_none()
    if email is None:
        return False
    unlock(connection, email)
    return True


def list_user_sessions(connection: Connection, user_id: str, current_session_id: str) -> list[dict]:
    """List active sessions newest first, marking the caller's session."""
    if not user_exists(connection, user_id):
        raise UserNotFound
    rows = connection.execute(
        text(
            "SELECT id, user_agent, created_at, last_seen_at, expires_at FROM sessions "
            "WHERE user_id = :user_id AND expires_at > now() ORDER BY created_at DESC"
        ),
        {"user_id": user_id},
    ).mappings()
    return [
        {
            "id": row["id"],
            "user_agent": row["user_agent"],
            "created_at": _timestamp(row["created_at"]),
            "last_seen_at": _timestamp(row["last_seen_at"]),
            "expires_at": _timestamp(row["expires_at"]),
            "current": row["id"] == current_session_id,
        }
        for row in rows
    ]


def revoke_user_session(connection: Connection, user_id: str, session_id: str) -> None:
    """Revoke one session belonging to the requested user."""
    if not user_exists(connection, user_id):
        raise UserNotFound
    result = connection.execute(
        text("DELETE FROM sessions WHERE id = :session_id AND user_id = :user_id"),
        {"session_id": session_id, "user_id": user_id},
    )
    if result.rowcount == 0:
        raise SessionNotFound


def revoke_user_sessions(connection: Connection, user_id: str) -> None:
    """Revoke all sessions belonging to an existing user."""
    if not user_exists(connection, user_id):
        raise UserNotFound
    connection.execute(text("DELETE FROM sessions WHERE user_id = :user_id"), {"user_id": user_id})


def list_user_directory(connection: Connection) -> list[dict]:
    """List the minimal user directory in email order."""
    rows = connection.execute(
        text("SELECT id, email, display_name FROM users WHERE id <> :emergency_id ORDER BY email ASC"),
        {"emergency_id": emergency_user_id},
    ).mappings()
    return [dict(row) for row in rows]
