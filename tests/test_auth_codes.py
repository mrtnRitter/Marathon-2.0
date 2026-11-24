import json
import re
from uuid import uuid4

from sqlalchemy import text

from marathon.server.config import AuthConfig
from marathon.server.auth.codes import (
    AuthError,
    confirm_code,
    create_admin_code,
    get_allowed_domains,
    hash_code,
    is_locked,
    locked_text,
    rate_text,
    request_code,
    unlock,
)


def _allow_domains(connection, domains):
    connection.execute(
        text(
            "INSERT INTO settings (key, value) VALUES ('auth.allowed_domains', CAST(:domains AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"domains": json.dumps(domains)},
    )


def _code_row(connection, email):
    return connection.execute(
        text("SELECT id, code_hash, expires_at, consumed_at FROM auth_codes WHERE email = :email ORDER BY created_at DESC, id DESC LIMIT 1"),
        {"email": email},
    ).mappings().one()


def test_request_code_allowed_domain(db_engine):
    with db_engine.begin() as connection:
        _allow_domains(connection, ["looks.film"])
        result = request_code(connection, " Max@Looks.Film ", AuthConfig())
        row = _code_row(connection, result.email)
        stored_hashes = connection.execute(text("SELECT code_hash FROM auth_codes WHERE email = :email"), {"email": result.email}).scalars().all()

    assert result.email == "max@looks.film"
    assert result.code is not None and re.fullmatch(r"\d{6}", result.code)
    assert row["code_hash"] == hash_code(result.email, result.code)
    assert result.code not in stored_hashes


def test_request_code_not_allowed_stores_row_without_code(db_engine):
    with db_engine.begin() as connection:
        result = request_code(connection, "max@other.test", AuthConfig())
        count = connection.execute(text("SELECT count(*) FROM auth_codes WHERE email = :email"), {"email": result.email}).scalar_one()

    assert result.code is None
    assert count == 1


def test_request_code_subdomain_not_allowed(db_engine):
    with db_engine.begin() as connection:
        _allow_domains(connection, ["looks.film"])
        result = request_code(connection, "max@berlin.looks.film", AuthConfig())

    assert result.code is None


def test_request_code_existing_user_and_admin_email_allowed(db_engine):
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, display_name, role) VALUES (:id, :email, '', 'user')"),
            {"id": uuid4().hex, "email": "known@other.test"},
        )
        existing = request_code(connection, "known@other.test", AuthConfig())
        admin = request_code(connection, "admin@other.test", AuthConfig(admin_emails=("admin@other.test",)))

    assert re.fullmatch(r"\d{6}", existing.code or "")
    assert re.fullmatch(r"\d{6}", admin.code or "")


def test_request_code_invalid_email(db_engine):
    with db_engine.begin() as connection:
        try:
            request_code(connection, "kein-mail", AuthConfig())
        except AuthError as error:
            assert error.status_code == 422
            assert error.detail == "Bitte eine gültige E-Mail-Adresse eingeben"
        else:
            raise AssertionError("expected AuthError")


def test_new_code_invalidates_old(db_engine):
    with db_engine.begin() as connection:
        _allow_domains(connection, ["looks.film"])
        first = request_code(connection, "max@looks.film", AuthConfig())
        request_code(connection, first.email, AuthConfig())
        outcome = confirm_code(connection, first.email, first.code or "", AuthConfig())

    assert outcome.status_code == 400


def test_rate_limit_same_for_all(db_engine):
    for email, domains in (("allowed@looks.film", ["looks.film"]), ("unknown@other.test", [])):
        with db_engine.begin() as connection:
            _allow_domains(connection, domains)
            results = [request_code(connection, email, AuthConfig()) for _ in range(5)]
            try:
                request_code(connection, email, AuthConfig())
            except AuthError as error:
                assert error.status_code == 429
                assert error.detail == rate_text
            else:
                raise AssertionError("expected rate limit")
        assert len(results) == 5


def test_confirm_creates_user(db_engine):
    with db_engine.begin() as connection:
        _allow_domains(connection, ["looks.film"])
        requested = request_code(connection, "max@looks.film", AuthConfig())
        outcome = confirm_code(connection, requested.email, requested.code or "", AuthConfig())
        repeated = confirm_code(connection, requested.email, requested.code or "", AuthConfig())
        user = connection.execute(text("SELECT role, last_login_at FROM users WHERE email = :email"), {"email": requested.email}).mappings().one()

    assert outcome.status_code == 200
    assert outcome.user is not None and outcome.user["role"] == "user"
    assert user["last_login_at"] is not None
    assert repeated.status_code == 400


def test_confirm_admin_email_creates_admin(db_engine):
    auth = AuthConfig(admin_emails=("admin@other.test",))
    with db_engine.begin() as connection:
        requested = request_code(connection, "admin@other.test", auth)
        outcome = confirm_code(connection, requested.email, requested.code or "", auth)

    assert outcome.status_code == 200
    assert outcome.user is not None and outcome.user["role"] == "admin"


def test_confirm_expired_code(db_engine):
    with db_engine.begin() as connection:
        _allow_domains(connection, ["looks.film"])
        requested = request_code(connection, "max@looks.film", AuthConfig())
        connection.execute(text("UPDATE auth_codes SET expires_at = now() - interval '1 minute' WHERE email = :email"), {"email": requested.email})
        outcome = confirm_code(connection, requested.email, requested.code or "", AuthConfig())

    assert outcome.status_code == 400


def test_three_failures_lock(db_engine):
    with db_engine.begin() as connection:
        _allow_domains(connection, ["looks.film"])
        requested = request_code(connection, "max@looks.film", AuthConfig())
        outcomes = [confirm_code(connection, requested.email, "bad", AuthConfig()) for _ in range(3)]
        lock_window = connection.execute(
            text("SELECT locked_until > now() + interval '23 hours' AND locked_until < now() + interval '25 hours' FROM auth_locks WHERE email = :email"),
            {"email": requested.email},
        ).scalar_one()
        correct = confirm_code(connection, requested.email, requested.code or "", AuthConfig())
        try:
            request_code(connection, requested.email, AuthConfig())
        except AuthError as error:
            request_error = error
        else:
            request_error = None

    assert [outcome.status_code for outcome in outcomes] == [400, 400, 429]
    assert outcomes[-1].detail == locked_text
    assert lock_window is True
    assert correct.status_code == 429
    assert request_error is not None and request_error.status_code == 429 and request_error.detail == locked_text
    with db_engine.connect() as connection:
        assert is_locked(connection, requested.email)


def test_old_failures_not_counted(db_engine):
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO auth_failures (email, failed_at) VALUES ('max@looks.film', now() - interval '25 hours'), ('max@looks.film', now() - interval '25 hours')"))
        outcome = confirm_code(connection, "max@looks.film", "wrong", AuthConfig())
        failure_count = connection.execute(text("SELECT count(*) FROM auth_failures WHERE email = 'max@looks.film' AND failed_at > now() - interval '24 hours'")).scalar_one()

    assert outcome.status_code == 400
    assert failure_count == 1


def test_failure_committed_with_outcome(db_engine):
    with db_engine.begin() as connection:
        outcome = confirm_code(connection, "max@looks.film", "wrong", AuthConfig())
    with db_engine.connect() as connection:
        count = connection.execute(text("SELECT count(*) FROM auth_failures WHERE email = 'max@looks.film'")).scalar_one()

    assert outcome.status_code == 400
    assert count == 1


def test_unlock(db_engine):
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO auth_locks (email, locked_until) VALUES ('max@looks.film', now() + interval '1 day')"))
        connection.execute(text("INSERT INTO auth_failures (email) VALUES ('max@looks.film')"))
        unlock(connection, "max@looks.film")
        result = request_code(connection, "max@looks.film", AuthConfig())

    assert result.email == "max@looks.film"


def test_create_admin_code(db_engine):
    with db_engine.begin() as connection:
        try:
            create_admin_code(connection, "not-admin@other.test", AuthConfig())
        except AuthError as error:
            assert error.status_code == 403
            assert error.detail == "not-admin@other.test ist kein Admin"
        else:
            raise AssertionError("expected AuthError")
        connection.execute(text("INSERT INTO auth_locks (email, locked_until) VALUES ('admin@other.test', now() + interval '1 day')"))
        code = create_admin_code(connection, "admin@other.test", AuthConfig(admin_emails=("admin@other.test",)))
        outcome = confirm_code(connection, "admin@other.test", code, AuthConfig(admin_emails=("admin@other.test",)))
        locked = is_locked(connection, "admin@other.test")

    assert re.fullmatch(r"\d{6}", code)
    assert outcome.status_code == 200
    assert locked is False
