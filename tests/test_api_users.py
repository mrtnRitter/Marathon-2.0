"""User-management API tests backed by local PostgreSQL."""

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from auth_helpers import create_test_user, sign_in
from marathon.server.app import create_app
from marathon.server.auth.sessions import cookie_name, create_session
from marathon.server.config import AuthConfig
from records import insert_records


PROTECTED_EMAIL = "protected@test.local"


@pytest.fixture
def admin_client(db_engine):
    app = create_app(engine=db_engine, auth_config=AuthConfig())
    admin = sign_in(app)
    with TestClient(app) as client:
        yield app, client, admin


def _insert_session(connection, user_id, user_agent="test-agent"):
    token = create_session(connection, user_id, user_agent, 180)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session_id = connection.execute(
        text("SELECT id FROM sessions WHERE token_hash = :token_hash"), {"token_hash": token_hash}
    ).scalar_one()
    return session_id, token


def test_list_users_reports_active_sessions_locks_and_sorted_roles(admin_client, db_engine):
    _, client, _ = admin_client
    with db_engine.begin() as connection:
        alpha = create_test_user(connection, "alpha@test.local")
        zeta = create_test_user(connection, "zeta@test.local", "admin")
        create_test_user(connection, "expired@test.local")
        _insert_session(connection, alpha.id)
        expired_session, _ = _insert_session(connection, zeta.id)
        connection.execute(text("UPDATE sessions SET expires_at = now() - interval '1 second' WHERE id = :id"), {"id": expired_session})
        connection.execute(
            text("INSERT INTO auth_locks (email, locked_until) VALUES (:email, now() + interval '1 hour')"),
            {"email": alpha.email},
        )
        connection.execute(
            text("INSERT INTO auth_locks (email, locked_until) VALUES ('expired@test.local', now() - interval '1 hour')")
        )
        connection.execute(
            text("INSERT INTO users (id, email, role) VALUES ('protected-user', :email, 'admin')"),
            {"email": PROTECTED_EMAIL},
        )

    response = client.get("/api/v1/users")

    assert response.status_code == 200
    users = response.json()
    assert [user["email"] for user in users] == sorted(user["email"] for user in users)
    assert set(users[0]) == {
        "id", "email", "display_name", "role", "protected", "created_at", "last_login_at", "session_count", "locked_until"
    }
    alpha_record = next(user for user in users if user["id"] == alpha.id)
    zeta_record = next(user for user in users if user["id"] == zeta.id)
    protected_record = next(user for user in users if user["email"] == PROTECTED_EMAIL)
    expired_record = next(user for user in users if user["email"] == "expired@test.local")
    assert alpha_record["role"] == "user"
    assert alpha_record["session_count"] == 1
    assert alpha_record["locked_until"] is not None
    assert alpha_record["protected"] is False
    assert zeta_record["session_count"] == 0
    assert zeta_record["locked_until"] is None
    assert protected_record["protected"] is False
    assert expired_record["locked_until"] is None
    assert alpha_record["created_at"].endswith("+00:00")
    assert alpha_record["last_login_at"] is None

    changed = client.put(f"/api/v1/users/{alpha.id}", json={"role": "admin"})
    assert changed.status_code == 200
    assert changed.json()["role"] == "admin"
    assert client.put(f"/api/v1/users/{alpha.id}", json={"role": "other"}).status_code == 422
    missing = client.put("/api/v1/users/not-found", json={"role": "admin"})
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Nutzer nicht gefunden"}


def test_last_admin_conflicts_and_self_delete(admin_client, db_engine):
    _, client, _ = admin_client
    with db_engine.begin() as connection:
        only_admin = create_test_user(connection, "last-admin@test.local", "admin")

    last_admin_demotion = client.put(f"/api/v1/users/{only_admin.id}", json={"role": "user"})
    assert last_admin_demotion.status_code == 409
    assert last_admin_demotion.json() == {"detail": "Der letzte Admin kann nicht herabgestuft oder gelöscht werden"}
    last_admin_delete = client.delete(f"/api/v1/users/{only_admin.id}")
    assert last_admin_delete.status_code == 409
    assert last_admin_delete.json() == {"detail": "Der letzte Admin kann nicht herabgestuft oder gelöscht werden"}

    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, display_name, role) VALUES ('test-admin', 'self@test.local', '', 'admin')")
        )
    own_delete = client.delete("/api/v1/users/test-admin")
    assert own_delete.status_code == 409
    assert own_delete.json() == {"detail": "Du kannst Dein eigenes Konto nicht löschen"}


def test_delete_user_removes_sessions_and_clears_workflow_owner(admin_client, db_engine):
    _, client, _ = admin_client
    insert_records(db_engine, __import__("datetime").datetime(2025, 1, 2, tzinfo=__import__("datetime").timezone.utc))
    with db_engine.begin() as connection:
        account = create_test_user(connection, "delete@test.local")
        connection.execute(text("UPDATE workflows SET owner_user_id = :user_id WHERE id = 'wf-a'"), {"user_id": account.id})
        _insert_session(connection, account.id)
        _insert_session(connection, account.id, "second-agent")

    response = client.delete(f"/api/v1/users/{account.id}")

    assert response.status_code == 204
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users WHERE id = :id"), {"id": account.id}).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM sessions WHERE user_id = :id"), {"id": account.id}).scalar_one() == 0
        assert connection.execute(text("SELECT owner_user_id FROM workflows WHERE id = 'wf-a'")).scalar_one() is None
    missing = client.delete(f"/api/v1/users/{account.id}")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Nutzer nicht gefunden"}


def test_session_listing_marks_real_current_cookie_and_revokes_sessions(db_engine):
    with db_engine.begin() as connection:
        admin = create_test_user(connection, "session-admin@test.local", "admin")
        target = create_test_user(connection, "session-target@test.local")
        current_id, token = _insert_session(connection, admin.id, "current-browser")
        other_id, _ = _insert_session(connection, admin.id, "other-browser")
        connection.execute(text("UPDATE sessions SET created_at = now() - interval '1 minute' WHERE id = :id"), {"id": current_id})
        first_target_id, _ = _insert_session(connection, target.id, "target-first")
        second_target_id, _ = _insert_session(connection, target.id, "target-second")
        connection.execute(text("UPDATE sessions SET created_at = now() - interval '1 minute' WHERE id = :id"), {"id": first_target_id})

    app = create_app(engine=db_engine)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get(
            f"/api/v1/users/{admin.id}/sessions",
            headers={"Cookie": f"{cookie_name}={token}"},
        )
        assert response.status_code == 200
        sessions = response.json()
        assert sessions[0]["id"] == other_id
        assert sessions[1]["id"] == current_id
        assert sessions[0]["current"] is False
        assert sessions[1]["current"] is True
        assert sessions[0]["user_agent"] == "other-browser"
        assert all(set(item) == {"id", "user_agent", "created_at", "last_seen_at", "expires_at", "current"} for item in sessions)

        deleted = client.delete(f"/api/v1/users/{target.id}/sessions/{first_target_id}", headers={"Cookie": f"{cookie_name}={token}"})
        assert deleted.status_code == 204
        all_deleted = client.delete(f"/api/v1/users/{target.id}/sessions", headers={"Cookie": f"{cookie_name}={token}"})
        assert all_deleted.status_code == 204
        assert client.get(f"/api/v1/users/{target.id}/sessions", headers={"Cookie": f"{cookie_name}={token}"}).json() == []

        missing_session = client.delete(f"/api/v1/users/{admin.id}/sessions/not-found", headers={"Cookie": f"{cookie_name}={token}"})
        assert missing_session.status_code == 404
        assert missing_session.json() == {"detail": "Sitzung nicht gefunden"}
        missing_user = client.get("/api/v1/users/not-found/sessions", headers={"Cookie": f"{cookie_name}={token}"})
        assert missing_user.status_code == 404
        assert missing_user.json() == {"detail": "Nutzer nicht gefunden"}
        own_deleted = client.delete(f"/api/v1/users/{admin.id}/sessions/{current_id}", headers={"Cookie": f"{cookie_name}={token}"})
        assert own_deleted.status_code == 204


def test_unlock_and_directory_for_signed_in_user(admin_client, db_engine):
    app, client, _ = admin_client
    with db_engine.begin() as connection:
        locked = create_test_user(connection, "locked@test.local")
        create_test_user(connection, "z-directory@test.local")
        create_test_user(connection, "a-directory@test.local")
        connection.execute(
            text("INSERT INTO auth_locks (email, locked_until) VALUES (:email, now() + interval '1 hour')"),
            {"email": locked.email},
        )
        connection.execute(text("INSERT INTO auth_failures (email) VALUES (:email)"), {"email": locked.email})

    assert client.delete(f"/api/v1/users/{locked.id}/lock").status_code == 204
    assert client.delete(f"/api/v1/users/{locked.id}/lock").status_code == 204
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM auth_locks WHERE email = :email"), {"email": locked.email}).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM auth_failures WHERE email = :email"), {"email": locked.email}).scalar_one() == 0
    assert client.delete("/api/v1/users/missing/lock").json() == {"detail": "Nutzer nicht gefunden"}

    user = create_test_user_for_directory(db_engine)
    sign_in(app, user)
    directory = client.get("/api/v1/users/directory")
    assert directory.status_code == 200
    assert [entry["email"] for entry in directory.json()] == sorted(entry["email"] for entry in directory.json())
    assert all(set(entry) == {"id", "email", "display_name"} for entry in directory.json())


def create_test_user_for_directory(db_engine):
    with db_engine.begin() as connection:
        return create_test_user(connection, "directory-user@test.local")


def test_emergency_account_is_protected(admin_client, db_engine):
    _, client, _ = admin_client
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, display_name, role) VALUES ('emergency', '', 'Notfallzugang', 'admin')")
        )
        ordinary = create_test_user(connection, "ordinary@test.local")

    users_response = client.get("/api/v1/users")

    assert users_response.status_code == 200
    users = users_response.json()
    emergency = next(user for user in users if user["id"] == "emergency")
    assert emergency["email"] == ""
    assert emergency["display_name"] == "Notfallzugang"
    assert emergency["role"] == "admin"
    assert emergency["protected"] is True
    assert all(user["protected"] is False for user in users if user["id"] != "emergency")

    changed = client.put("/api/v1/users/emergency", json={"role": "user"})
    assert changed.status_code == 409
    assert changed.json() == {"detail": "Notfallzugang kann nicht geändert oder gelöscht werden"}
    deleted = client.delete("/api/v1/users/emergency")
    assert deleted.status_code == 409
    assert deleted.json() == {"detail": "Notfallzugang kann nicht geändert oder gelöscht werden"}
    assert any(user["id"] == ordinary.id for user in users)


def test_emergency_admin_does_not_count_as_last_admin(admin_client, db_engine):
    _, client, _ = admin_client
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, display_name, role) VALUES ('emergency', '', 'Notfallzugang', 'admin')")
        )
        only_real_admin = create_test_user(connection, "real-admin@test.local", "admin")

    rejected = client.put(f"/api/v1/users/{only_real_admin.id}", json={"role": "user"})
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": "Der letzte Admin kann nicht herabgestuft oder gelöscht werden"}

    with db_engine.begin() as connection:
        second_real_admin = create_test_user(connection, "second-admin@test.local", "admin")
    accepted = client.put(f"/api/v1/users/{only_real_admin.id}", json={"role": "user"})
    assert accepted.status_code == 200
    assert accepted.json()["role"] == "user"
    assert second_real_admin.id != only_real_admin.id


def test_directory_excludes_emergency_account(admin_client, db_engine):
    _, client, _ = admin_client
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, display_name, role) VALUES ('emergency', '', 'Notfallzugang', 'admin')")
        )
        ordinary = create_test_user(connection, "directory-visible@test.local")

    response = client.get("/api/v1/users/directory")

    assert response.status_code == 200
    assert all(entry["id"] != "emergency" for entry in response.json())
    assert any(entry["id"] == ordinary.id for entry in response.json())


def test_emergency_lock_can_be_lifted(admin_client, db_engine):
    _, client, _ = admin_client
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, display_name, role) VALUES ('emergency', '', 'Notfallzugang', 'admin')")
        )
        connection.execute(
            text("INSERT INTO auth_locks (email, locked_until) VALUES ('', now() + interval '1 hour')")
        )
        connection.execute(text("INSERT INTO auth_failures (email) VALUES ('')"))

    response = client.delete("/api/v1/users/emergency/lock")

    assert response.status_code == 204
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM auth_locks WHERE email = ''")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM auth_failures WHERE email = ''")).scalar_one() == 0

