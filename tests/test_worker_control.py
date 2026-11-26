"""Integration tests for one-time worker control links and sessions."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.server.app import create_app
from marathon.server.worker_auth import hash_token
from marathon.server.worker_control import create_control_link, exchange_control_token


link_error = "Link ungültig oder abgelaufen. Bitte im Tray-Menü erneut „Worker-Einstellungen öffnen“ wählen."
session_error = "Keine Worker-Sitzung. Bitte im Tray-Menü „Worker-Einstellungen öffnen“ wählen."


def _enroll(client, hostname="Control Worker"):
    response = client.post(
        "/api/v1/worker/enroll",
        json={"hostname": hostname, "platform": "linux", "client_version": "1.2.3"},
    )
    assert response.status_code == 200
    return response.json()


def _set_worker_skills(db_engine, worker_id):
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO module_types (id, name, category, icon, has_input) VALUES ('copy_file', 'Datei kopieren', 'test', 'box', false)")
        )
        connection.execute(
            text("UPDATE workers SET capabilities = '[\"copy_file\", \"unknown_type\"]'::jsonb, skills = '[\"copy_file\", \"unknown_type\"]'::jsonb WHERE id = :worker_id"),
            {"worker_id": worker_id},
        )


def _control_link(client, token):
    return client.post("/api/v1/worker/control-link", headers={"Authorization": f"Bearer {token}"})


def _exchange(client, url):
    from urllib.parse import parse_qs, urlsplit

    token = parse_qs(urlsplit(url).query)["token"][0]
    return client.post("/api/v1/worker-control/exchange", json={"token": token})


def _session_cookie(db_engine, worker_id, expires_at):
    import secrets

    token = secrets.token_urlsafe(32)
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO worker_control_sessions (id, worker_id, token_hash, expires_at) VALUES (:id, :worker_id, :token_hash, :expires_at)"),
            {"id": secrets.token_hex(16), "worker_id": worker_id, "token_hash": hash_token(token), "expires_at": expires_at},
        )
    return token


def test_control_tokens_expire_after_sixty_seconds_and_are_single_use(db_engine):
    app = create_app(engine=db_engine)
    with TestClient(app) as client:
        worker = _enroll(client)
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    with db_engine.begin() as connection:
        token = create_control_link(connection, worker["worker_id"], now)
        first = exchange_control_token(connection, token, now, 12)
        second = exchange_control_token(connection, token, now, 12)
        expired_token = create_control_link(connection, worker["worker_id"], now)
        expired = exchange_control_token(connection, expired_token, now + timedelta(seconds=61), 12)
        expiry = connection.execute(
            text("SELECT expires_at FROM worker_ephemeral_tokens WHERE token_hash = :token_hash"),
            {"token_hash": hash_token(token)},
        ).scalar_one()

    assert first is not None and first[0] == worker["worker_id"]
    assert second is None and expired is None
    assert expiry == now + timedelta(seconds=60)


def test_worker_control_link_requires_worker_token_and_returns_one_time_url(db_engine):
    app = create_app(engine=db_engine, worker_token="server-secret")
    with TestClient(app) as client:
        worker = _enroll(client)
        denied = client.post("/api/v1/worker/control-link")
        allowed = _control_link(client, worker["worker_token"])

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["url"].startswith("http://testserver/worker-control?token=")
    with db_engine.connect() as connection:
        record = connection.execute(text("SELECT token_hash, purpose, expires_at FROM worker_ephemeral_tokens WHERE worker_id = :worker_id"), {"worker_id": worker["worker_id"]}).mappings().one()
    assert record["purpose"] == "control_link"
    assert len(record["token_hash"]) == 64
    assert "/worker-control?token=" in allowed.json()["url"]


def test_exchange_sets_secure_cookie_and_returns_skill_names(db_engine):
    app = create_app(engine=db_engine)
    with TestClient(app, base_url="https://testserver") as client:
        worker = _enroll(client)
        _set_worker_skills(db_engine, worker["worker_id"])
        link = _control_link(client, worker["worker_token"])
        exchange = _exchange(client, link.json()["url"])

    assert exchange.status_code == 200
    assert exchange.json()["approved"] is False
    assert exchange.json()["skills"] == ["copy_file", "unknown_type"]
    assert exchange.json()["skill_names"] == ["Datei kopieren", "unknown_type"]
    cookie = exchange.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert "marathon_worker_control=" in cookie


def test_second_exchange_and_expired_link_return_exact_401(db_engine):
    app = create_app(engine=db_engine)
    with TestClient(app, base_url="https://testserver") as client:
        worker = _enroll(client)
        link = _control_link(client, worker["worker_token"])
        first = _exchange(client, link.json()["url"])
        second = _exchange(client, link.json()["url"])
        expired_link = _control_link(client, worker["worker_token"])
        with db_engine.begin() as connection:
            connection.execute(text("UPDATE worker_ephemeral_tokens SET expires_at = now() - interval '1 second' WHERE worker_id = :worker_id AND consumed_at IS NULL"), {"worker_id": worker["worker_id"]})
        expired = _exchange(client, expired_link.json()["url"])

    assert first.status_code == 200
    assert second.status_code == expired.status_code == 401
    assert second.json() == expired.json() == {"detail": link_error}


def test_control_self_requires_session_and_controls_only_its_worker(db_engine):
    app = create_app(engine=db_engine)
    with TestClient(app, base_url="https://testserver") as client:
        first = _enroll(client, "First")
        second = _enroll(client, "Second")
        no_cookie = client.get("/api/v1/worker-control/self")
        link = _control_link(client, first["worker_token"])
        exchange = _exchange(client, link.json()["url"])
        initial = client.get("/api/v1/worker-control/self")
        with db_engine.begin() as connection:
            connection.execute(text("UPDATE workers SET capabilities = '[\"copy_file\"]'::jsonb WHERE id = :worker_id"), {"worker_id": first["worker_id"]})
        valid_rules = {"blocked_processes": ["app.exe"]}
        saved = client.put("/api/v1/worker-control/self/availability", json=valid_rules)
        invalid = client.put("/api/v1/worker-control/self/availability", json={"invalid": True})
        denied_start = client.post("/api/v1/worker-control/self/start")
        with db_engine.begin() as connection:
            connection.execute(text("UPDATE workers SET approved = true WHERE id = :worker_id"), {"worker_id": first["worker_id"]})
        started = client.post("/api/v1/worker-control/self/start")
        drained = client.post("/api/v1/worker-control/self/stop-drain")
        stopped = client.post("/api/v1/worker-control/self/stop-now")
        assert client.get("/api/v1/worker-control/self").json()["id"] == first["worker_id"]

    assert exchange.status_code == 200
    assert no_cookie.status_code == 401
    assert no_cookie.json() == {"detail": session_error}
    assert initial.json()["id"] == first["worker_id"]
    assert saved.status_code == 200 and saved.json()["availability_revision"] == 1
    assert invalid.status_code == 422
    assert denied_start.status_code == 409
    assert denied_start.json() == {"detail": "Worker wartet auf Freigabe durch einen Administrator"}
    assert started.status_code == 200 and started.json()["status"] == "ready"
    assert drained.status_code == 200 and drained.json()["status"] == "stopped"
    assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
    assert second["worker_id"] != first["worker_id"]


def test_expired_session_and_deleted_worker_return_401(db_engine):
    app = create_app(engine=db_engine)
    with TestClient(app, base_url="https://testserver") as client:
        worker = _enroll(client)
        expired = _session_cookie(db_engine, worker["worker_id"], datetime.now(timezone.utc) - timedelta(seconds=1))
        client.cookies.set("marathon_worker_control", expired, domain="testserver", path="/")
        expired_response = client.get("/api/v1/worker-control/self")
        client.cookies.clear()
        valid = _session_cookie(db_engine, worker["worker_id"], datetime.now(timezone.utc) + timedelta(hours=1))
        client.cookies.set("marathon_worker_control", valid, domain="testserver", path="/")
        with db_engine.begin() as connection:
            connection.execute(text("DELETE FROM workers WHERE id = :worker_id"), {"worker_id": worker["worker_id"]})
        deleted_response = client.get("/api/v1/worker-control/self")

    assert expired_response.status_code == deleted_response.status_code == 401
    assert expired_response.json() == deleted_response.json() == {"detail": session_error}
