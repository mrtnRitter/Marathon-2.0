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
from marathon.server.config import ServerConfig, SmtpConfig
from marathon.server.db.seed import seed_defaults


smtp_path = "/api/v1/settings/smtp"
status_path = "/api/v1/auth/status"
default_smtp = {
    "host": "",
    "port": 587,
    "username": "",
    "use_tls": True,
    "from_address": "",
    "from_name": "Marathon",
    "password_set": False,
}


@pytest.fixture
def api_app(db_engine, tmp_path, monkeypatch):
    monkeypatch.setattr(server_config, "smtp_password_file", tmp_path / ".data" / "smtp.pass", raising=False)
    monkeypatch.delenv("MARATHON_SMTP_PASSWORD", raising=False)
    app = create_app(engine=db_engine, static_dir=tmp_path)
    sign_in(app)
    return app


def _body(**changes):
    result = {
        "host": "smtp.example.test",
        "port": 587,
        "username": "mailer",
        "use_tls": True,
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
        "use_tls": True,
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
        response = _put(client, password=None)
        omitted = _body()
        omitted.pop("password")
        second = client.put(smtp_path, json=omitted)

    assert response.status_code == second.status_code == 200
    assert response.json()["password_set"] is True
    assert second.json()["password_set"] is True
    assert password_file.read_text(encoding="utf-8") == "existing-secret"


def test_password_set_checks_environment_and_nonempty_file(api_app, tmp_path, monkeypatch):
    password_file = tmp_path / "smtp.pass"
    monkeypatch.setattr(server_config, "smtp_password_file", password_file, raising=False)
    monkeypatch.delenv("MARATHON_SMTP_PASSWORD", raising=False)

    with TestClient(api_app) as client:
        assert client.get(smtp_path).json()["password_set"] is False
        password_file.write_text("  \n", encoding="utf-8")
        assert client.get(smtp_path).json()["password_set"] is False
        password_file.write_text("stored-secret", encoding="utf-8")
        assert client.get(smtp_path).json()["password_set"] is True
        monkeypatch.setenv("MARATHON_SMTP_PASSWORD", "env-secret")
        assert client.get(smtp_path).json()["password_set"] is True


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


def test_auth_status_tracks_database_smtp_settings_without_restart(api_app):
    with TestClient(api_app) as client:
        assert client.get(status_path).json() == {"mail_configured": False}
        configured = _put(client, host="smtp.example.test", from_address="mailer@example.test")
        assert configured.status_code == 200
        assert client.get(status_path).json() == {"mail_configured": True}
        cleared = _put(client, host="", from_address="")
        assert cleared.status_code == 200
        assert client.get(status_path).json() == {"mail_configured": False}


def test_seed_smtp_defaults_only_when_setting_is_missing(db_engine):
    first = ServerConfig(smtp=SmtpConfig(host="first.example.test", from_address="first@example.test"))
    second = ServerConfig(smtp=SmtpConfig(host="second.example.test", from_address="second@example.test"))

    with db_engine.begin() as connection:
        seed_defaults(connection, first)
        first_value = connection.execute(text("SELECT value FROM settings WHERE key = 'smtp'")).scalar_one()
        seed_defaults(connection, second)
        second_value = connection.execute(text("SELECT value FROM settings WHERE key = 'smtp'")).scalar_one()

    assert first_value == {
        "host": "first.example.test",
        "port": 587,
        "username": "",
        "use_tls": True,
        "from_address": "first@example.test",
        "from_name": "Marathon",
    }
    assert second_value == first_value


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
        status = client.get(status_path)

    assert response.status_code == 200
    assert response.json()["host"] == ""
    assert response.json()["from_address"] == ""
    assert status.json() == {"mail_configured": False}


def test_sender_address_is_validated_when_host_is_blank(api_app):
    with TestClient(api_app) as client:
        response = _put(client, host="", from_address="invalid-address")

    assert response.status_code == 422
    assert response.json() == {"detail": "Mailversand: Absenderadresse ungültig"}
