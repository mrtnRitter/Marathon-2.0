import json
import re
import threading
import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from marathon.server.api import auth as auth_api

from marathon.server.api.deps import get_engine, require_admin
from marathon.server.app import create_app
from marathon.server.auth.codes import neutral_text
from marathon.server.auth.sessions import cookie_name, create_session
from marathon.server.config import AuthConfig, SmtpConfig
from marathon.server.db.settings_writes import save_smtp_config


class FakeMailSender:
    def __init__(self, delay: float = 0):
        self.delay = delay
        self.submissions = []
        self.started = threading.Event()
        self._lock = threading.Lock()
        self.closed = False

    def submit(self, config, to_address, subject, body):
        with self._lock:
            self.submissions.append((config, to_address, subject, body))
            self.started.set()
        if self.delay:
            time.sleep(self.delay)

    def wait_for_count(self, count: int, timeout: float = 2) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.submissions) >= count:
                    return True
            time.sleep(0.005)
        return False

    def snapshot(self):
        with self._lock:
            return list(self.submissions)

    def close(self):
        self.closed = True


@pytest.fixture
def app_factory(db_engine, tmp_path):
    def make(*, auth=None, smtp=None, sender=None):
        mail_sender = sender or FakeMailSender()
        smtp = smtp or SmtpConfig()
        with db_engine.begin() as connection:
            save_smtp_config(
                connection,
                host=smtp.host,
                port=smtp.port,
                username=smtp.username,
                use_tls=smtp.use_tls,
                from_address=smtp.from_address,
                from_name=smtp.from_name,
            )
        app = create_app(
            static_dir=tmp_path,
            engine=db_engine,
            auth_config=auth or AuthConfig(),
            smtp_config=smtp,
            mail_sender=mail_sender,
        )
        return app, mail_sender

    return make


def _allow_domains(db_engine, domains):
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO settings (key, value) VALUES ('auth.allowed_domains', CAST(:domains AS jsonb)) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"domains": json.dumps(domains)},
        )


def _login(client, sender, email):
    previous_count = len(sender.snapshot())
    requested = client.post("/api/v1/auth/request-code", json={"email": email})
    assert requested.status_code == 200
    assert requested.json() == {"detail": neutral_text}
    assert sender.wait_for_count(previous_count + 1)
    subject = sender.snapshot()[-1][2]
    match = re.fullmatch(r"(\d{6}) ist Dein Marathon-Code", subject)
    assert match is not None
    confirmed = client.post("/api/v1/auth/confirm", json={"email": email, "code": match.group(1)})
    return confirmed


def test_auth_status_reports_mail_configuration(app_factory):
    configured_app, _ = app_factory(smtp=SmtpConfig(host="smtp.test", from_address="auth@test.invalid"))

    with TestClient(configured_app, base_url="https://testserver") as client:
        assert client.get("/api/v1/auth/status").json() == {"mail_configured": True}

    unconfigured_app, _ = app_factory()
    with TestClient(unconfigured_app, base_url="https://testserver") as client:
        assert client.get("/api/v1/auth/status").json() == {"mail_configured": False}


def test_request_code_requires_configured_mail(app_factory):
    app, _ = app_factory()
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/v1/auth/request-code", json={"email": "max@looks.film"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Anmeldung per E-Mail ist nicht eingerichtet. Bitte wende Dich an Deinen Administrator."
    }


def test_request_code_sends_authorized_email_with_neutral_response(db_engine, app_factory):
    _allow_domains(db_engine, ["looks.film"])
    smtp = SmtpConfig(host="smtp.test", from_address="auth@test.invalid")
    app, sender = app_factory(smtp=smtp)

    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/v1/auth/request-code", json={"email": "Max@Looks.Film"})

    assert response.status_code == 200
    assert response.json() == {"detail": neutral_text}
    assert sender.wait_for_count(1)
    config, address, subject, body = sender.snapshot()[0]
    assert config == smtp
    assert address == "max@looks.film"
    assert re.fullmatch(r"\d{6} ist Dein Marathon-Code", subject)
    assert body.startswith("Dein Anmeldecode für Marathon: ")
    assert sender.closed is True


def test_request_code_uses_latest_database_smtp_settings(db_engine, app_factory):
    _allow_domains(db_engine, ["looks.film"])
    old_smtp = SmtpConfig(host="old.smtp.test", from_address="old@test.invalid")
    new_smtp = SmtpConfig(host="new.smtp.test", from_address="new@test.invalid")
    app, sender = app_factory(smtp=old_smtp)
    with db_engine.begin() as connection:
        save_smtp_config(
            connection,
            host=new_smtp.host,
            port=new_smtp.port,
            username=new_smtp.username,
            use_tls=new_smtp.use_tls,
            from_address=new_smtp.from_address,
            from_name=new_smtp.from_name,
        )

    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/v1/auth/request-code", json={"email": "max@looks.film"})

    assert response.status_code == 200
    assert sender.wait_for_count(1)
    assert sender.snapshot()[0][0] == new_smtp


def test_request_code_unknown_address_has_same_response_and_no_mail(db_engine, app_factory):
    app, sender = app_factory(smtp=SmtpConfig(host="smtp.test", from_address="auth@test.invalid"))

    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/v1/auth/request-code", json={"email": "unknown@other.test"})

    assert response.status_code == 200
    assert response.json() == {"detail": neutral_text}
    assert sender.snapshot() == []


def test_request_code_uses_same_database_work_for_known_and_unknown(db_engine, app_factory, monkeypatch):
    _allow_domains(db_engine, ["allowed.test"])
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, display_name, role) VALUES ('known-user', 'known@other.test', '', 'user')")
        )

    statements = []
    thread_starts = []
    original_thread = auth_api.Thread

    class TrackingThread(original_thread):
        def start(self):
            thread_starts.append(self)
            return super().start()

    monkeypatch.setattr(auth_api, "Thread", TrackingThread)
    listener = lambda *args: statements.append(args[2])
    event.listen(db_engine, "after_cursor_execute", listener)
    app, _ = app_factory(smtp=SmtpConfig(host="smtp.test", from_address="auth@test.invalid"))
    statements.clear()
    try:
        with TestClient(app, base_url="https://testserver") as client:
            known = client.post("/api/v1/auth/request-code", json={"email": "known@other.test"})
            known_queries = len(statements)
            known_threads = len(thread_starts)
            statements.clear()
            unknown = client.post("/api/v1/auth/request-code", json={"email": "unknown@other.test"})
            unknown_queries = len(statements)
            unknown_threads = len(thread_starts) - known_threads
    finally:
        event.remove(db_engine, "after_cursor_execute", listener)

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {"detail": neutral_text}
    assert known_queries == unknown_queries
    assert known_threads == unknown_threads == 1


def test_request_response_does_not_wait_for_sender(db_engine, app_factory):
    _allow_domains(db_engine, ["looks.film"])
    sender = FakeMailSender(delay=2)
    app, _ = app_factory(
        smtp=SmtpConfig(host="smtp.test", from_address="auth@test.invalid"),
        sender=sender,
    )
    with TestClient(app, base_url="https://testserver") as client:
        started = time.monotonic()
        response = client.post("/api/v1/auth/request-code", json={"email": "max@looks.film"})
        elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 0.5
    assert sender.started.is_set() is True


def test_confirm_success_sets_secure_cookie_and_me_returns_user(db_engine, app_factory):
    _allow_domains(db_engine, ["looks.film"])
    app, sender = app_factory(smtp=SmtpConfig(host="smtp.test", from_address="auth@test.invalid"))

    with TestClient(app, base_url="https://testserver") as client:
        response = _login(client, sender, "max@looks.film")
        cookie = response.headers["set-cookie"].casefold()
        me = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == "max@looks.film"
    assert response.json()["role"] == "user"
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert "max-age=15552000" in cookie
    assert me.status_code == 200
    assert me.json()["role"] == "user"
    assert set(me.json()) == {"id", "email", "display_name", "role"}


def test_invalid_confirms_commit_lockout_before_error_response(db_engine, app_factory):
    _allow_domains(db_engine, ["looks.film"])
    app, sender = app_factory(smtp=SmtpConfig(host="smtp.test", from_address="auth@test.invalid"))

    with TestClient(app, base_url="https://testserver") as client:
        requested = client.post("/api/v1/auth/request-code", json={"email": "max@looks.film"})
        assert requested.status_code == 200
        assert sender.wait_for_count(1)
        code = re.match(r"(\d{6})", sender.snapshot()[0][2]).group(1)
        wrong_code = "000000" if code != "000000" else "000001"
        responses = [
            client.post("/api/v1/auth/confirm", json={"email": "max@looks.film", "code": wrong_code})
            for _ in range(3)
        ]

    assert [response.status_code for response in responses] == [400, 400, 429]
    assert responses[0].json() == {"detail": "Code ungültig oder abgelaufen"}
    with db_engine.connect() as connection:
        lock = connection.execute(
            text("SELECT locked_until > now() FROM auth_locks WHERE email = 'max@looks.film'")
        ).scalar_one()
    assert lock is True


def test_me_requires_session(app_factory):
    app, _ = app_factory()
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "Nicht angemeldet"}


def test_logout_deletes_session_and_cookie(db_engine, app_factory):
    _allow_domains(db_engine, ["looks.film"])
    app, sender = app_factory(smtp=SmtpConfig(host="smtp.test", from_address="auth@test.invalid"))

    with TestClient(app, base_url="https://testserver") as client:
        login = _login(client, sender, "max@looks.film")
        with db_engine.connect() as connection:
            session_count_before = connection.execute(text("SELECT count(*) FROM sessions")).scalar_one()
        logout = client.post("/api/v1/auth/logout")
        me = client.get("/api/v1/auth/me")

    assert login.status_code == 200
    assert session_count_before == 1
    assert logout.status_code == 204
    assert me.status_code == 401
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 0


def test_require_admin_rejects_user_and_accepts_admin(db_engine):
    test_app = FastAPI()
    test_app.state.auth_config = AuthConfig()
    test_app.dependency_overrides[get_engine] = lambda: db_engine
    test_app.get("/test-admin")(lambda user=Depends(require_admin): {"role": user.role})

    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, display_name, role) VALUES "
                "('test-user', 'user@looks.film', '', 'user'), "
                "('test-admin', 'admin@looks.film', '', 'admin')"
            )
        )
        user_token = create_session(connection, "test-user", "test", 180)
        admin_token = create_session(connection, "test-admin", "test", 180)

    with TestClient(test_app, base_url="https://testserver") as client:
        user_access = client.get("/test-admin", headers={"Cookie": f"{cookie_name}={user_token}"})
        admin_access = client.get("/test-admin", headers={"Cookie": f"{cookie_name}={admin_token}"})

    assert user_access.status_code == 403
    assert user_access.json() == {"detail": "Nur für Admins"}
    assert admin_access.status_code == 200
    assert admin_access.json() == {"role": "admin"}


def test_request_code_settings_read_error_returns_503(db_engine, app_factory, monkeypatch):
    app, _ = app_factory(smtp=SmtpConfig(host="smtp.test", from_address="auth@test.invalid"))

    def fail_read(connection):
        raise RuntimeError("private database detail")

    monkeypatch.setattr(auth_api, "get_smtp_config", fail_read)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/v1/auth/request-code", json={"email": "max@looks.film"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Anmeldung per E-Mail ist nicht eingerichtet. Bitte wende Dich an Deinen Administrator."
    }
    assert "private database detail" not in response.text
