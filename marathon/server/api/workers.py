"""Worker management endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.engine import Connection, Engine

from marathon.server.api.deps import get_engine, require_admin
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.queries import get_worker
from marathon.server.db.worker_writes import (
    WorkerConflict,
    WorkerValidationError,
    start_worker,
    stop_worker,
    update_worker,
)


class WorkerInput(BaseModel):
    """Editable worker configuration."""

    name: str
    skills: list[str]


router = APIRouter(tags=["workers"])


def _worker_response(connection: Connection, changed: bool, worker_id: str) -> dict:
    """Return the changed worker or raise the API's not-found response."""
    worker = get_worker(connection, worker_id) if changed else None
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker nicht gefunden")
    return worker


@router.put("/workers/{worker_id}")
def put_worker(
    worker_id: str,
    payload: WorkerInput,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Replace a worker's name and ordered skills."""
    try:
        with engine.begin() as connection:
            changed = update_worker(connection, worker_id, payload.name, payload.skills)
            worker = _worker_response(connection, changed, worker_id)
    except WorkerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return worker


@router.post("/workers/{worker_id}/start")
def post_worker_start(
    worker_id: str,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Start a stopped worker."""
    try:
        with engine.begin() as connection:
            worker = _worker_response(connection, start_worker(connection, worker_id), worker_id)
    except WorkerConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return worker


@router.post("/workers/{worker_id}/stop")
def post_worker_stop(
    worker_id: str,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Stop a worker after requesting cancellation of its active jobs."""
    with engine.begin() as connection:
        changed = stop_worker(connection, worker_id, datetime.now(timezone.utc))
        return _worker_response(connection, changed, worker_id)
