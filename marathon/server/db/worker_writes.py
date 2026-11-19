"""Validated write operations for workers."""

import re
from datetime import datetime

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from marathon.server.engine.jobs import cancel_job


_worker_type_pattern = re.compile(r"^[a-z0-9_]{1,64}$")


class WorkerValidationError(ValueError):
    """Raised when worker settings do not meet the API contract."""


class WorkerConflict(Exception):
    """Raised when a worker cannot be started while it is stopping."""


def update_worker(connection: Connection, worker_id: str, name: str, skills: list[str]) -> bool:
    """Validate and replace a worker's name and ordered skills."""
    worker = connection.execute(
        text("SELECT capabilities FROM workers WHERE id = :worker_id"), {"worker_id": worker_id}
    ).mappings().one_or_none()
    if worker is None:
        return False

    name = name.strip()
    if not name:
        raise WorkerValidationError("Name fehlt")
    if len(name) > 100:
        raise WorkerValidationError("Name ist zu lang (max. 100 Zeichen)")

    seen: set[str] = set()
    for skill in skills:
        if not isinstance(skill, str) or not _worker_type_pattern.fullmatch(skill):
            raise WorkerValidationError(f"Ungültiger Modultyp: {skill}")
        if skill in seen:
            raise WorkerValidationError(f"Fähigkeit doppelt: {skill}")
        if skill not in worker["capabilities"]:
            raise WorkerValidationError(f"Fähigkeit wird vom Rechner nicht gemeldet: {skill}")
        seen.add(skill)

    connection.execute(
        text("UPDATE workers SET name = :name, skills = :skills WHERE id = :worker_id").bindparams(
            bindparam("skills", type_=JSONB())
        ),
        {"worker_id": worker_id, "name": name, "skills": skills},
    )
    return True


def start_worker(connection: Connection, worker_id: str) -> bool:
    """Set legacy or stopped workers to ready; leave other states unchanged."""
    worker = connection.execute(
        text("SELECT status FROM workers WHERE id = :worker_id FOR UPDATE"), {"worker_id": worker_id}
    ).mappings().one_or_none()
    if worker is None:
        return False
    if worker["status"] == "stopping":
        raise WorkerConflict("Worker wird gerade gestoppt")
    if worker["status"] in ("stopped", "new", "locked"):
        connection.execute(
            text("UPDATE workers SET status = 'ready' WHERE id = :worker_id"), {"worker_id": worker_id}
        )
    return True


def stop_worker(connection: Connection, worker_id: str, now: datetime) -> bool:
    """Cancel a worker's active jobs and stop it once none remain active."""
    job_ids = list(connection.execute(
        text(
            "SELECT id FROM jobs WHERE worker_id = :worker_id "
            "AND status IN ('assigned', 'running', 'cancelling') ORDER BY id"
        ),
        {"worker_id": worker_id},
    ).scalars())
    for job_id in job_ids:
        cancel_job(connection, job_id, reason="Worker gestoppt", now=now)

    # Lock the worker last to preserve the workflow/job/asset/worker lock order.
    worker = connection.execute(
        text("SELECT status FROM workers WHERE id = :worker_id FOR UPDATE"), {"worker_id": worker_id}
    ).mappings().one_or_none()
    if worker is None:
        return False

    status = worker["status"]
    if status not in ("stopping", "stopped"):
        status = "stopping"
    active_job = connection.execute(
        text(
            "SELECT 1 FROM jobs WHERE worker_id = :worker_id "
            "AND status IN ('assigned', 'running', 'cancelling') LIMIT 1"
        ),
        {"worker_id": worker_id},
    ).scalar_one_or_none()
    if active_job is None:
        status = "stopped"
    connection.execute(
        text("UPDATE workers SET status = :status WHERE id = :worker_id"),
        {"worker_id": worker_id, "status": status},
    )
    return True
