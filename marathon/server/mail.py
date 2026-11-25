"""SMTP email delivery for Marathon."""

# IMPORTS
import argparse
import logging
import os
import smtplib
import socket
import ssl
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from threading import Event, Lock, Timer

from marathon.server import config as server_config
from marathon.server.config import (
    ConfigError,
    SmtpConfig,
    default_config_file,
    load_server_config,
    normalize_email,
    pg_password_file,
)
from marathon.server.db.engine import create_db_engine, read_password
from marathon.server.db.settings_writes import get_smtp_config

# CONFIG
app_name = "marathon-mail"
app_version = "2.0.0"
smtp_timeout_s = 10.0
not_configured_error = "Mailversand ist nicht eingerichtet (Einstellungen → Mailversand)"
test_mail_subject = "Marathon: Testmail"
test_mail_body = "Der Mailversand von Marathon funktioniert."


# FUNC
class MailError(Exception):
    """An SMTP delivery failure safe to show without exposing credentials."""


def read_smtp_password(file: Path | None = None) -> str:
    """Read the SMTP password from the local password file."""
    password_file = server_config.smtp_password_file if file is None else file
    try:
        return password_file.read_text(encoding="utf-8").rstrip()
    except FileNotFoundError:
        return ""


def smtp_password_is_set() -> bool:
    """Return whether the local password file contains a non-empty password."""
    try:
        return bool(server_config.smtp_password_file.read_text(encoding="utf-8").rstrip())
    except OSError:
        return False


def write_smtp_password(password: str) -> None:
    """Atomically replace the local password file, removing temporary files on failure."""
    password_file = server_config.smtp_password_file
    password_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=password_file.parent,
            prefix=f".{password_file.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            if os.name == "posix":
                os.fchmod(temporary_file.fileno(), 0o600)
            temporary_file.write(password)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, password_file)
    except BaseException:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass
        raise


def send_mail(
    config: SmtpConfig,
    to_address: str,
    subject: str,
    body: str,
    *,
    password: str | None = None,
    smtp_factory=smtplib.SMTP,
    smtp_ssl_factory=smtplib.SMTP_SSL,
    timeout_s: float = smtp_timeout_s,
) -> None:
    """Send a plain-text email using the configured SMTP server."""
    if config.encryption not in server_config.smtp_encryptions:
        raise MailError("Mailversand: Verschlüsselung muss none, starttls oder tls sein")
    if not config.configured:
        raise MailError(not_configured_error)
    if config.username and password is None:
        password = read_smtp_password()
    if config.username and not password:
        raise MailError("SMTP-Passwort fehlt (Einstellungen → Mailversand)")

    message = EmailMessage()
    message["From"] = formataddr((config.from_name, config.from_address))
    message["To"] = to_address
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    message.set_content(body, subtype="plain", charset="utf-8")

    started = time.monotonic()
    smtp = None
    timer = None
    timed_out = Event()
    timeout_message = f"Zeitlimit von {timeout_s:g} s überschritten"
    try:
        factory = smtp_ssl_factory if config.encryption == "tls" else smtp_factory
        factory_kwargs = {"timeout": timeout_s}
        if config.encryption == "tls":
            factory_kwargs["context"] = ssl.create_default_context()
        smtp = factory(**factory_kwargs)
        remaining = timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            raise MailError(timeout_message)

        def stop_smtp() -> None:
            timed_out.set()
            current_socket = getattr(smtp, "sock", None)
            if current_socket is not None:
                try:
                    current_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        timer = Timer(remaining, stop_smtp)
        timer.name = "marathon-mail-timeout"
        timer.daemon = True
        timer.start()

        smtp.connect(config.host, config.port)
        if timed_out.is_set():
            raise MailError(timeout_message)
        if config.encryption == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if config.username:
            smtp.login(config.username, password)
        smtp.send_message(message)
        smtp.quit()
        if timed_out.is_set():
            raise MailError(timeout_message)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as error:
        if timed_out.is_set() or isinstance(error, (socket.timeout, TimeoutError)):
            raise MailError(timeout_message) from error
        detail = str(error).splitlines()[0]
        if password:
            detail = detail.replace(password, "[geschützt]")
        raise MailError(f"{type(error).__name__}: {detail}") from error
    finally:
        if timer is not None:
            timer.cancel()
            timer.join(timeout=1)
        if smtp is not None:
            smtp.close()


def login_code_mail(code: str, ttl_minutes: int) -> tuple[str, str]:
    """Build the subject and plain-text body for a sign-in code email."""
    return (
        f"{code} ist Dein Marathon-Code",
        f"Dein Anmeldecode für Marathon: {code}\n\n"
        f"Der Code gilt {ttl_minutes} Minuten. Falls Du keine Anmeldung angefordert hast, ignoriere diese E-Mail.\n",
    )


class MailSender:
    """Submit email delivery to a small background pool and never wait on shutdown."""

    def __init__(self, max_workers: int = 2, send=send_mail):
        self._send = send
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="marathon-mail")
        self._lock = Lock()
        self._closed = False
        self._logger = logging.getLogger("marathon.server.mail")

    def submit(self, config: SmtpConfig, to_address: str, subject: str, body: str) -> None:
        """Queue a message without waiting for SMTP, logging delivery failures."""
        def deliver() -> None:
            try:
                self._send(config, to_address, subject, body)
            except Exception as error:
                self._logger.warning("Mailversand an %s fehlgeschlagen (%s)", to_address, type(error).__name__)

        with self._lock:
            if self._closed:
                return
            try:
                self._executor.submit(deliver)
            except RuntimeError as error:
                self._logger.warning("Mailversand an %s fehlgeschlagen (%s)", to_address, type(error).__name__)

    def close(self) -> None:
        """Stop accepting messages and cancel queued work without waiting for active sends."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)


# MAIN
def main(argv: list[str] | None = None, config_file: Path = default_config_file) -> int:
    parser = argparse.ArgumentParser(description="Testet den Mailversand von Marathon.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    test_parser = subparsers.add_parser("test", help="Eine Testmail senden")
    test_parser.add_argument("address")
    args = parser.parse_args(argv)

    try:
        address = normalize_email(args.address)
    except ValueError:
        print("Fehler: Bitte eine gültige E-Mail-Adresse eingeben", file=sys.stderr)
        return 2
    try:
        config = load_server_config(config_file)
    except ConfigError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    try:
        engine = create_db_engine(config.database, read_password(pg_password_file))
        try:
            with engine.connect() as connection:
                smtp = get_smtp_config(connection)
        finally:
            engine.dispose()
    except Exception:
        print("Fehler: SMTP-Einstellungen konnten nicht aus der Datenbank gelesen werden", file=sys.stderr)
        return 1
    try:
        send_mail(smtp, address, test_mail_subject, test_mail_body)
    except MailError as error:
        if str(error) == not_configured_error:
            print(f"Fehler: {error}", file=sys.stderr)
        else:
            print(f"Fehler: Testmail nicht gesendet: {error}", file=sys.stderr)
        return 1
    print(f"Testmail an {address} gesendet")
    return 0


# EXEC
if __name__ == "__main__":
    sys.exit(main())
