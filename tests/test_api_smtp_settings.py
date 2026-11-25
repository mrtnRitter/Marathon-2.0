"""Database-backed SMTP settings API tests."""

import json
import os
import stat

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from auth_helpers import sign_in
from marathon.server import config as server_config
from marathon.server import mail
from marathon.server.app import create_app
from marathon.server.auth.sessions import AuthUser


smtp_path = "/api/v1/settings/smtp"
default_smtp = {
    "host": "",
    "port": 587,
    "username": "",
    "encryption": "starttls",
    "from_address": "",
    "from_name": "Marathon",
    "password_set": False,
}


@pytest.fixture
def api_app(db_engine, tmp_path, monkeypatch):
    monkeypatch.setattr(server_config, "smtp_password_file", tmp_path / ".data" / "smtp.pass", raising=False)
    app = create_app(engine=db_engine, static_dir=tmp_path)
    sign_in(app)
    return app


def _body(**changes):
    result = {
        "host": "smtp.example.test",
        "port": 587,
        "username": "",
        "encryption": "starttls",
        "from_address": "mailer@example.test",
        "from_name": "Marathon",
        "password": None,
    }
    result.update(changes)
    return result


def _put(client, **changes):
    return client.put(smtp_path, json=_body(**changes))


def test_get_smtp_settings_as_admin_returns_defaults_without_password(api_app):
    with TestClient(api_app) as client:
        response = client.get(smtp_path)

    assert response.status_code == 200
    assert response.json() == default_smtp


def test_put_smtp_settings_as_admin_trims_normalizes_and_hides_password(
    api_app, db_engine, tmp_path, monkeypatch
):
    password_file = tmp_path / ".data" / "smtp.pass"
    monkeypatch.setattr(server_config, "smtp_password_file", password_file, raising=False)

    with TestClient(api_app) as client:
        response = _put(
            client,
            host=" smtp.example.test ",
            username=" mailer ",
            from_address=" Mailer@Example.Test ",
            from_name=" Marathon Mail ",
            password="not-returned-secret",
        )
        fetched = client.get(smtp_path)

    expected = {
        "host": "smtp.example.test",
        "port": 587,
        "username": "mailer",
        "encryption": "starttls",
        "from_address": "mailer@example.test",
        "from_name": "Marathon Mail",
        "password_set": True,
    }
    assert response.status_code == 200
    assert response.json() == expected
    assert fetched.json() == expected
    assert "not-returned-secret" not in response.text
    assert password_file.read_text(encoding="utf-8") == "not-returned-secret"
    if os.name == "posix":
        assert stat.S_IMODE(password_file.stat().st_mode) == 0o600
    with db_engine.connect() as connection:
        stored = connection.execute(
            text("SELECT value FROM settings WHERE key = 'smtp'")
        ).scalar_one()
    assert stored == {key: value for key, value in expected.items() if key != "password_set"}
    assert "not-returned-secret" not in json.dumps(stored)


def test_password_null_or_missing_preserves_password_file(api_app, tmp_path, monkeypatch):
    password_file = tmp_path / ".data" / "smtp.pass"
    password_file.parent.mkdir(parents=True)
    password_file.write_text("existing-secret", encoding="utf-8")
    monkeypatch.setattr(server_config, "smtp_password_file", password_file, raising=False)

    with TestClient(api_app) as client:
        response = _put(client, username="mailer", password=None)
        omitted = _body(username="mailer")
        omitted.pop("password")
        second = client.put(smtp_path, json=omitted)

    assert response.status_code == second.status_code == 200
    assert response.json()["password_set"] is True
    assert second.json()["password_set"] is True
    assert password_file.read_text(encoding="utf-8") == "existing-secret"


def test_password_set_checks_nonempty_file(api_app, tmp_path, monkeypatch):
    password_file = tmp_path / "smtp.pass"
    monkeypatch.setattr(server_config, "smtp_password_file", password_file, raising=False)

    with TestClient(api_app) as client:
        assert client.get(smtp_path).json()["password_set"] is False
        password_file.write_text("  \n", encoding="utf-8")
        assert client.get(smtp_path).json()["password_set"] is False
        password_file.write_text("stored-secret", encoding="utf-8")
        assert client.get(smtp_path).json()["password_set"] is True


def test_username_without_password_is_rejected(api_app):
    with TestClient(api_app) as client:
        before = client.get(smtp_path).json()
        response = _put(client, username="mailer", password=None)
        after = client.get(smtp_path).json()

    assert response.status_code == 422
    assert response.json() == {"detail": "Mailversand: Passwort fehlt für den Benutzernamen"}
    assert after == before == default_smtp


def test_existing_password_file_allows_username_without_new_password(
    api_app, tmp_path, monkeypatch
):
    password_file = tmp_path / ".data" / "smtp.pass"
    password_file.parent.mkdir(parents=True)
    password_file.write_text("stored-secret", encoding="utf-8")
    monkeypatch.setattr(server_config, "smtp_password_file", password_file, raising=False)

    with TestClient(api_app) as client:
        response = _put(client, username="mailer", password=None)

    assert response.status_code == 200
    assert response.json()["username"] == "mailer"
    assert response.json()["password_set"] is True
    assert password_file.read_text(encoding="utf-8") == "stored-secret"


def test_smtp_test_mail_sends_with_saved_settings(api_app, monkeypatch):
    from marathon.server.config import SmtpConfig

    sent = []
    monkeypatch.setattr(mail, "send_mail", lambda *args: sent.append(args))
    expected_config = SmtpConfig(
        "smtp.example.test", 587, "mailer", "starttls", "mailer@example.test", "Marathon"
    )
    with TestClient(api_app) as client:
        saved = _put(client, username="mailer", password="saved-secret")
        response = client.post(
            f"{smtp_path}/test", json={"address": " Admin@Example.Test "}
        )

    assert saved.status_code == 200
    assert response.status_code == 200
    assert response.json() == {"detail": "Testmail an admin@example.test gesendet"}
    assert sent == [(
        expected_config, "admin@example.test", mail.test_mail_subject, mail.test_mail_body
    )]


def test_smtp_test_mail_invalid_address(api_app):
    with TestClient(api_app) as client:
        response = client.post(f"{smtp_path}/test", json={"address": "bad-address"})

    assert response.status_code == 422
    assert response.json() == {"detail": server_config.email_error}


def test_smtp_test_mail_not_configured(api_app):
    with TestClient(api_app) as client:
        response = client.post(
            f"{smtp_path}/test", json={"address": "admin@example.test"}
        )

    assert response.status_code == 409
    assert response.json() == {"detail": mail.not_configured_error}


def test_smtp_test_mail_failure(api_app, monkeypatch):
    def fail(*args):
        raise mail.MailError("SMTPAuthenticationError: bad")

    monkeypatch.setattr(mail, "send_mail", fail)
    with TestClient(api_app) as client:
        saved = _put(client, password="saved-secret")
        response = client.post(
            f"{smtp_path}/test", json={"address": "admin@example.test"}
        )

    assert saved.status_code == 200
    assert response.status_code == 502
    assert response.json() == {
        "detail": "Testmail nicht gesendet: SMTPAuthenticationError: bad"
    }


def test_smtp_test_mail_admin_only(db_engine):
    app = create_app(engine=db_engine)
    sign_in(app, AuthUser("test-user", "user@test.local", "Test User", "user", "user-session"))

    with TestClient(app) as client:
        response = client.post(
            f"{smtp_path}/test", json={"address": "admin@example.test"}
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Nur für Admins"}


def test_smtp_tls_encryption_round_trip(api_app):
    with TestClient(api_app) as client:
        written = _put(client, encryption="tls")
        fetched = client.get(smtp_path)

    assert written.status_code == 200
    assert written.json()["encryption"] == "tls"
    assert fetched.status_code == 200
    assert fetched.json()["encryption"] == "tls"


def test_smtp_rejects_unknown_encryption(api_app):
    with TestClient(api_app) as client:
        response = _put(client, encryption="ssl")

    assert response.status_code == 422
    assert response.json() == {"detail": "Mailversand: Verschlüsselung muss none, starttls oder tls sein"}


@pytest.mark.parametrize(("legacy_value", "expected"), [(False, "none"), (True, "starttls")])
def test_legacy_use_tls_is_mapped(api_app, db_engine, legacy_value, expected):
    legacy_settings = {
        "host": "smtp.example.test",
        "port": 25,
        "username": "",
        "use_tls": legacy_value,
        "from_address": "a@example.test",
        "from_name": "Marathon",
    }
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO settings (key, value) VALUES ('smtp', CAST(:value AS jsonb)) "
                 "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"),
            {"value": json.dumps(legacy_settings)},
        )

    with TestClient(api_app) as client:
        response = client.get(smtp_path)

    assert response.status_code == 200
    assert response.json()["encryption"] == expected


def test_smtp_settings_are_admin_only(db_engine):
    app = create_app(engine=db_engine)
    sign_in(app, AuthUser("test-user", "user@test.local", "Test User", "user", "user-session"))

    with TestClient(app) as client:
        read_response = client.get(smtp_path)
        write_response = _put(client)

    assert read_response.status_code == write_response.status_code == 403
    assert read_response.json() == write_response.json() == {"detail": "Nur für Admins"}


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        ({"host": "smtp host"}, "Mailversand: Server nur als Rechnername oder IP-Adresse, z. B. smtp.office365.com"),
        ({"host": "smtp.example.test:587"}, "Mailversand: Server nur als Rechnername oder IP-Adresse, z. B. smtp.office365.com"),
        ({"port": 0}, "Mailversand: Port muss zwischen 1 und 65535 liegen"),
        ({"port": 65536}, "Mailversand: Port muss zwischen 1 und 65535 liegen"),
        ({"from_address": "not-an-address"}, "Mailversand: Absenderadresse ungültig"),
        ({"from_address": ""}, "Mailversand: Absenderadresse fehlt"),
        ({"password": "line1\nline2"}, "Mailversand: Passwort darf keinen Zeilenumbruch enthalten"),
        ({"password": "p" * 1025}, "Mailversand: Passwort ist zu lang (max. 1024 Zeichen)"),
        ({"from_name": "N" * 101}, "Mailversand: Absendername ist zu lang (max. 100 Zeichen)"),
    ],
)
def test_invalid_smtp_settings_return_exact_validation_error(api_app, changes, detail):
    with TestClient(api_app) as client:
        response = _put(client, **changes)

    assert response.status_code == 422
    assert response.json() == {"detail": detail}


def test_password_replace_failure_preserves_existing_file(api_app, tmp_path, monkeypatch):
    password_file = tmp_path / ".data" / "smtp.pass"
    password_file.parent.mkdir(parents=True)
    password_file.write_text("original-secret", encoding="utf-8")
    monkeypatch.setattr(server_config, "smtp_password_file", password_file, raising=False)

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(mail.os, "replace", fail_replace)
    with TestClient(api_app) as client:
        response = _put(client, password="replacement-secret")

    assert response.status_code == 500
    assert "replacement-secret" not in response.text
    assert password_file.read_text(encoding="utf-8") == "original-secret"
    assert list(password_file.parent.iterdir()) == [password_file]


def test_blank_host_and_sender_are_valid_unconfigured_settings(api_app):
    with TestClient(api_app) as client:
        response = _put(client, host="  ", from_address="  ")

    assert response.status_code == 200
    assert response.json()["host"] == ""
    assert response.json()["from_address"] == ""


def test_sender_address_is_validated_when_host_is_blank(api_app):
    with TestClient(api_app) as client:
        response = _put(client, host="", from_address="invalid-address")

    assert response.status_code == 422
    assert response.json() == {"detail": "Mailversand: Absenderadresse ungültig"}
