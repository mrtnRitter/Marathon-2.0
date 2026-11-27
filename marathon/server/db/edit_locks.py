"""Database operations for exclusive workflow and macro editing locks."""

# IMPORTS
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from marathon.server.auth.sessions import AuthUser


# CONFIG
lock_held_prefix = "Wird gerade bearbeitet von "
lock_missing_text = "Bearbeitungssperre fehlt oder wurde aufgehoben. Bitte neu laden."


# TYPES
class LockHeld(Exception):
    """Raised when an active lock belongs to another editing session."""

    def __init__(self, holder_name: str) -> None:
        self.holder_name = holder_name
        super().__init__(lock_held_prefix + holder_name)


class LockMissing(Exception):
    """Raised when a save token no longer identifies the current lock."""

    def __init__(self) -> None:
        super().__init__(lock_missing_text)


# FUNC
def _current_time(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _holder_name(display_name: str | None, email: str) -> str:
    return display_name or email


def heartbeat_s(ttl_s: int) -> int:
    return max(5, ttl_s // 4)


def acquire_lock(
    connection: Connection,
    user: AuthUser,
    object_type: str,
    object_id: str,
    token: str | None,
    ttl_s: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Acquire a new lock or renew the lock identified by the supplied token."""
    current_time = _current_time(now)
    expires_at = current_time + timedelta(seconds=ttl_s)
    new_token = secrets.token_urlsafe(32)

    while True:
        inserted = connection.execute(
            text(
                "INSERT INTO edit_locks (object_type, object_id, user_id, token_hash, expires_at) "
                "VALUES (:object_type, :object_id, :user_id, :token_hash, :expires_at) "
                "ON CONFLICT (object_type, object_id) DO NOTHING"
            ),
            {
                "object_type": object_type,
                "object_id": object_id,
                "user_id": user.id,
                "token_hash": _token_hash(new_token),
                "expires_at": expires_at,
            },
        )
        if inserted.rowcount == 1:
            return {"token": new_token, "expires_at": expires_at, "heartbeat_s": heartbeat_s(ttl_s)}

        row = connection.execute(
            text(
                "SELECT edit_locks.user_id, edit_locks.token_hash, edit_locks.expires_at, "
                "users.display_name, users.email "
                "FROM edit_locks JOIN users ON users.id = edit_locks.user_id "
                "WHERE edit_locks.object_type = :object_type AND edit_locks.object_id = :object_id "
                "FOR UPDATE OF edit_locks"
            ),
            {"object_type": object_type, "object_id": object_id},
        ).mappings().first()
        if row is None:
            continue

        if token and _token_hash(token) == row["token_hash"]:
            connection.execute(
                text(
                    "UPDATE edit_locks SET expires_at = :expires_at "
                    "WHERE object_type = :object_type AND object_id = :object_id"
                ),
                {"expires_at": expires_at, "object_type": object_type, "object_id": object_id},
            )
            return {"token": token, "expires_at": expires_at, "heartbeat_s": heartbeat_s(ttl_s)}

        if row["expires_at"] <= current_time:
            connection.execute(
                text(
                    "UPDATE edit_locks SET user_id = :user_id, token_hash = :token_hash, expires_at = :expires_at "
                    "WHERE object_type = :object_type AND object_id = :object_id"
                ),
                {
                    "user_id": user.id,
                    "token_hash": _token_hash(new_token),
                    "expires_at": expires_at,
                    "object_type": object_type,
                    "object_id": object_id,
                },
            )
            return {"token": new_token, "expires_at": expires_at, "heartbeat_s": heartbeat_s(ttl_s)}

        raise LockHeld(_holder_name(row["display_name"], row["email"]))


def release_lock(connection: Connection, object_type: str, object_id: str, token: str | None) -> None:
    """Release a lock only when the supplied token matches its stored hash."""
    if not token:
        return
    connection.execute(
        text(
            "DELETE FROM edit_locks WHERE object_type = :object_type AND object_id = :object_id "
            "AND token_hash = :token_hash"
        ),
        {"object_type": object_type, "object_id": object_id, "token_hash": _token_hash(token)},
    )


def break_lock(
    connection: Connection,
    object_type: str,
    object_id: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Remove any lock and return its former holder, if one existed."""
    current_time = _current_time(now)
    row = connection.execute(
        text(
            "SELECT edit_locks.user_id, edit_locks.expires_at, users.display_name, users.email "
            "FROM edit_locks JOIN users ON users.id = edit_locks.user_id "
            "WHERE edit_locks.object_type = :object_type AND edit_locks.object_id = :object_id "
            "FOR UPDATE OF edit_locks"
        ),
        {"object_type": object_type, "object_id": object_id},
    ).mappings().first()
    if row is None:
        return None
    connection.execute(
        text("DELETE FROM edit_locks WHERE object_type = :object_type AND object_id = :object_id"),
        {"object_type": object_type, "object_id": object_id},
    )
    return {
        "user_id": row["user_id"],
        "name": _holder_name(row["display_name"], row["email"]),
        "expired": row["expires_at"] <= current_time,
    }


def require_lock(connection: Connection, object_type: str, object_id: str, token: str | None) -> None:
    """Require a token matching the current lock, regardless of its expiry time."""
    if not token:
        raise LockMissing()
    exists = connection.execute(
        text(
            "SELECT 1 FROM edit_locks WHERE object_type = :object_type AND object_id = :object_id "
            "AND token_hash = :token_hash"
        ),
        {"object_type": object_type, "object_id": object_id, "token_hash": _token_hash(token)},
    ).scalar_one_or_none()
    if exists is None:
        raise LockMissing()


def lock_status(
    connection: Connection,
    object_type: str,
    object_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the active lock holder, if any."""
    row = connection.execute(
        text(
            "SELECT edit_locks.user_id, edit_locks.expires_at, users.email, users.display_name "
            "FROM edit_locks JOIN users ON users.id = edit_locks.user_id "
            "WHERE edit_locks.object_type = :object_type AND edit_locks.object_id = :object_id "
            "AND edit_locks.expires_at > :now"
        ),
        {"object_type": object_type, "object_id": object_id, "now": _current_time(now)},
    ).mappings().first()
    if row is None:
        return {"holder": None, "expires_at": None}
    return {
        "holder": {
            "id": row["user_id"],
            "email": row["email"],
            "display_name": _holder_name(row["display_name"], row["email"]),
        },
        "expires_at": row["expires_at"],
    }


def active_foreign_lock(
    connection: Connection,
    object_type: str,
    object_id: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the active lock holder for server-side editing checks."""
    row = connection.execute(
        text(
            "SELECT edit_locks.user_id, users.display_name, users.email "
            "FROM edit_locks JOIN users ON users.id = edit_locks.user_id "
            "WHERE edit_locks.object_type = :object_type AND edit_locks.object_id = :object_id "
            "AND edit_locks.expires_at > :now"
        ),
        {"object_type": object_type, "object_id": object_id, "now": _current_time(now)},
    ).mappings().first()
    if row is None:
        return None
    return {"user_id": row["user_id"], "name": _holder_name(row["display_name"], row["email"])}
