"""Worker management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.engine import Connection, Engine

from marathon.server.api.deps import get_engine
from marathon.server.db.queries import get_worker
from marathon.server.db.worker_writes import (
    WorkerValidationError,
    approve_worker,
    lock_worker,
    update_worker,
)


class WorkerInput(BaseModel):
    """Editable worker configuration."""

    name: str
    max_parallel: int
    priorities: dict[str, str]


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
    engine: Engine = Depends(get_engine),
) -> dict:
    """Replace a worker's name, concurrency limit, and module priorities."""
    try:
        with engine.begin() as connection:
            changed = update_worker(
                connection, worker_id, payload.name, payload.max_parallel, payload.priorities
            )
            worker = _worker_response(connection, changed, worker_id)
    except WorkerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return worker


@router.post("/workers/{worker_id}/approve")
def post_worker_approve(
    worker_id: str,
    engine: Engine = Depends(get_engine),
) -> dict:
    """Approve a worker by setting it to ready."""
    with engine.begin() as connection:
        return _worker_response(connection, approve_worker(connection, worker_id), worker_id)


@router.post("/workers/{worker_id}/lock")
def post_worker_lock(
    worker_id: str,
    engine: Engine = Depends(get_engine),
) -> dict:
    """Lock a worker so it receives no new jobs."""
    with engine.begin() as connection:
        return _worker_response(connection, lock_worker(connection, worker_id), worker_id)
