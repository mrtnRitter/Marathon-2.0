"""Pluggable authentication provider interface and email-code implementation."""

# IMPORTS
from typing import Protocol

from sqlalchemy.engine import Connection

from marathon.server.auth.codes import CodeRequest, ConfirmOutcome, confirm_code, request_code
from marathon.server.auth.sessions import AuthUser, create_session, resolve_session
from marathon.server.config import AuthConfig

# CONFIG
app_name = "marathon-auth-provider"
app_version = "1.0"


# TYPES
class AuthProvider(Protocol):
    def resolve_session(self, connection: Connection, token: str) -> tuple[AuthUser, bool] | None: ...

    def start_login(self, connection: Connection, email: str) -> CodeRequest: ...

    def complete_login(
        self, connection: Connection, email: str, code: str, user_agent: str
    ) -> tuple[ConfirmOutcome, str | None]: ...


class EmailCodeProvider:
    """Authenticate users with the configured email-code flow."""

    def __init__(self, auth: AuthConfig):
        self.auth = auth

    def resolve_session(self, connection: Connection, token: str) -> tuple[AuthUser, bool] | None:
        return resolve_session(connection, token, self.auth.session_days)

    def start_login(self, connection: Connection, email: str) -> CodeRequest:
        return request_code(connection, email, self.auth)

    def complete_login(
        self, connection: Connection, email: str, code: str, user_agent: str
    ) -> tuple[ConfirmOutcome, str | None]:
        outcome = confirm_code(connection, email, code, self.auth)
        if outcome.user is None:
            return outcome, None
        token = create_session(connection, outcome.user["id"], user_agent, self.auth.session_days)
        return outcome, token
