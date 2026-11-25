"""Authentication endpoints."""

# IMPORTS
import logging
from threading import Thread

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from marathon.server.api.deps import current_user, get_engine
from marathon.server.auth.codes import AuthError, neutral_text
from marathon.server.auth.provider import EmailCodeProvider
from marathon.server.auth.sessions import AuthUser, cookie_name, delete_session
from marathon.server.config import AuthConfig, SmtpConfig
from marathon.server.mail import MailSender, login_code_mail

# CONFIG
app_name = "marathon-api-auth"
app_version = "1.0"
mail_not_configured_text = "Anmeldung per E-Mail ist nicht eingerichtet. Bitte wende Dich an Deinen Administrator."
_logger = logging.getLogger("marathon.server.api.auth")
router = APIRouter(tags=["auth"])


# TYPES
class EmailRequest(BaseModel):
    email: str


class ConfirmRequest(BaseModel):
    email: str
    code: str


# FUNC
def _submit_mail(
    sender: MailSender, smtp: SmtpConfig, address: str, subject: str, body: str, should_send: bool
) -> None:
    """Hand submission to a daemon thread so even a slow sender cannot delay HTTP."""
    def submit() -> None:
        try:
            if should_send:
                sender.submit(smtp, address, subject, body)
        except Exception as error:
            _logger.warning("Anmelde-Mail konnte nicht eingereiht werden (%s)", type(error).__name__)

    try:
        Thread(target=submit, name="marathon-mail-submit", daemon=True).start()
    except RuntimeError as error:
        _logger.warning("Anmelde-Mail konnte nicht eingereiht werden (%s)", type(error).__name__)


def _user_payload(user: dict) -> dict[str, str]:
    return {key: user[key] for key in ("id", "email", "display_name", "role")}


# ROUTES
@router.get("/auth/status")
def auth_status(request: Request) -> dict[str, bool]:
    return {"mail_configured": request.app.state.smtp_config.configured}


@router.post("/auth/request-code")
def request_login_code(
    payload: EmailRequest, request: Request, engine: Engine = Depends(get_engine)
) -> dict[str, str]:
    smtp = request.app.state.smtp_config
    if not smtp.configured:
        raise HTTPException(status_code=503, detail=mail_not_configured_text)

    auth: AuthConfig = request.app.state.auth_config
    provider = EmailCodeProvider(auth)
    try:
        with engine.begin() as connection:
            result = provider.start_login(connection, payload.email)
    except AuthError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error

    subject, body = login_code_mail(result.code or "000000", auth.code_ttl_minutes)
    _submit_mail(
        request.app.state.mail_sender,
        smtp,
        result.email,
        subject,
        body,
        result.code is not None,
    )
    return {"detail": neutral_text}


@router.post("/auth/confirm")
def confirm_login(
    payload: ConfirmRequest,
    request: Request,
    response: Response,
    engine: Engine = Depends(get_engine),
) -> dict[str, str]:
    auth: AuthConfig = request.app.state.auth_config
    provider = EmailCodeProvider(auth)
    with engine.begin() as connection:
        outcome, token = provider.complete_login(
            connection, payload.email, payload.code, request.headers.get("user-agent", "")
        )

    if outcome.user is None or token is None:
        raise HTTPException(status_code=outcome.status_code, detail=outcome.detail)

    response.set_cookie(
        key=cookie_name,
        value=token,
        max_age=auth.session_days * 86400,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return _user_payload(outcome.user)


@router.post("/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    engine: Engine = Depends(get_engine),
) -> Response:
    token = request.cookies.get(cookie_name)
    if token:
        provider = EmailCodeProvider(request.app.state.auth_config)
        with engine.begin() as connection:
            resolved = provider.resolve_session(connection, token)
            if resolved is not None:
                delete_session(connection, resolved[0].session_id)
    response.delete_cookie(key=cookie_name, path="/", secure=True, httponly=True, samesite="lax")
    response.status_code = 204
    return response


@router.get("/auth/me")
def get_me(user: AuthUser = Depends(current_user)) -> dict[str, str]:
    return {"id": user.id, "email": user.email, "display_name": user.display_name, "role": user.role}
