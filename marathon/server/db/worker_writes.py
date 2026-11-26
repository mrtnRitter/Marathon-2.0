"""Validated write operations for workers."""

import re
from datetime import datetime

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from marathon.common.availability import AvailabilityError, normalize_availability
from marathon.server.engine.jobs import cancel_job, drop_unstarted_job


_worker_type_pattern = re.compile(r"^[a-z0-9_]{1,64}$")


class WorkerValidationError(ValueError):
    """Raised when worker settings do not meet the API contract."""


class WorkerConflict(Exception):
    """Raised when a worker state prevents the requested control operation."""


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
    """Make an approved stopped or draining worker ready."""
    worker = connection.execute(
        text("SELECT status, approved FROM workers WHERE id = :worker_id FOR UPDATE"), {"worker_id": worker_id}
    ).mappings().one_or_none()
    if worker is None:
        return False
    if worker["status"] == "stopping":
        raise WorkerConflict("Worker wird gerade gestoppt")
    if not worker["approved"]:
        raise WorkerConflict("Worker wartet auf Freigabe durch einen Administrator")
    if worker["status"] in ("stopped", "draining"):
        connection.execute(
            text("UPDATE workers SET status = 'ready' WHERE id = :worker_id"), {"worker_id": worker_id}
        )
    return True


def drain_worker(connection: Connection, worker_id: str, now: datetime) -> bool:
    """Return unstarted assignments and let active jobs finish before stopping."""
    initial = connection.execute(
        text("SELECT status FROM workers WHERE id = :worker_id"), {"worker_id": worker_id}
    ).mappings().one_or_none()
    if initial is None:
        return False
    if initial["status"] in ("ready", "draining"):
        job_ids = connection.execute(
            text(
                "SELECT id FROM jobs WHERE worker_id = :worker_id AND status = 'assigned' ORDER BY id"
            ),
            {"worker_id": worker_id},
        ).scalars().all()
        for job_id in job_ids:
            drop_unstarted_job(connection, job_id, now)

    # Lock the worker last to preserve the workflow/job/asset/worker lock order.
    worker = connection.execute(
        text("SELECT status FROM workers WHERE id = :worker_id FOR UPDATE"), {"worker_id": worker_id}
    ).mappings().one_or_none()
    if worker is None:
        return False

    status = worker["status"]
    if status == "ready":
        status = "draining"
    active_job = connection.execute(
        text(
            "SELECT 1 FROM jobs WHERE worker_id = :worker_id "
            "AND status IN ('assigned', 'running', 'cancelling') LIMIT 1"
        ),
        {"worker_id": worker_id},
    ).scalar_one_or_none()
    if active_job is None and status in ("draining", "ready"):
        status = "stopped"
    connection.execute(
        text("UPDATE workers SET status = :status WHERE id = :worker_id"),
        {"worker_id": worker_id, "status": status},
    )
    return True


def stop_worker_now(connection: Connection, worker_id: str, now: datetime) -> bool:
    """Cancel active jobs and stop the worker once cancellation completes."""
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


def approve_worker(connection: Connection, worker_id: str) -> bool:
    """Approve a worker without changing its current status."""
    result = connection.execute(
        text("UPDATE workers SET approved = true WHERE id = :worker_id"), {"worker_id": worker_id}
    )
    return result.rowcount > 0


def delete_worker(connection: Connection, worker_id: str) -> bool:
    """Delete an idle worker, retaining server-worker and active workers."""
    worker = connection.execute(
        text("SELECT id FROM workers WHERE id = :worker_id FOR UPDATE"), {"worker_id": worker_id}
    ).scalar_one_or_none()
    if worker is None:
        return False
    if worker_id == "server-worker":
        raise WorkerConflict("Der Server-Worker kann nicht entfernt werden")
    active_job = connection.execute(
        text(
            "SELECT 1 FROM jobs WHERE worker_id = :worker_id "
            "AND status IN ('assigned', 'running', 'cancelling') LIMIT 1"
        ),
        {"worker_id": worker_id},
    ).scalar_one_or_none()
    if active_job is not None:
        raise WorkerConflict("Worker hat noch laufende Jobs. Zuerst stoppen.")
    connection.execute(text("DELETE FROM workers WHERE id = :worker_id"), {"worker_id": worker_id})
    return True


def update_availability(connection: Connection, worker_id: str, config: object) -> bool:
    """Validate and store canonical worker availability settings."""
    exists = connection.execute(
        text("SELECT 1 FROM workers WHERE id = :worker_id"), {"worker_id": worker_id}
    ).scalar_one_or_none()
    if exists is None:
        return False
    try:
        normalized = normalize_availability(config)
    except AvailabilityError as exc:
        raise WorkerValidationError(str(exc)) from exc
    statement = text(
        "UPDATE workers SET availability_config = :config, "
        "availability_revision = availability_revision + 1 WHERE id = :worker_id"
    ).bindparams(bindparam("config", type_=JSONB()))
    connection.execute(statement, {"worker_id": worker_id, "config": normalized})
    return True
