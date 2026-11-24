"""Database-backed browser sessions."""

# IMPORTS
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

# CONFIG
app_name = "marathon-auth-sessions"
app_version = "1.0"
cookie_name = "marathon_session"


# TYPES
@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    display_name: str
    role: str
    session_id: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# FUNC
def create_session(connection: Connection, user_id: str, user_agent: str, session_days: int) -> str:
    """Create a session and return its one-time plaintext token."""
    token = secrets.token_urlsafe(32)
    connection.execute(
        text(
            "INSERT INTO sessions (id, user_id, token_hash, expires_at, last_seen_at, user_agent) "
            "VALUES (:id, :user_id, :token_hash, now() + (:session_days * interval '1 day'), now(), :user_agent)"
        ),
        {
            "id": uuid4().hex,
            "user_id": user_id,
            "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "session_days": session_days,
            "user_agent": (user_agent or "")[:300],
        },
    )
    return token


def resolve_session(connection: Connection, token: str, session_days: int) -> tuple[AuthUser, bool] | None:
    """Resolve a valid token and renew sessions below the 80% remaining threshold."""
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = connection.execute(
        text(
            "SELECT s.id AS session_id, s.expires_at, now() AS current_time, "
            "u.id, u.email, u.display_name, u.role "
            "FROM sessions AS s JOIN users AS u ON u.id = s.user_id "
            "WHERE s.token_hash = :token_hash AND s.expires_at > now() "
            "FOR UPDATE OF s"
        ),
        {"token_hash": token_hash},
    ).mappings().one_or_none()
    if row is None:
        return None

    user = AuthUser(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        session_id=row["session_id"],
    )
    threshold = timedelta(days=session_days * 0.8)
    if row["expires_at"] - row["current_time"] >= threshold:
        return user, False

    connection.execute(
        text(
            "UPDATE sessions SET expires_at = now() + (:session_days * interval '1 day'), last_seen_at = now() "
            "WHERE id = :session_id"
        ),
        {"session_days": session_days, "session_id": user.session_id},
    )
    return user, True


def delete_session(connection: Connection, session_id: str) -> None:
    """Delete a session by its database identifier."""
    connection.execute(text("DELETE FROM sessions WHERE id = :session_id"), {"session_id": session_id})
