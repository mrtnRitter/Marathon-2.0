"""Authenticated HTTP endpoints for worker registration and job updates."""

# IMPORTS
import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.concurrency import run_in_threadpool

from marathon.server.api.deps import get_engine
from marathon.server.config import EngineConfig
from marathon.server.engine.jobs import JobNotFound, JobWorkerMismatch, apply_job_result, start_job
from marathon.server.engine.protocol import job_progress, poll_commands, register_worker, touch_worker
from marathon.server.worker_auth import require_worker_token


# MODELS
class RegisterInput(BaseModel):
    """Worker metadata submitted during registration."""

    worker_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str
    client_version: str
    capabilities: list[str]
    max_parallel: int = Field(ge=1)
    protocol: int


class PollInput(BaseModel):
    """Worker identity and locally running job IDs for long-polling."""

    worker_id: str
    running: list[str] = Field(default_factory=list)


class StartInput(BaseModel):
    """Worker identity acknowledging a job assignment."""

    worker_id: str


class ProgressInput(BaseModel):
    """Progress and log updates for an active job."""

    worker_id: str
    progress: int = Field(ge=0, le=100)
    log: list[str] = Field(default_factory=list)


class SpawnedInput(BaseModel):
    """A child asset emitted by a job result."""

    path: str
    output: str
    log: list[str] = Field(default_factory=list)


class ResultInput(BaseModel):
    """Worker job outcome accepted by the workflow engine."""

    worker_id: str
    outcome: Literal["result", "error", "cancelled"]
    output: str | None = None
    log: list[str] = Field(default_factory=list)
    asset_path: str | None = None
    spawned: list[SpawnedInput] = Field(default_factory=list)
    error: str | None = None


# CONFIG
router = APIRouter(prefix="/worker", tags=["worker"], dependencies=[Depends(require_worker_token)])
_empty_poll = {"jobs": [], "cancel": []}


# FUNC
def _set_poll_timeouts(connection) -> None:
    connection.execute(text("SET LOCAL lock_timeout = '2s'"))
    connection.execute(text("SET LOCAL statement_timeout = '2s'"))


def _touch(engine: Engine, worker_id: str, now: datetime) -> bool:
    with engine.begin() as connection:
        _set_poll_timeouts(connection)
        return touch_worker(connection, worker_id, now)


def _poll(engine: Engine, worker_id: str, running: Sequence[str]) -> dict:
    with engine.begin() as connection:
        _set_poll_timeouts(connection)
        return poll_commands(connection, worker_id, running)


def _consume_background_result(task: asyncio.Task) -> None:
    """Retrieve exceptions from thread work that outlived a disconnected poll."""
    if not task.cancelled():
        task.exception()


async def _run_threadpool_until(deadline: float, function, *args):
    """Run blocking database work without extending the poll's hard deadline."""
    task = asyncio.create_task(run_in_threadpool(function, *args))
    done, _pending = await asyncio.wait({task}, timeout=max(0.0, deadline - asyncio.get_running_loop().time()))
    if task not in done:
        task.add_done_callback(_consume_background_result)
        return False, None
    return True, task.result()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/register")
def post_worker_register(
    payload: RegisterInput,
    request: Request,
    engine: Engine = Depends(get_engine),
) -> dict:
    """Register or refresh a worker using protocol version 1."""
    if payload.protocol != 1:
        raise HTTPException(status_code=409, detail="Protokollversion nicht unterstützt")
    config: EngineConfig = request.app.state.engine_config
    ip_address = request.client.host if request.client is not None else None
    with engine.begin() as connection:
        return register_worker(connection, payload.model_dump(), ip_address, _utc_now(), config)


@router.post("/poll")
async def post_worker_poll(
    payload: PollInput,
    request: Request,
    engine: Engine = Depends(get_engine),
) -> dict:
    """Wait asynchronously for commands while periodically refreshing the worker."""
    config: EngineConfig = request.app.state.engine_config
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + config.poll_timeout_s
    hard_deadline = deadline + 3.0
    last_seen: float | None = None

    while True:
        if await request.is_disconnected():
            return _empty_poll
        current = loop.time()
        if last_seen is None or current - last_seen >= 5.0:
            completed, exists = await _run_threadpool_until(
                hard_deadline, _touch, engine, payload.worker_id, _utc_now()
            )
            if await request.is_disconnected():
                return _empty_poll
            if not completed:
                return _empty_poll
            if not exists:
                raise HTTPException(status_code=404, detail="Worker unbekannt")
            last_seen = loop.time()

        if await request.is_disconnected():
            return _empty_poll
        completed, response = await _run_threadpool_until(
            hard_deadline, _poll, engine, payload.worker_id, payload.running
        )
        if await request.is_disconnected():
            return _empty_poll
        if not completed:
            return _empty_poll
        if response["jobs"] or response["cancel"]:
            return response
        remaining = deadline - loop.time()
        if remaining <= 0:
            return response
        await asyncio.sleep(min(0.5, remaining))


@router.post("/jobs/{job_id}/start")
def post_worker_job_start(
    job_id: str,
    payload: StartInput,
    engine: Engine = Depends(get_engine),
) -> dict[str, bool]:
    """Mark an assigned job as started by its worker."""
    try:
        with engine.begin() as connection:
            started = start_job(connection, job_id, payload.worker_id, _utc_now())
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="Job nicht gefunden") from exc
    if not started:
        raise HTTPException(status_code=409, detail="Job nicht mehr gültig")
    return {"ok": True}


@router.post("/jobs/{job_id}/progress")
def post_worker_job_progress(
    job_id: str,
    payload: ProgressInput,
    engine: Engine = Depends(get_engine),
) -> dict[str, bool]:
    """Persist progress and return whether the worker must cancel the job."""
    try:
        with engine.begin() as connection:
            cancel = job_progress(connection, job_id, payload.worker_id, payload.progress, payload.log)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="Job nicht gefunden") from exc
    except JobWorkerMismatch as exc:
        raise HTTPException(status_code=403, detail="Job gehört einem anderen Worker") from exc
    return {"cancel": cancel}


@router.post("/jobs/{job_id}/result")
def post_worker_job_result(
    job_id: str,
    payload: ResultInput,
    request: Request,
    engine: Engine = Depends(get_engine),
) -> dict[str, bool]:
    """Apply a result from the assigned worker and acknowledge acceptance."""
    try:
        with engine.begin() as connection:
            accepted = apply_job_result(
                connection,
                job_id,
                payload.worker_id,
                payload.model_dump(exclude={"worker_id"}),
                _utc_now(),
                request.app.state.engine_config.max_attempts,
            )
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail="Job nicht gefunden") from exc
    except JobWorkerMismatch as exc:
        raise HTTPException(status_code=403, detail="Job gehört einem anderen Worker") from exc
    return {"accepted": accepted}
