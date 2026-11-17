"""Validated write operations for workers."""

import re

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection


priority_levels = ("preferred", "normal", "last", "off")
_worker_type_pattern = re.compile(r"^[a-z0-9_]{1,64}$")


class WorkerValidationError(ValueError):
    """Raised when worker settings do not meet the API contract."""


def update_worker(
    connection: Connection,
    worker_id: str,
    name: str,
    max_parallel: int,
    priorities: dict[str, str],
) -> bool:
    """Validate and replace a worker's editable settings; return whether it exists."""
    name = name.strip()
    if not name:
        raise WorkerValidationError("Name fehlt")
    if len(name) > 100:
        raise WorkerValidationError("Name ist zu lang (max. 100 Zeichen)")
    if isinstance(max_parallel, bool) or not 1 <= max_parallel <= 64:
        raise WorkerValidationError("Parallele Jobs: 1 bis 64")
    for module_type, priority in priorities.items():
        if not _worker_type_pattern.fullmatch(module_type):
            raise WorkerValidationError(f"Ungültiger Modultyp: {module_type}")
        if priority not in priority_levels:
            raise WorkerValidationError(f"Ungültiger Vorrang für {module_type}: {priority}")

    result = connection.execute(
        text(
            "UPDATE workers SET name = :name, max_parallel = :max_parallel, priorities = :priorities "
            "WHERE id = :worker_id"
        ).bindparams(bindparam("priorities", type_=JSONB)),
        {"worker_id": worker_id, "name": name, "max_parallel": max_parallel, "priorities": priorities},
    )
    return result.rowcount > 0


def approve_worker(connection: Connection, worker_id: str) -> bool:
    """Set a worker to ready and return whether it exists."""
    result = connection.execute(
        text("UPDATE workers SET status = 'ready' WHERE id = :worker_id"),
        {"worker_id": worker_id},
    )
    return result.rowcount > 0


def lock_worker(connection: Connection, worker_id: str) -> bool:
    """Set a worker to locked and return whether it exists."""
    result = connection.execute(
        text("UPDATE workers SET status = 'locked' WHERE id = :worker_id"),
        {"worker_id": worker_id},
    )
    return result.rowcount > 0
