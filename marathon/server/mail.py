"""SMTP email delivery for Marathon."""

# IMPORTS
import argparse
import logging
import os
import smtplib
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from threading import Lock
from typing import Mapping

from marathon.server.config import (
    ConfigError,
    SmtpConfig,
    data_dir,
    default_config_file,
    load_server_config,
    normalize_email,
)

# CONFIG
app_name = "marathon-mail"
app_version = "2.0.0"
smtp_timeout_s = 10.0
password_env = "MARATHON_SMTP_PASSWORD"
password_file = data_dir / "smtp.pass"
not_configured_error = "Mailversand ist nicht eingerichtet ([smtp] host und from_address in config/marathon.toml)"


# FUNC
class MailError(Exception):
    """An SMTP delivery failure safe to show without exposing credentials."""


def read_smtp_password(env: Mapping[str, str] = os.environ, file: Path = password_file) -> str:
    """Read the SMTP password from the environment first, then the local password file."""
    value = env.get(password_env)
    if value is not None:
        return value
    try:
        return file.read_text(encoding="utf-8").rstrip()
    except FileNotFoundError:
        return ""


def send_mail(
    config: SmtpConfig,
    to_address: str,
    subject: str,
    body: str,
    *,
    password: str | None = None,
    smtp_factory=smtplib.SMTP,
    timeout_s: float = smtp_timeout_s,
) -> None:
    """Send a plain-text email using the configured SMTP server."""
    if not config.configured:
        raise MailError(not_configured_error)
    if config.username and password is None:
        password = read_smtp_password()
    if config.username and not password:
        raise MailError("SMTP-Passwort fehlt (MARATHON_SMTP_PASSWORD oder .data/smtp.pass)")

    message = EmailMessage()
    message["From"] = formataddr((config.from_name, config.from_address))
    message["To"] = to_address
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    message.set_content(body, subtype="plain", charset="utf-8")

    try:
        with smtp_factory(config.host, config.port, timeout=timeout_s) as smtp:
            if config.use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if config.username:
                smtp.login(config.username, password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as error:
        detail = str(error).splitlines()[0]
        if password:
            detail = detail.replace(password, "[geschützt]")
        raise MailError(f"{type(error).__name__}: {detail}") from error


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
        send_mail(config.smtp, address, "Marathon: Testmail", "Der Mailversand von Marathon funktioniert.")
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
