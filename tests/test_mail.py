"""Tests for Marathon SMTP delivery without network access."""

# IMPORTS
import logging
import smtplib
import ssl
import socket
import threading
import time
from email.message import EmailMessage

import pytest

from marathon.server.config import SmtpConfig
from marathon.server import mail


# FUNC
class FakeSMTP:
    def __init__(self, *, timeout, context=None):
        self.created = (timeout, context)
        self.calls = []
        self.error = None
        self.sock = None
        self.__class__.instances.append(self)

    instances = []

    def connect(self, host, port):
        self.calls.append(("connect", host, port))
        return 220, b"ready"

    def starttls(self, *, context):
        self.calls.append(("starttls", context))

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        self.calls.append(("send_message", message))
        if self.error:
            raise self.error

    def quit(self):
        self.calls.append(("quit",))

    def close(self):
        self.calls.append(("close",))


def _factory_with_error(error):
    def factory(*, timeout, **kwargs):
        smtp = FakeSMTP(timeout=timeout, **kwargs)
        smtp.error = error
        return smtp
    return factory


@pytest.fixture
def local_smtp_server():
    servers = []

    def start(*, trickle=False):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.1)
        stop = threading.Event()
        accepted = []

        def serve():
            connection = None
            try:
                while not stop.is_set():
                    try:
                        connection, _ = listener.accept()
                        accepted.append(connection)
                        break
                    except socket.timeout:
                        continue
                if connection is None:
                    return
                connection.settimeout(0.2)
                if trickle:
                    connection.sendall(b"220 ")
                    while not stop.wait(0.1):
                        connection.sendall(b"x")
                else:
                    stop.wait(30)
            except OSError:
                pass
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        def close():
            stop.set()
            for connection in accepted:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    connection.close()
                except OSError:
                    pass
            try:
                listener.close()
            except OSError:
                pass
            thread.join(timeout=2)

        servers.append(close)
        return listener.getsockname()[1], close

    yield start
    for close in servers:
        close()

def mail_settings(**changes):
    values = {
        "host": "smtp.example.test",
        "port": 587,
        "username": "marathon@example.test",
        "encryption": "starttls",
        "from_address": "marathon@example.test",
        "from_name": "Marathon",
    }
    values.update(changes)
    return SmtpConfig(**values)


def test_send_mail_starttls():
    FakeSMTP.instances.clear()
    mail.send_mail(mail_settings(), "to@example.test", "Subject", "Body", password="geheim", smtp_factory=FakeSMTP)
    smtp = FakeSMTP.instances[-1]
    assert smtp.created == (10.0, None)
    assert [call[0] for call in smtp.calls] == [
        "connect", "starttls", "login", "send_message", "quit", "close"
    ]
    assert smtp.calls[0] == ("connect", "smtp.example.test", 587)
    assert smtp.calls[2] == ("login", "marathon@example.test", "geheim")
    message = smtp.calls[3][1]
    assert isinstance(message, EmailMessage)
    assert message["From"] == "Marathon <marathon@example.test>"
    assert message["To"] == "to@example.test"
    assert message["Subject"] == "Subject"


def test_send_mail_without_encryption():
    FakeSMTP.instances.clear()
    mail.send_mail(
        mail_settings(username="", encryption="none"), "to@example.test", "Subject", "Body", smtp_factory=FakeSMTP
    )
    assert [call[0] for call in FakeSMTP.instances[-1].calls] == [
        "connect", "send_message", "quit", "close"
    ]


def test_send_mail_implicit_tls():
    FakeSMTP.instances.clear()
    smtp_calls = []
    ssl_calls = []

    def smtp_factory(**kwargs):
        smtp_calls.append(kwargs)
        return FakeSMTP(**kwargs)

    def ssl_factory(*, timeout, context):
        ssl_calls.append((timeout, context))
        return FakeSMTP(timeout=timeout, context=context)

    mail.send_mail(
        mail_settings(encryption="tls"), "to@example.test", "Subject", "Body",
        password="geheim", smtp_factory=smtp_factory, smtp_ssl_factory=ssl_factory,
    )
    assert smtp_calls == []
    assert FakeSMTP.instances[-1].created[0] == 10.0
    assert len(ssl_calls) == 1
    assert isinstance(ssl_calls[0][1], ssl.SSLContext)
    assert [call[0] for call in FakeSMTP.instances[-1].calls] == [
        "connect", "login", "send_message", "quit", "close"
    ]


def test_send_mail_unknown_encryption():
    factory_calls = []

    def factory(*args, **kwargs):
        factory_calls.append((args, kwargs))
        return FakeSMTP(*args, **kwargs)

    with pytest.raises(mail.MailError, match="Mailversand: Verschlüsselung muss none, starttls oder tls sein"):
        mail.send_mail(
            mail_settings(encryption="ssl"), "to@example.test", "Subject", "Body",
            smtp_factory=factory, smtp_ssl_factory=factory,
        )
    assert factory_calls == []


def test_send_mail_not_configured():
    with pytest.raises(mail.MailError, match="Mailversand ist nicht eingerichtet"):
        mail.send_mail(mail_settings(host=""), "to@example.test", "Subject", "Body", smtp_factory=FakeSMTP)


def test_send_mail_missing_password(tmp_path, monkeypatch):
    monkeypatch.setattr(mail, "read_smtp_password", lambda: "")
    with pytest.raises(mail.MailError, match=r"SMTP-Passwort fehlt \(Einstellungen → Mailversand\)"):
        mail.send_mail(
            mail_settings(), "to@example.test", "Subject", "Body", password=None,
            smtp_factory=FakeSMTP,
        )


def test_send_mail_error_hides_password():
    error = smtplib.SMTPAuthenticationError(535, b"bad geheim")
    with pytest.raises(mail.MailError) as caught:
        mail.send_mail(
            mail_settings(), "to@example.test", "Subject", "Body", password="geheim",
            smtp_factory=_factory_with_error(error),
        )
    assert "geheim" not in str(caught.value)
    assert "[geschützt]" in str(caught.value)


def test_send_mail_timeout():
    with pytest.raises(mail.MailError, match=r"Zeitlimit von 10 s überschritten"):
        mail.send_mail(
            mail_settings(), "to@example.test", "Subject", "Body", password="pw",
            smtp_factory=_factory_with_error(socket.timeout("timed out")),
        )


def test_send_mail_stops_after_timeout_before_socket_exists():
    class DelayedConnectSMTP(FakeSMTP):
        def connect(self, host, port):
            super().connect(host, port)
            time.sleep(0.1)

    FakeSMTP.instances.clear()
    with pytest.raises(mail.MailError, match=r"Zeitlimit von 0\.02 s überschritten"):
        mail.send_mail(
            mail_settings(), "to@example.test", "Subject", "Body", password="pw",
            smtp_factory=DelayedConnectSMTP, timeout_s=0.02,
        )

    assert [call[0] for call in FakeSMTP.instances[-1].calls] == ["connect", "close"]


def test_read_smtp_password_reads_only_file(tmp_path, monkeypatch):
    password_file = tmp_path / "smtp.pass"
    password_file.write_text("pw\n", encoding="utf-8")
    monkeypatch.setattr(mail.server_config, "smtp_password_file", password_file)
    monkeypatch.setenv("MARATHON_SMTP_PASSWORD", "env-pw")
    assert mail.read_smtp_password() == "pw"
    assert mail.read_smtp_password(tmp_path / "missing") == ""


def test_password_env_is_ignored(tmp_path, monkeypatch):
    password_file = tmp_path / "missing-smtp.pass"
    monkeypatch.setattr(mail.server_config, "smtp_password_file", password_file)
    monkeypatch.setenv("MARATHON_SMTP_PASSWORD", "env-pw")
    assert mail.read_smtp_password() == ""
    assert mail.smtp_password_is_set() is False


def test_send_mail_leaves_no_timer_thread():
    mail.send_mail(
        mail_settings(), "to@example.test", "Subject", "Body", password="pw",
        smtp_factory=FakeSMTP,
    )
    assert not any(
        thread.name == "marathon-mail-timeout" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_mail_watchdog_shuts_current_call_socket_only():
    class TrackedSocket:
        def __init__(self):
            self.shutdown_called = threading.Event()

        def shutdown(self, _how):
            self.shutdown_called.set()

    class ReplacingSMTP(FakeSMTP):
        def __init__(self, *, timeout, context=None, plain_socket, tls_socket, on_starttls):
            super().__init__(timeout=timeout, context=context)
            self.plain_socket = plain_socket
            self.tls_socket = tls_socket
            self.on_starttls = on_starttls

        def connect(self, host, port):
            super().connect(host, port)
            self.sock = self.plain_socket

        def starttls(self, *, context):
            super().starttls(context=context)
            self.sock = self.tls_socket
            self.on_starttls()

    first_plain, first_tls = TrackedSocket(), TrackedSocket()
    second_plain, second_tls = TrackedSocket(), TrackedSocket()
    first_tls_ready = threading.Event()
    second_tls_ready = threading.Event()
    release_second = threading.Event()
    results = {}

    def first_starttls():
        first_tls_ready.set()
        if not first_tls.shutdown_called.wait(1):
            raise AssertionError("the active TLS socket was not shut down")
        raise OSError("socket closed")

    def second_starttls():
        second_tls_ready.set()
        if second_tls.shutdown_called.wait(1):
            raise OSError("another SMTP call shut down this socket")
        if not release_second.wait(1):
            raise AssertionError("second SMTP call was not released")

    def factory(plain_socket, tls_socket, on_starttls):
        return lambda **kwargs: ReplacingSMTP(
            **kwargs, plain_socket=plain_socket, tls_socket=tls_socket, on_starttls=on_starttls
        )

    def send(label, smtp_factory, timeout_s):
        try:
            mail.send_mail(
                mail_settings(), "to@example.test", "Subject", "Body", password="pw",
                smtp_factory=smtp_factory, timeout_s=timeout_s,
            )
        except Exception as error:
            results[label] = error
        else:
            results[label] = None

    first = threading.Thread(
        target=send,
        args=("first", factory(first_plain, first_tls, first_starttls), 0.5),
    )
    second = threading.Thread(
        target=send,
        args=("second", factory(second_plain, second_tls, second_starttls), 2.0),
    )
    first.start()
    assert first_tls_ready.wait(1)
    second.start()
    try:
        assert second_tls_ready.wait(1)
        first.join(2)
    finally:
        release_second.set()
        first.join(1)
        second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert isinstance(results["first"], mail.MailError)
    assert str(results["first"]) == "Zeitlimit von 0.5 s überschritten"
    assert results["second"] is None
    assert first_tls.shutdown_called.is_set()
    assert not first_plain.shutdown_called.is_set()
    assert not second_plain.shutdown_called.is_set()
    assert not second_tls.shutdown_called.is_set()


def test_send_mail_total_timeout_with_silent_server(local_smtp_server):
    port, close_server = local_smtp_server()
    before = time.monotonic()
    try:
        with pytest.raises(mail.MailError, match=r"Zeitlimit von 0\.5 s überschritten"):
            mail.send_mail(
                mail_settings(host="127.0.0.1", port=port, username=""),
                "to@example.test", "Subject", "Body", timeout_s=0.5,
            )
    finally:
        close_server()
    assert time.monotonic() - before < 2


def test_send_mail_total_timeout_with_trickling_server(local_smtp_server):
    port, close_server = local_smtp_server(trickle=True)
    caught = []

    def send():
        try:
            mail.send_mail(
                mail_settings(host="127.0.0.1", port=port, username=""),
                "to@example.test", "Subject", "Body", timeout_s=0.5,
            )
        except Exception as error:
            caught.append(error)

    thread = threading.Thread(target=send)
    before = time.monotonic()
    thread.start()
    thread.join(5)
    elapsed = time.monotonic() - before
    if thread.is_alive():
        close_server()
        thread.join(2)
    assert not thread.is_alive()
    assert not any(thread.name == "marathon-mail-timeout" and thread.is_alive() for thread in threading.enumerate())
    assert elapsed < 2
    assert len(caught) == 1
    assert str(caught[0]) == "Zeitlimit von 0.5 s überschritten"


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
        sender.submit(mail_settings(), "to@example.test", "Subject", "Body")
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
        sender.submit(mail_settings(), "to@example.test", "Subject", "Body")
        assert done.wait(1)
        deadline = time.monotonic() + 1
        expected = "Mailversand an to@example.test fehlgeschlagen (MailError)"
        while expected not in caplog.text and time.monotonic() < deadline:
            time.sleep(0.01)
        sender.close()
    assert expected in caplog.text
    assert secret_code not in caplog.text


def test_mail_main_test_command(tmp_path, monkeypatch, capsys):
    from contextlib import nullcontext

    config_file = tmp_path / "marathon.toml"
    config_file.write_text(
        '[smtp]\nhost = "stale.example.test"\nfrom_address = "stale@example.test"\n',
        encoding="utf-8",
    )
    database_smtp = mail_settings(host="database.example.test", from_address="database@example.test")

    class FakeEngine:
        def connect(self):
            return nullcontext(object())

        def dispose(self):
            pass

    monkeypatch.setattr(mail, "read_password", lambda path: "test-db-password", raising=False)
    monkeypatch.setattr(mail, "create_db_engine", lambda database, password: FakeEngine())
    monkeypatch.setattr(mail, "get_smtp_config", lambda connection: database_smtp)
    sent = []
    monkeypatch.setattr(mail, "send_mail", lambda config, address, subject, body: sent.append((config, address, subject, body)))
    assert mail.main(["test", "Max@Example.Test"], config_file=config_file) == 0
    assert capsys.readouterr().out.strip() == "Testmail an max@example.test gesendet"
    assert sent[0][0] == database_smtp
    assert sent[0][1:] == (
        "max@example.test", mail.test_mail_subject, mail.test_mail_body
    )

    def fail(*args, **kwargs):
        raise mail.MailError("SMTP abgelehnt")
    monkeypatch.setattr(mail, "send_mail", fail)
    assert mail.main(["test", "max@example.test"], config_file=config_file) == 1
    assert "Fehler: Testmail nicht gesendet:" in capsys.readouterr().err
    assert mail.main(["test", "keine-adresse"], config_file=config_file) == 2
    assert "Fehler: Bitte eine gültige E-Mail-Adresse eingeben" in capsys.readouterr().err


def test_mail_main_passes_database_password(tmp_path, monkeypatch):
    from contextlib import nullcontext

    config_file = tmp_path / "marathon.toml"
    config_file.write_text('[database]\nport = 5544\n', encoding="utf-8")
    database = mail.load_server_config(config_file).database
    captured = {}

    class FakeEngine:
        def connect(self):
            return nullcontext(object())

        def dispose(self):
            pass

    monkeypatch.setattr(mail, "read_password", lambda path: "postgres-secret", raising=False)
    monkeypatch.setattr(
        mail,
        "create_db_engine",
        lambda config, password: captured.update(config=config, password=password) or FakeEngine(),
    )
    monkeypatch.setattr(mail, "get_smtp_config", lambda connection: mail_settings())
    monkeypatch.setattr(mail, "send_mail", lambda *args, **kwargs: None)

    assert mail.main(["test", "admin@example.test"], config_file=config_file) == 0
    assert captured == {"config": database, "password": "postgres-secret"}
