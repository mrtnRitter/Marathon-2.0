"""Tests for Marathon SMTP delivery without network access."""

# IMPORTS
import logging
import smtplib
import socket
import threading
import time
from email.message import EmailMessage
from pathlib import Path

import pytest

from marathon.server.config import SmtpConfig
from marathon.server import mail


# FUNC
class FakeSMTP:
    def __init__(self, host, port, *, timeout):
        self.created = (host, port, timeout)
        self.calls = []
        self.error = None
        self.__class__.instances.append(self)

    instances = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, *, context):
        self.calls.append(("starttls", context))

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        self.calls.append(("send_message", message))
        if self.error:
            raise self.error


def smtp_config(**changes):
    values = {
        "host": "smtp.example.test",
        "port": 587,
        "username": "marathon@example.test",
        "use_tls": True,
        "from_address": "marathon@example.test",
        "from_name": "Marathon",
    }
    values.update(changes)
    return SmtpConfig(**values)


def _factory_with_error(error):
    def factory(host, port, *, timeout):
        smtp = FakeSMTP(host, port, timeout=timeout)
        smtp.error = error
        return smtp
    return factory


def test_send_mail_with_starttls_and_login():
    FakeSMTP.instances.clear()
    mail.send_mail(smtp_config(), "to@example.test", "Subject", "Body", password="geheim", smtp_factory=FakeSMTP)
    smtp = FakeSMTP.instances[-1]
    assert smtp.created == ("smtp.example.test", 587, 10.0)
    assert [call[0] for call in smtp.calls] == ["starttls", "login", "send_message"]
    assert smtp.calls[1] == ("login", "marathon@example.test", "geheim")
    message = smtp.calls[2][1]
    assert isinstance(message, EmailMessage)
    assert message["From"] == "Marathon <marathon@example.test>"
    assert message["To"] == "to@example.test"
    assert message["Subject"] == "Subject"


def test_send_mail_without_login_and_tls():
    FakeSMTP.instances.clear()
    mail.send_mail(
        smtp_config(username="", use_tls=False), "to@example.test", "Subject", "Body", smtp_factory=FakeSMTP
    )
    assert [call[0] for call in FakeSMTP.instances[-1].calls] == ["send_message"]


def test_send_mail_not_configured():
    with pytest.raises(mail.MailError, match="Mailversand ist nicht eingerichtet"):
        mail.send_mail(smtp_config(host=""), "to@example.test", "Subject", "Body", smtp_factory=FakeSMTP)


def test_send_mail_missing_password(tmp_path, monkeypatch):
    monkeypatch.setattr(mail, "read_smtp_password", lambda: "")
    with pytest.raises(mail.MailError, match=r"SMTP-Passwort fehlt \(MARATHON_SMTP_PASSWORD oder \.data/smtp.pass\)"):
        mail.send_mail(
            smtp_config(), "to@example.test", "Subject", "Body", password=None,
            smtp_factory=FakeSMTP,
        )


def test_send_mail_error_hides_password():
    error = smtplib.SMTPAuthenticationError(535, b"bad geheim")
    with pytest.raises(mail.MailError) as caught:
        mail.send_mail(
            smtp_config(), "to@example.test", "Subject", "Body", password="geheim",
            smtp_factory=_factory_with_error(error),
        )
    assert "geheim" not in str(caught.value)
    assert "[geschützt]" in str(caught.value)


def test_send_mail_timeout():
    with pytest.raises(mail.MailError, match="TimeoutError|timeout"):
        mail.send_mail(
            smtp_config(), "to@example.test", "Subject", "Body", password="pw",
            smtp_factory=_factory_with_error(socket.timeout("timed out")),
        )


def test_read_smtp_password_prefers_env_then_file(tmp_path):
    password_file = tmp_path / "smtp.pass"
    password_file.write_text("pw\n", encoding="utf-8")
    assert mail.read_smtp_password({mail.password_env: "env-pw"}, password_file) == "env-pw"
    assert mail.read_smtp_password({}, password_file) == "pw"
    assert mail.read_smtp_password({}, tmp_path / "missing") == ""


def test_login_code_mail():
    assert mail.login_code_mail("123456", 15) == (
        "123456 ist Dein Marathon-Code",
        "Dein Anmeldecode für Marathon: 123456\n\n"
        "Der Code gilt 15 Minuten. Falls Du keine Anmeldung angefordert hast, ignoriere diese E-Mail.\n",
    )


def test_mail_sender_submit_returns_immediately():
    started = threading.Event()
    release = threading.Event()

    def blocked_send(*args):
        started.set()
        release.wait(2)

    sender = mail.MailSender(send=blocked_send)
    try:
        before = time.monotonic()
        sender.submit(smtp_config(), "to@example.test", "Subject", "Body")
        elapsed = time.monotonic() - before
        assert elapsed < 0.5
        assert started.wait(1)
        before_close = time.monotonic()
        sender.close()
        assert time.monotonic() - before_close < 1
    finally:
        release.set()
        sender.close()


def test_mail_sender_logs_failure_without_exception_details(caplog):
    done = threading.Event()
    secret_code = "123456"

    def fail(*args):
        try:
            raise mail.MailError(secret_code)
        finally:
            done.set()

    sender = mail.MailSender(send=fail)
    with caplog.at_level(logging.WARNING, logger="marathon.server.mail"):
        sender.submit(smtp_config(), "to@example.test", "Subject", "Body")
        assert done.wait(1)
        deadline = time.monotonic() + 1
        expected = "Mailversand an to@example.test fehlgeschlagen (MailError)"
        while expected not in caplog.text and time.monotonic() < deadline:
            time.sleep(0.01)
        sender.close()
    assert expected in caplog.text
    assert secret_code not in caplog.text


def test_mail_main_test_command(tmp_path, monkeypatch, capsys):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text(
        '[smtp]\nhost = "smtp.example.test"\nfrom_address = "marathon@example.test"\n',
        encoding="utf-8",
    )
    sent = []
    monkeypatch.setattr(mail, "send_mail", lambda config, address, subject, body: sent.append((config, address, subject, body)))
    assert mail.main(["test", "Max@Example.Test"], config_file=config_file) == 0
    assert capsys.readouterr().out.strip() == "Testmail an max@example.test gesendet"
    assert sent[0][1:] == (
        "max@example.test", "Marathon: Testmail", "Der Mailversand von Marathon funktioniert."
    )

    def fail(*args, **kwargs):
        raise mail.MailError("SMTP abgelehnt")
    monkeypatch.setattr(mail, "send_mail", fail)
    assert mail.main(["test", "max@example.test"], config_file=config_file) == 1
    assert "Fehler: Testmail nicht gesendet:" in capsys.readouterr().err
    assert mail.main(["test", "keine-adresse"], config_file=config_file) == 2
    assert "Fehler: Bitte eine gültige E-Mail-Adresse eingeben" in capsys.readouterr().err
