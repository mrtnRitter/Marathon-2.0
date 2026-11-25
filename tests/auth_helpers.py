"""Test-only authentication helpers for API requests."""

from uuid import uuid4

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Connection

from marathon.server.api.deps import current_user
from marathon.server.auth.sessions import AuthUser


def sign_in(app: FastAPI, user: AuthUser | None = None) -> AuthUser:
    """Override browser authentication with the supplied or default admin."""
    signed_in_user = user or AuthUser("test-admin", "admin@test.local", "Test Admin", "admin", "test-session")
    app.dependency_overrides[current_user] = lambda: signed_in_user
    return signed_in_user


def create_test_user(connection: Connection, email: str, role: str = "user") -> AuthUser:
    """Insert and return a database user for API tests."""
    normalized_email = email.strip().casefold()
    user_id = f"test-{uuid4().hex}"
    connection.execute(
        text("INSERT INTO users (id, email, display_name, role) VALUES (:id, :email, '', :role)"),
        {"id": user_id, "email": normalized_email, "role": role},
    )
    return AuthUser(user_id, normalized_email, "", role, "test-session")
