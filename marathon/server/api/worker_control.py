"""Worker-specific browser control endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.engine import Connection, Engine

from marathon.server.api.deps import get_engine
from marathon.server.db.queries import get_worker, list_module_types
from marathon.server.db.worker_writes import (
    WorkerConflict,
    WorkerValidationError,
    drain_worker,
    start_worker,
    stop_worker_now,
    update_availability,
)
from marathon.server.worker_auth import require_worker
from marathon.server.worker_control import (
    control_cookie,
    create_control_link,
    exchange_control_token,
    resolve_control_session,
)


link_router = APIRouter(prefix="/worker", tags=["worker-control"])
router = APIRouter(prefix="/worker-control", tags=["worker-control"])


class ControlTokenInput(BaseModel):
    """One-time token submitted by the control page."""

    token: str


link_error = "Link ungültig oder abgelaufen. Bitte im Tray-Menü erneut „Worker-Einstellungen öffnen“ wählen."
session_error = "Keine Worker-Sitzung. Bitte im Tray-Menü „Worker-Einstellungen öffnen“ wählen."


def current_control_worker(request: Request, engine: Engine = Depends(get_engine)) -> str:
    """Resolve the cookie session and confirm its worker still exists."""
    token = request.cookies.get(control_cookie)
    if not token:
        raise HTTPException(status_code=401, detail=session_error)

    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        worker_id = resolve_control_session(connection, token, now)
        if worker_id is None or get_worker(connection, worker_id) is None:
            raise HTTPException(status_code=401, detail=session_error)
    return worker_id


def _worker_response(connection: Connection, worker_id: str) -> dict:
    """Return a worker record with configured names in skill order."""
    worker = get_worker(connection, worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker nicht gefunden")
    names = {module["id"]: module["name"] for module in list_module_types(connection)}
    worker["skill_names"] = [names.get(skill, skill) for skill in worker["skills"]]
    return worker


@link_router.post("/control-link")
def post_control_link(
    request: Request,
    worker_id: str = Depends(require_worker),
    engine: Engine = Depends(get_engine),
) -> dict[str, str]:
    """Create a one-time browser URL for the authenticated worker."""
    with engine.begin() as connection:
        token = create_control_link(connection, worker_id, datetime.now(timezone.utc))
    settings = request.app.state.proxy_settings
    public_url = settings.public_url if settings is not None else str(request.base_url).rstrip("/")
    return {"url": f"{public_url}/worker-control?token={token}"}


@router.post("/exchange")
def post_control_exchange(
    payload: ControlTokenInput,
    request: Request,
    response: Response,
    engine: Engine = Depends(get_engine),
) -> dict:
    """Exchange a one-time URL token for a worker-scoped cookie session."""
    session_hours = request.app.state.worker_client.control_session_hours
    with engine.begin() as connection:
        session = exchange_control_token(connection, payload.token, datetime.now(timezone.utc), session_hours)
        if session is None:
            raise HTTPException(status_code=401, detail=link_error)
        worker_id, session_token = session
        worker = _worker_response(connection, worker_id)
    response.set_cookie(
        key=control_cookie,
        value=session_token,
        max_age=session_hours * 3600,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return worker


@router.get("/self")
def get_control_self(
    worker_id: str = Depends(current_control_worker),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Return the worker associated with the current control session."""
    with engine.begin() as connection:
        return _worker_response(connection, worker_id)


@router.put("/self/availability")
def put_control_availability(
    payload: object = Body(...),
    worker_id: str = Depends(current_control_worker),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Update only the current worker's availability rules."""
    try:
        with engine.begin() as connection:
            if not update_availability(connection, worker_id, payload):
                raise HTTPException(status_code=404, detail="Worker nicht gefunden")
            return _worker_response(connection, worker_id)
    except WorkerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/self/start")
def post_control_start(
    worker_id: str = Depends(current_control_worker),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Start the worker associated with the current control session."""
    try:
        with engine.begin() as connection:
            if not start_worker(connection, worker_id):
                raise HTTPException(status_code=404, detail="Worker nicht gefunden")
            return _worker_response(connection, worker_id)
    except WorkerConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/self/stop-drain")
def post_control_stop_drain(
    worker_id: str = Depends(current_control_worker),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Drain and stop the worker associated with this control session."""
    with engine.begin() as connection:
        if not drain_worker(connection, worker_id, datetime.now(timezone.utc)):
            raise HTTPException(status_code=404, detail="Worker nicht gefunden")
        return _worker_response(connection, worker_id)


@router.post("/self/stop-now")
def post_control_stop_now(
    worker_id: str = Depends(current_control_worker),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Immediately stop the worker associated with this control session."""
    with engine.begin() as connection:
        if not stop_worker_now(connection, worker_id, datetime.now(timezone.utc)):
            raise HTTPException(status_code=404, detail="Worker nicht gefunden")
        return _worker_response(connection, worker_id)
