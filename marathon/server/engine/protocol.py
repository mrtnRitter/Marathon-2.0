"""Database operations for the worker HTTP protocol."""

# IMPORTS
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from marathon.common.availability import AvailabilityError, default_availability, normalize_availability
from marathon.server.config import EngineConfig
from marathon.server.engine.flow import asset_file_path
from marathon.server.engine.jobs import (
    JobNotFound,
    JobWorkerMismatch,
    active_job_statuses,
    drop_unstarted_job,
    interrupt_job,
)


# CONFIG
protocol_version = 3
_terminal_job_statuses = ("succeeded", "failed", "cancelled", "interrupted")


# FUNC
def _normalized_availability(value: object) -> dict[str, Any]:
    try:
        return normalize_availability(value)
    except AvailabilityError:
        return default_availability()


def register_worker(
    connection: Connection,
    payload: Mapping[str, Any],
    ip_address: str | None,
    now: datetime,
    config: EngineConfig,
) -> dict[str, Any]:
    """Register a server worker or refresh an enrolled worker and its active jobs."""
    worker_id = payload["worker_id"]
    available = payload.get("available", True)
    availability_reason = payload.get("availability_reason", "")[:200]
    worker = connection.execute(
        text("SELECT status FROM workers WHERE id = :worker_id"), {"worker_id": worker_id}
    ).mappings().one_or_none()

    if worker is None:
        if worker_id != "server-worker":
            raise LookupError("Worker unbekannt")
        status = "ready"
        statement = text(
            "INSERT INTO workers (id, name, status, capabilities, skills, online, client_version, "
            "ip_address, last_seen, created_at, client_available, availability_reason) "
            "VALUES (:worker_id, :name, :status, :capabilities, :skills, true, :client_version, "
            ":ip_address, :now, :now, :available, :availability_reason)"
        ).bindparams(bindparam("capabilities", type_=JSONB), bindparam("skills", type_=JSONB))
        connection.execute(statement, {
            "worker_id": worker_id, "name": payload["name"], "status": status,
            "capabilities": payload["capabilities"], "skills": payload["capabilities"],
            "client_version": payload["client_version"], "ip_address": ip_address, "now": now,
            "available": available, "availability_reason": availability_reason,
        })
    else:
        jobs = list(connection.execute(
            text(
                "SELECT id, status FROM jobs WHERE worker_id = :worker_id "
                "AND status IN ('assigned', 'running', 'cancelling') ORDER BY id"
            ), {"worker_id": worker_id}
        ).mappings())
        for job in jobs:
            if job["status"] == "assigned":
                drop_unstarted_job(connection, job["id"], now)
            else:
                interrupt_job(connection, job["id"], "Worker neu gestartet", now, config.max_attempts)
        connection.execute(
            text(
                "UPDATE workers SET client_version = :client_version, capabilities = :capabilities, "
                "ip_address = :ip_address, last_seen = :now, online = true, "
                "client_available = :available, availability_reason = :availability_reason "
                "WHERE id = :worker_id"
            ).bindparams(bindparam("capabilities", type_=JSONB)),
            {
                "worker_id": worker_id, "client_version": payload["client_version"],
                "capabilities": payload["capabilities"], "ip_address": ip_address, "now": now,
                "available": available, "availability_reason": availability_reason,
            },
        )

    row = connection.execute(
        text(
            "SELECT status, approved, availability_config, availability_revision "
            "FROM workers WHERE id = :worker_id"
        ), {"worker_id": worker_id}
    ).mappings().one()
    return {
        "worker_id": worker_id,
        "status": row["status"],
        "approved": row["approved"],
        "protocol": protocol_version,
        "poll_timeout_s": config.poll_timeout_s,
        "availability": {
            "config": _normalized_availability(row["availability_config"]),
            "revision": row["availability_revision"],
        },
    }


def touch_worker(
    connection: Connection,
    worker_id: str,
    now: datetime,
    available: bool = True,
    availability_reason: str = "",
) -> bool:
    """Refresh worker presence and release unstarted jobs while unavailable."""
    result = connection.execute(
        text(
            "UPDATE workers SET last_seen = :now, online = true, client_available = :available, "
            "availability_reason = :availability_reason WHERE id = :worker_id"
        ),
        {
            "worker_id": worker_id, "now": now, "available": available,
            "availability_reason": availability_reason[:200],
        },
    )
    if not result.rowcount:
        return False
    if not available:
        assigned_ids = connection.execute(
            text("SELECT id FROM jobs WHERE worker_id = :worker_id AND status = 'assigned' ORDER BY id"),
            {"worker_id": worker_id},
        ).scalars()
        for job_id in assigned_ids:
            drop_unstarted_job(connection, job_id, now)
    return True


def poll_commands(
    connection: Connection,
    worker_id: str,
    running: Sequence[str],
    availability_revision: int = -1,
) -> dict[str, Any]:
    """Return jobs, worker state, and any availability revision the client lacks."""
    worker = connection.execute(
        text(
            "SELECT status, approved, availability_config, availability_revision "
            "FROM workers WHERE id = :worker_id"
        ), {"worker_id": worker_id}
    ).mappings().one_or_none()
    if worker is None:
        return {"jobs": [], "cancel": []}

    job_rows = connection.execute(
        text(
            "SELECT j.id AS job_id, j.type_id, j.settings, a.id AS asset_id, a.name AS asset_name, a.metadata "
            "FROM jobs AS j LEFT JOIN job_assets AS ja ON ja.job_id = j.id "
            "LEFT JOIN assets AS a ON a.id = ja.asset_id "
            "WHERE j.worker_id = :worker_id AND j.status = 'assigned' ORDER BY j.id, a.id"
        ), {"worker_id": worker_id}
    ).mappings()
    jobs = []
    jobs_by_id = {}
    for row in job_rows:
        job = jobs_by_id.get(row["job_id"])
        if job is None:
            job = {
                "job_id": row["job_id"],
                "type_id": row["type_id"],
                "settings": row["settings"],
                "assets": [],
            }
            jobs_by_id[row["job_id"]] = job
            jobs.append(job)
        if row["asset_id"] is not None:
            job["assets"].append({
                "id": row["asset_id"],
                "name": row["asset_name"],
                "path": asset_file_path(row["metadata"] or {}),
            })

    cancelling = list(connection.execute(
        text("SELECT id FROM jobs WHERE worker_id = :worker_id AND status = 'cancelling' ORDER BY id"),
        {"worker_id": worker_id},
    ).scalars())
    running_ids = list(dict.fromkeys(running))
    active_rows = []
    if running_ids:
        active_rows = list(connection.execute(
            text("SELECT id, worker_id, status FROM jobs WHERE id IN :job_ids").bindparams(
                bindparam("job_ids", expanding=True)
            ), {"job_ids": running_ids}
        ).mappings())
    active_by_id = {row["id"]: row for row in active_rows}
    stale = [
        job_id for job_id in running_ids
        if job_id not in active_by_id
        or active_by_id[job_id]["worker_id"] != worker_id
        or active_by_id[job_id]["status"] not in ("running", "cancelling")
    ]
    response = {
        "jobs": jobs,
        "cancel": list(dict.fromkeys([*cancelling, *stale])),
        "status": worker["status"],
        "approved": worker["approved"],
    }
    if worker["availability_revision"] != availability_revision:
        response["availability"] = {
            "config": _normalized_availability(worker["availability_config"]),
            "revision": worker["availability_revision"],
        }
    return response


def job_progress(
    connection: Connection,
    job_id: str,
    worker_id: str,
    progress: int,
    log: Sequence[str],
) -> bool:
    """Record active job progress and return whether the worker should stop."""
    job = connection.execute(
        text("SELECT workflow_id, module_id, status, worker_id FROM jobs WHERE id = :job_id FOR UPDATE"),
        {"job_id": job_id},
    ).mappings().one_or_none()
    if job is None:
        raise JobNotFound(f"Job nicht gefunden: {job_id}")
    if job["worker_id"] != worker_id:
        raise JobWorkerMismatch(f"Job gehört einem anderen Worker: {job_id}")
    if job["status"] in active_job_statuses:
        connection.execute(
            text("UPDATE jobs SET progress = :progress WHERE id = :job_id"),
            {"job_id": job_id, "progress": progress},
        )
        if log:
            connection.execute(
                text(
                    "INSERT INTO log_lines (workflow_id, module_id, job_id, level, text) "
                    "VALUES (:workflow_id, :module_id, :job_id, 'info', :text)"
                ),
                [
                    {"workflow_id": job["workflow_id"], "module_id": job["module_id"],
                     "job_id": job_id, "text": message}
                    for message in log
                ],
            )
    return job["status"] in ("cancelling", *_terminal_job_statuses)
