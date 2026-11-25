"""Application settings endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool, StrictInt
from sqlalchemy.engine import Connection, Engine

from marathon.server.api.deps import get_engine, require_admin
from marathon.server.config import email_error, normalize_email
from marathon.server import mail
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.settings_writes import (
    SettingsValidationError,
    get_settings,
    get_smtp_config,
    save_settings,
    save_smtp_config,
)


class FolderDialogInput(BaseModel):
    """Folder-dialog settings supplied by the client."""

    server_address: str


class ProxyInput(BaseModel):
    """Proxy settings supplied by the client."""

    public_url: str
    builtin: StrictBool
    trusted_proxies: list[str]


class AuthInput(BaseModel):
    """Authentication settings supplied by the client."""

    allowed_domains: list[str]


class SettingsInput(BaseModel):
    """Settings supplied by the client."""

    folder_dialog: FolderDialogInput
    proxy: ProxyInput
    auth: AuthInput


class SmtpInput(BaseModel):
    """SMTP settings supplied by an administrator."""

    host: str
    port: StrictInt
    username: str
    encryption: str
    from_address: str
    from_name: str
    password: str | None = None


class SmtpTestInput(BaseModel):
    """Recipient address for an administrator's test email."""

    address: str


router = APIRouter(tags=["settings"])


@router.get("/settings")
def read_settings(engine: Engine = Depends(get_engine)) -> dict:
    """Return the saved application settings."""
    with engine.connect() as connection:
        return get_settings(connection)


@router.put("/settings")
def put_settings(
    payload: SettingsInput,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Validate and save application settings."""
    try:
        with engine.begin() as connection:
            return save_settings(
                connection,
                payload.folder_dialog.server_address,
                public_url=payload.proxy.public_url,
                builtin=payload.proxy.builtin,
                trusted_proxies=payload.proxy.trusted_proxies,
                allowed_domains=payload.auth.allowed_domains,
            )
    except SettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/settings/smtp")
def read_smtp_settings(
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Return saved SMTP settings without exposing the password."""
    with engine.connect() as connection:
        config = get_smtp_config(connection)
    return {
        "host": config.host,
        "port": config.port,
        "username": config.username,
        "encryption": config.encryption,
        "from_address": config.from_address,
        "from_name": config.from_name,
        "password_set": mail.smtp_password_is_set(),
    }


@router.put("/settings/smtp")
def put_smtp_settings(
    payload: SmtpInput,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Validate and persist SMTP settings and an optional local password."""
    try:
        with engine.begin() as connection:
            config = save_smtp_config(
                connection,
                host=payload.host,
                port=payload.port,
                username=payload.username,
                encryption=payload.encryption,
                from_address=payload.from_address,
                from_name=payload.from_name,
            )
            if payload.username.strip() and not payload.password and not mail.smtp_password_is_set():
                raise SettingsValidationError(
                    "Mailversand: Passwort fehlt für den Benutzernamen"
                )
            if payload.password:
                if "\n" in payload.password or "\r" in payload.password:
                    raise SettingsValidationError(
                        "Mailversand: Passwort darf keinen Zeilenumbruch enthalten"
                    )
                if len(payload.password) > 1024:
                    raise SettingsValidationError(
                        "Mailversand: Passwort ist zu lang (max. 1024 Zeichen)"
                    )
                try:
                    mail.write_smtp_password(payload.password)
                except OSError as error:
                    raise HTTPException(
                        status_code=500,
                        detail="Mailversand: Passwort konnte nicht gespeichert werden",
                    ) from error
    except SettingsValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {
        "host": config.host,
        "port": config.port,
        "username": config.username,
        "encryption": config.encryption,
        "from_address": config.from_address,
        "from_name": config.from_name,
        "password_set": mail.smtp_password_is_set(),
    }


@router.post("/settings/smtp/test")
def send_smtp_test_mail(
    payload: SmtpTestInput,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Send a test email using the saved SMTP settings."""
    try:
        address = normalize_email(payload.address)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=email_error) from error

    with engine.connect() as connection:
        config = get_smtp_config(connection)
    if not config.configured:
        raise HTTPException(status_code=409, detail=mail.not_configured_error)

    try:
        mail.send_mail(config, address, mail.test_mail_subject, mail.test_mail_body)
    except mail.MailError as error:
        raise HTTPException(status_code=502, detail=f"Testmail nicht gesendet: {error}") from error
    return {"detail": f"Testmail an {address} gesendet"}
