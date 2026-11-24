"""Database-backed email authentication codes and lockout handling."""

# IMPORTS
import hashlib
import re
import secrets
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from marathon.server.config import AuthConfig, email_error, normalize_email


# CONFIG
app_name = "marathon-auth-codes"
app_version = "1.0"
neutral_text = "Falls die Adresse für Marathon freigegeben ist, ist eine E-Mail mit Code unterwegs. Keine E-Mail erhalten? Adresse prüfen oder an den Administrator wenden."
locked_text = "Zu viele falsche Codes. Die Anmeldung für diese Adresse ist vorübergehend gesperrt."
rate_text = "Zu viele Anfragen. Bitte in ein paar Minuten erneut versuchen."
invalid_code_text = "Code ungültig oder abgelaufen"
lock_failures = 3
lock_hours = 24
request_limit = 5
request_window_minutes = 15
_six_digit_code = re.compile(r"^\d{6}$")


# TYPES
class AuthError(Exception):
    """Authentication error that can be translated to an HTTP response."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class CodeRequest:
    email: str
    code: str | None


@dataclass(frozen=True)
class ConfirmOutcome:
    user: dict | None
    status_code: int
    detail: str


# FUNC

def hash_code(email: str, code: str) -> str:
    """Return a SHA-256 digest bound to the normalized email address."""
    return hashlib.sha256(f"{email}:{code}".encode("utf-8")).hexdigest()


def get_allowed_domains(connection: Connection) -> tuple[str, ...]:
    """Return configured allowed email domains, or an empty tuple if unset."""
    domains = connection.execute(
        text("SELECT value FROM settings WHERE key = 'auth.allowed_domains'")
    ).scalar_one_or_none()
    if not isinstance(domains, list):
        return ()
    return tuple(domain for domain in domains if isinstance(domain, str))


def is_locked(connection: Connection, email: str) -> bool:
    """Check whether the email has an unexpired database lock."""
    return connection.execute(
        text("SELECT EXISTS (SELECT 1 FROM auth_locks WHERE email = :email AND locked_until > now())"),
        {"email": email},
    ).scalar_one()


def _new_code(connection: Connection, email: str, auth: AuthConfig) -> str:
    code = f"{secrets.randbelow(10**6):06d}"
    connection.execute(
        text("UPDATE auth_codes SET consumed_at = now() WHERE email = :email AND consumed_at IS NULL"),
        {"email": email},
    )
    connection.execute(
        text(
            "INSERT INTO auth_codes (id, email, code_hash, expires_at) "
            "VALUES (:id, :email, :code_hash, now() + (:ttl * interval '1 minute'))"
        ),
        {"id": uuid4().hex, "email": email, "code_hash": hash_code(email, code), "ttl": auth.code_ttl_minutes},
    )
    return code


def request_code(connection: Connection, email: str, auth: AuthConfig) -> CodeRequest:
    """Store a code request and reveal the code only for allowed addresses."""
    try:
        normalized_email = normalize_email(email)
    except ValueError as error:
        raise AuthError(422, email_error) from error

    if is_locked(connection, normalized_email):
        raise AuthError(429, locked_text)
    recent_requests = connection.execute(
        text(
            "SELECT count(*) FROM auth_codes WHERE email = :email "
            "AND created_at > now() - (:window * interval '1 minute')"
        ),
        {"email": normalized_email, "window": request_window_minutes},
    ).scalar_one()
    if recent_requests >= request_limit:
        raise AuthError(429, rate_text)

    domain = normalized_email.rsplit("@", 1)[1]
    allowed_domains = get_allowed_domains(connection)
    existing_user = connection.execute(
        text("SELECT EXISTS (SELECT 1 FROM users WHERE email = :email)"), {"email": normalized_email}
    ).scalar_one()
    domain_allowed = domain in allowed_domains
    admin_allowed = normalized_email in auth.admin_emails
    allowed = bool(existing_user | domain_allowed | admin_allowed)
    code = _new_code(connection, normalized_email, auth)
    return CodeRequest(normalized_email, code if allowed else None)


def confirm_code(connection: Connection, email: str, code: str, auth: AuthConfig) -> ConfirmOutcome:
    """Consume a valid code, creating/updating its user or recording failure."""
    try:
        normalized_email = normalize_email(email)
    except ValueError:
        return ConfirmOutcome(None, 400, invalid_code_text)

    if is_locked(connection, normalized_email):
        return ConfirmOutcome(None, 429, locked_text)

    matched = None
    if isinstance(code, str) and _six_digit_code.fullmatch(code):
        matched = connection.execute(
            text(
                "UPDATE auth_codes SET consumed_at = now() WHERE email = :email "
                "AND code_hash = :code_hash AND consumed_at IS NULL AND expires_at > now() "
                "RETURNING id"
            ),
            {"email": normalized_email, "code_hash": hash_code(normalized_email, code)},
        ).scalar_one_or_none()

    if matched is None:
        connection.execute(text("INSERT INTO auth_failures (email) VALUES (:email)"), {"email": normalized_email})
        failures = connection.execute(
            text(
                "SELECT count(*) FROM auth_failures WHERE email = :email "
                "AND failed_at > now() - (:hours * interval '1 hour')"
            ),
            {"email": normalized_email, "hours": lock_hours},
        ).scalar_one()
        if failures >= lock_failures:
            connection.execute(
                text(
                    "INSERT INTO auth_locks (email, locked_until) "
                    "VALUES (:email, now() + (:hours * interval '1 hour')) "
                    "ON CONFLICT (email) DO UPDATE SET locked_until = EXCLUDED.locked_until"
                ),
                {"email": normalized_email, "hours": lock_hours},
            )
            connection.execute(text("DELETE FROM auth_failures WHERE email = :email"), {"email": normalized_email})
            return ConfirmOutcome(None, 429, locked_text)
        return ConfirmOutcome(None, 400, invalid_code_text)

    connection.execute(text("DELETE FROM auth_failures WHERE email = :email"), {"email": normalized_email})
    is_admin = normalized_email in auth.admin_emails
    user = connection.execute(
        text(
            "INSERT INTO users (id, email, display_name, role, last_login_at) "
            "VALUES (:id, :email, '', :role, now()) "
            "ON CONFLICT (email) DO UPDATE SET last_login_at = now() "
            "RETURNING id, email, display_name, role"
        ),
        {
            "id": uuid4().hex,
            "email": normalized_email,
            "role": "admin" if is_admin else "user",
        },
    ).mappings().one()
    return ConfirmOutcome(dict(user), 200, "")


def unlock(connection: Connection, email: str) -> None:
    """Remove lock and failed-attempt history for an email address."""
    connection.execute(text("DELETE FROM auth_locks WHERE email = :email"), {"email": email})
    connection.execute(text("DELETE FROM auth_failures WHERE email = :email"), {"email": email})


def create_admin_code(connection: Connection, email: str, auth: AuthConfig) -> str:
    """Create a fresh code for an administrator, clearing their lock first."""
    try:
        normalized_email = normalize_email(email)
    except ValueError as error:
        raise AuthError(422, email_error) from error
    is_admin = connection.execute(
        text("SELECT EXISTS (SELECT 1 FROM users WHERE email = :email AND role = 'admin')"),
        {"email": normalized_email},
    ).scalar_one() or normalized_email in auth.admin_emails
    if not is_admin:
        raise AuthError(403, f"{normalized_email} ist kein Admin")
    unlock(connection, normalized_email)
    return _new_code(connection, normalized_email, auth)
