"""Worker management endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.engine import Connection, Engine

from marathon.server.api.deps import get_engine, require_admin
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.queries import get_worker
from marathon.server.db.worker_writes import (
    WorkerConflict,
    WorkerValidationError,
    approve_worker,
    delete_worker,
    drain_worker,
    start_worker,
    stop_worker_now,
    update_availability,
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
    """Start an approved stopped worker."""
    try:
        with engine.begin() as connection:
            worker = _worker_response(connection, start_worker(connection, worker_id), worker_id)
    except WorkerConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return worker


@router.post("/workers/{worker_id}/stop-drain")
def post_worker_stop_drain(
    worker_id: str,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Drain assigned work and stop once running work finishes."""
    with engine.begin() as connection:
        changed = drain_worker(connection, worker_id, datetime.now(timezone.utc))
        return _worker_response(connection, changed, worker_id)


@router.post("/workers/{worker_id}/stop-now")
def post_worker_stop_now(
    worker_id: str,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Request immediate cancellation of the worker's active jobs."""
    with engine.begin() as connection:
        changed = stop_worker_now(connection, worker_id, datetime.now(timezone.utc))
        return _worker_response(connection, changed, worker_id)


@router.post("/workers/{worker_id}/approve")
def post_worker_approve(
    worker_id: str,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Approve a worker while leaving its status unchanged."""
    with engine.begin() as connection:
        changed = approve_worker(connection, worker_id)
        return _worker_response(connection, changed, worker_id)


@router.put("/workers/{worker_id}/availability")
def put_worker_availability(
    worker_id: str,
    payload: object = Body(...),
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Replace a worker's availability rules with their canonical form."""
    try:
        with engine.begin() as connection:
            changed = update_availability(connection, worker_id, payload)
            worker = _worker_response(connection, changed, worker_id)
    except WorkerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return worker


@router.delete("/workers/{worker_id}")
def delete_worker_endpoint(
    worker_id: str,
    _admin: AuthUser = Depends(require_admin),
    engine: Engine = Depends(get_engine),
) -> dict:
    """Delete an idle worker except for the server worker."""
    try:
        with engine.begin() as connection:
            if not delete_worker(connection, worker_id):
                raise HTTPException(status_code=404, detail="Worker nicht gefunden")
    except WorkerConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}
