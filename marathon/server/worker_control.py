"""One-time links and browser sessions for controlling a single worker."""

import secrets
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from marathon.server.worker_auth import hash_token


control_cookie = "marathon_worker_control"
control_link_ttl_s = 60


def create_control_link(connection: Connection, worker_id: str, now: datetime) -> str:
    """Create a hashed, short-lived one-time control token for a worker."""
    cutoff = now - timedelta(days=1)
    connection.execute(
        text("DELETE FROM worker_ephemeral_tokens WHERE expires_at < :cutoff"), {"cutoff": cutoff}
    )
    connection.execute(
        text("DELETE FROM worker_control_sessions WHERE expires_at < :cutoff"), {"cutoff": cutoff}
    )

    token = secrets.token_urlsafe(32)
    connection.execute(
        text(
            "INSERT INTO worker_ephemeral_tokens (token_hash, worker_id, purpose, expires_at) "
            "VALUES (:token_hash, :worker_id, 'control_link', :expires_at)"
        ),
        {
            "token_hash": hash_token(token),
            "worker_id": worker_id,
            "expires_at": now + timedelta(seconds=control_link_ttl_s),
        },
    )
    return token


def exchange_control_token(
    connection: Connection, token: str, now: datetime, session_hours: int
) -> tuple[str, str] | None:
    """Consume a control link once and create a hashed worker-bound session."""
    worker_id = connection.execute(
        text(
            "UPDATE worker_ephemeral_tokens SET consumed_at = :now "
            "WHERE token_hash = :token_hash AND purpose = 'control_link' "
            "AND consumed_at IS NULL AND expires_at > :now RETURNING worker_id"
        ),
        {"now": now, "token_hash": hash_token(token)},
    ).scalar_one_or_none()
    if worker_id is None:
        return None

    session_token = secrets.token_urlsafe(32)
    connection.execute(
        text(
            "INSERT INTO worker_control_sessions (id, worker_id, token_hash, expires_at) "
            "VALUES (:id, :worker_id, :token_hash, :expires_at)"
        ),
        {
            "id": secrets.token_hex(16),
            "worker_id": worker_id,
            "token_hash": hash_token(session_token),
            "expires_at": now + timedelta(hours=session_hours),
        },
    )
    return worker_id, session_token


def resolve_control_session(connection: Connection, session_token: str, now: datetime) -> str | None:
    """Return the worker owning a live control session, if any."""
    return connection.execute(
        text(
            "SELECT worker_id FROM worker_control_sessions "
            "WHERE token_hash = :token_hash AND expires_at > :now"
        ),
        {"token_hash": hash_token(session_token), "now": now},
    ).scalar_one_or_none()
