"""Dependencies shared by API routes."""

from threading import Lock

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy.engine import Engine

from marathon.server.auth.provider import EmailCodeProvider
from marathon.server.auth.sessions import AuthUser, cookie_name
from marathon.server.config import load_server_config
from marathon.server.db.engine import create_db_engine


_engine_lock = Lock()


def app_engine(app: FastAPI) -> Engine:
    """Return the app's database engine, creating it lazily when needed."""
    engine = app.state.engine
    if engine is not None:
        return engine
    with _engine_lock:
        engine = app.state.engine
        if engine is None:
            config = load_server_config()
            engine = create_db_engine(config.database)
            app.state.engine = engine
            app.state.engine_owned = True
    return engine


def get_engine(request: Request) -> Engine:
    """Return the engine for the request's application."""
    return app_engine(request.app)


def current_user(
    request: Request, response: Response, engine: Engine = Depends(get_engine)
) -> AuthUser:
    """Resolve the browser session or reject unauthenticated requests."""
    token = request.cookies.get(cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")

    auth = request.app.state.auth_config
    with engine.begin() as connection:
        resolved = EmailCodeProvider(auth).resolve_session(connection, token)
    if resolved is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")

    user, renewed = resolved
    if renewed:
        response.set_cookie(
            key=cookie_name,
            value=token,
            max_age=auth.session_days * 86400,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
    return user


def require_admin(user: AuthUser = Depends(current_user)) -> AuthUser:
    """Require an administrator account."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Nur für Admins")
    return user
