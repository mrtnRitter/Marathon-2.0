"""Database operations for the worker HTTP protocol."""

# IMPORTS
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

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
_terminal_job_statuses = ("succeeded", "failed", "cancelled", "interrupted")


# FUNC
def register_worker(
    connection: Connection,
    payload: Mapping[str, Any],
    ip_address: str | None,
    now: datetime,
    config: EngineConfig,
) -> dict[str, Any]:
    """Register a new worker or refresh a known worker and its active jobs."""
    worker_id = payload["worker_id"]
    worker = connection.execute(
        text("SELECT status FROM workers WHERE id = :worker_id"), {"worker_id": worker_id}
    ).mappings().one_or_none()
    priorities = {module_type: "last" for module_type in payload.get("last_choice", [])}
    statement = text(
        "INSERT INTO workers (id, name, status, capabilities, priorities, max_parallel, client_slots, "
        "client_version, ip_address, last_seen, created_at) VALUES (:worker_id, :name, :status, "
        ":capabilities, :priorities, :max_parallel, :max_parallel, :client_version, :ip_address, :now, :now)"
    ).bindparams(bindparam("capabilities", type_=JSONB), bindparam("priorities", type_=JSONB))

    if worker is None:
        status = "ready" if worker_id == "server-worker" else "new"
        connection.execute(statement, {
            "worker_id": worker_id, "name": payload["name"], "status": status,
            "capabilities": payload["capabilities"], "priorities": priorities,
            "max_parallel": payload["max_parallel"], "client_version": payload["client_version"],
            "ip_address": ip_address, "now": now,
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
        status_row = connection.execute(
            text(
                "UPDATE workers SET client_version = :client_version, capabilities = :capabilities, "
                "client_slots = :client_slots, ip_address = :ip_address, last_seen = :now, "
                "status = CASE WHEN status = 'offline' THEN 'ready' ELSE status END "
                "WHERE id = :worker_id RETURNING status"
            ).bindparams(bindparam("capabilities", type_=JSONB)),
            {
                "worker_id": worker_id, "client_version": payload["client_version"],
                "capabilities": payload["capabilities"], "client_slots": payload["max_parallel"],
                "ip_address": ip_address, "now": now,
            },
        ).mappings().one()
        status = status_row["status"]

    return {
        "worker_id": worker_id,
        "status": status,
        "protocol": 1,
        "poll_timeout_s": config.poll_timeout_s,
    }


def touch_worker(connection: Connection, worker_id: str, now: datetime) -> bool:
    """Refresh a worker heartbeat, reviving it only when it was offline."""
    result = connection.execute(
        text(
            "UPDATE workers SET last_seen = :now, "
            "status = CASE WHEN status = 'offline' THEN 'ready' ELSE status END WHERE id = :worker_id"
        ),
        {"worker_id": worker_id, "now": now},
    )
    return result.rowcount > 0


def poll_commands(connection: Connection, worker_id: str, running: Sequence[str]) -> dict[str, Any]:
    """Return assigned jobs and cancellation requests for a registered worker."""
    job_rows = connection.execute(
        text(
            "SELECT j.id AS job_id, j.type_id, j.settings, a.id AS asset_id, a.name AS asset_name, a.metadata "
            "FROM jobs AS j LEFT JOIN job_assets AS ja ON ja.job_id = j.id "
            "LEFT JOIN assets AS a ON a.id = ja.asset_id "
            "WHERE j.worker_id = :worker_id AND j.status = 'assigned' ORDER BY j.id, a.id"
        ), {"worker_id": worker_id}
    ).mappings()
    jobs = []
    seen_jobs = set()
    for row in job_rows:
        if row["job_id"] in seen_jobs:
            continue
        seen_jobs.add(row["job_id"])
        jobs.append({
            "job_id": row["job_id"],
            "type_id": row["type_id"],
            "settings": row["settings"],
            "asset": {
                "id": row["asset_id"],
                "name": row["asset_name"],
                "path": asset_file_path(row["metadata"] or {}),
            },
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
    return {"jobs": jobs, "cancel": list(dict.fromkeys([*cancelling, *stale]))}


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
