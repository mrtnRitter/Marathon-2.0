"""Advance workflow assets and assign jobs to available workers."""

# IMPORTS
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import logging
from threading import Event, Lock, Thread, current_thread
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from marathon.server.config import EngineConfig
from marathon.server.engine.flow import asset_file_path, enter_asset, fail_asset, load_graph, route_asset
from marathon.server.engine.jobs import (
    create_job,
    drop_unstarted_job,
    interrupt_job,
)


# CONFIG
_logger = logging.getLogger("marathon.engine")
_priority_rank = {"preferred": 0, "normal": 1, "last": 2}


# FUNC
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fail_lost_workers(connection: Connection, now: datetime, config: EngineConfig) -> int:
    """Interrupt jobs of silent workers and mark ready workers offline."""
    cutoff = now - timedelta(seconds=config.worker_timeout_s)
    jobs = connection.execute(
        text(
            "SELECT j.id, j.status FROM jobs AS j "
            "JOIN workers AS w ON w.id = j.worker_id "
            "WHERE j.status IN ('assigned', 'running', 'cancelling') "
            "AND COALESCE(w.last_seen, w.created_at) < :cutoff ORDER BY j.id"
        ),
        {"cutoff": cutoff},
    ).mappings().all()
    for job in jobs:
        if job["status"] == "assigned":
            drop_unstarted_job(connection, job["id"], now)
        else:
            interrupt_job(connection, job["id"], "Worker antwortet nicht", now, config.max_attempts)

    offline_workers = connection.execute(
        text(
            "UPDATE workers SET status = 'offline' WHERE status IN ('ready', 'busy') "
            "AND COALESCE(last_seen, created_at) < :cutoff RETURNING id"
        ),
        {"cutoff": cutoff},
    ).all()
    return len(jobs) + len(offline_workers)


def expire_pickups(connection: Connection, now: datetime) -> int:
    """Cancel assigned jobs whose pickup deadline has passed."""
    job_ids = connection.execute(
        text(
            "SELECT id FROM jobs WHERE status = 'assigned' AND pickup_deadline < :now "
            "ORDER BY pickup_deadline, id"
        ),
        {"now": now},
    ).scalars().all()
    for job_id in job_ids:
        drop_unstarted_job(connection, job_id, now)
    return len(job_ids)


def finish_pausing(connection: Connection) -> int:
    """Clear the pausing flag when a workflow has no active jobs."""
    workflows = connection.execute(
        text(
            "UPDATE workflows SET pausing = false WHERE pausing AND NOT EXISTS ("
            "SELECT 1 FROM jobs WHERE jobs.workflow_id = workflows.id "
            "AND jobs.status IN ('assigned', 'running', 'cancelling')) RETURNING id"
        )
    ).all()
    return len(workflows)


def _asset_ids(
    connection: Connection,
    workflow_id: str,
    *,
    status: str,
    step_status: str | None = None,
    due_at: datetime | None = None,
) -> list[str]:
    conditions = ["workflow_id = :workflow_id", "status = :status"]
    params: dict[str, Any] = {"workflow_id": workflow_id, "status": status}
    if step_status is not None:
        conditions.append("step_status = :step_status")
        params["step_status"] = step_status
    if due_at is not None:
        conditions.append("wait_until <= :due_at")
        params["due_at"] = due_at
    query = (
        "SELECT id FROM assets WHERE " + " AND ".join(conditions)
        + " ORDER BY list_position, id LIMIT 500"
    )
    return list(connection.execute(text(query), params).scalars())


def _write_wait_until(connection: Connection, asset_id: str, now: datetime, wait_s: float) -> None:
    connection.execute(
        text(
            "UPDATE assets SET step_status = 'running', wait_until = :wait_until, updated_at = :now "
            "WHERE id = :id AND status = 'in_progress' AND step_status = 'waiting'"
        ),
        {"id": asset_id, "now": now, "wait_until": now + timedelta(seconds=wait_s)},
    )


def advance_assets(connection: Connection, now: datetime) -> int:
    """Enter assets, start engine waits, fail unsupported modules, and route expired waits."""
    workflow_ids = connection.execute(
        text("SELECT id FROM workflows WHERE active AND NOT pausing AND NOT schedule_waiting ORDER BY id")
    ).scalars().all()
    processed = 0
    for workflow_id in workflow_ids:
        graph = load_graph(connection, workflow_id)
        if graph is None:
            continue

        for asset_id in _asset_ids(connection, workflow_id, status="pending"):
            enter_asset(connection, asset_id, graph)
            processed += 1

        waiting_ids = _asset_ids(
            connection, workflow_id, status="in_progress", step_status="waiting"
        )
        for asset_id in waiting_ids:
            asset = connection.execute(
                text("SELECT current_module_id FROM assets WHERE id = :id"), {"id": asset_id}
            ).mappings().one_or_none()
            if asset is None:
                continue
            module = graph.modules.get(asset["current_module_id"])
            if module is None:
                fail_asset(connection, asset_id, "Modul nicht mehr im Workflow")
                processed += 1
            elif module.type_id == "wait":
                wait_s = module.settings.get("wait_s", 60)
                _write_wait_until(connection, asset_id, now, wait_s)
                processed += 1
            elif module.execution != "worker":
                fail_asset(connection, asset_id, f"Modul kann nicht ausgeführt werden: {module.type_id}")
                processed += 1

        due_ids = _asset_ids(
            connection,
            workflow_id,
            status="in_progress",
            step_status="running",
            due_at=now,
        )
        for asset_id in due_ids:
            asset = connection.execute(
                text("SELECT current_module_id FROM assets WHERE id = :id"), {"id": asset_id}
            ).mappings().one_or_none()
            if asset is None:
                continue
            module_id = asset["current_module_id"]
            if module_id not in graph.modules:
                fail_asset(connection, asset_id, "Modul nicht mehr im Workflow")
            else:
                connection.execute(
                    text(
                        "INSERT INTO asset_history (asset_id, kind, text) "
                        "VALUES (:asset_id, 'info', 'Wartezeit abgelaufen')"
                    ),
                    {"asset_id": asset_id},
                )
                route_asset(connection, asset_id, graph, module_id, "success")
            processed += 1
    return processed


def _available_workers(connection: Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            "SELECT w.id, w.name, w.capabilities, w.priorities, w.max_parallel, w.client_slots, "
            "(SELECT count(*) FROM jobs AS j WHERE j.worker_id = w.id "
            "AND j.status IN ('assigned', 'running', 'cancelling')) AS active_jobs "
            "FROM workers AS w WHERE w.status IN ('ready', 'busy') ORDER BY w.id"
        )
    ).mappings()
    workers = []
    for row in rows:
        worker = dict(row)
        worker["active_jobs"] = int(worker["active_jobs"])
        worker["free_slots"] = max(
            0, min(worker["max_parallel"], worker["client_slots"]) - worker["active_jobs"]
        )
        workers.append(worker)
    return workers


def _worker_for_type(workers: list[dict[str, Any]], type_id: str) -> dict[str, Any] | None:
    candidates = []
    for worker in workers:
        if worker["free_slots"] <= 0 or type_id not in (worker["capabilities"] or []):
            continue
        priority = (worker["priorities"] or {}).get(type_id, "normal")
        if priority == "off":
            continue
        candidates.append((
            _priority_rank.get(priority, 1),
            worker["active_jobs"],
            worker["name"],
            worker["id"],
            worker,
        ))
    return min(candidates, default=(None, None, None, None, None))[-1]


def _waiting_assets(connection: Connection, workflow_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            "SELECT id, current_module_id, metadata FROM assets "
            "WHERE workflow_id = :workflow_id AND status = 'in_progress' AND step_status = 'waiting' "
            "ORDER BY list_position, id LIMIT 200 FOR UPDATE SKIP LOCKED"
        ),
        {"workflow_id": workflow_id},
    ).mappings()
    return [dict(row) for row in rows]


def _assign_workflow_asset(
    connection: Connection,
    workflow_id: str,
    graph: Any,
    workers: list[dict[str, Any]],
    now: datetime,
    assignment_index: int,
    pickup_timeout_s: float,
) -> tuple[bool, int]:
    for asset in _waiting_assets(connection, workflow_id):
        module = graph.modules.get(asset["current_module_id"])
        if module is None or module.execution != "worker":
            continue
        worker = _worker_for_type(workers, module.type_id)
        if worker is None:
            continue
        if not asset_file_path(asset["metadata"] or {}):
            fail_asset(connection, asset["id"], "Asset hat keine Datei")
            return False, 1
        create_job(connection, asset["id"], graph, worker["id"], now, pickup_timeout_s)
        worker["free_slots"] -= 1
        worker["active_jobs"] += 1
        connection.execute(
            text("UPDATE workflows SET last_assigned_at = :at WHERE id = :id"),
            {"id": workflow_id, "at": now + timedelta(microseconds=assignment_index)},
        )
        return True, 1
    return False, 0


def assign_jobs(connection: Connection, now: datetime, config: EngineConfig) -> int:
    """Assign waiting assets fairly to workers with matching free capacity."""
    workers = _available_workers(connection)
    processed = 0
    assignment_index = 0
    while True:
        workflows = connection.execute(
            text(
                "SELECT id FROM workflows WHERE active AND NOT pausing AND NOT schedule_waiting "
                "ORDER BY last_assigned_at NULLS FIRST, id FOR UPDATE SKIP LOCKED"
            )
        ).scalars().all()
        assigned_this_round = 0
        for workflow_id in workflows:
            if not any(worker["free_slots"] > 0 for worker in workers):
                return processed
            graph = load_graph(connection, workflow_id)
            if graph is None:
                continue
            assigned, count = _assign_workflow_asset(
                connection, workflow_id, graph, workers, now, assignment_index + 1, config.pickup_timeout_s
            )
            processed += count
            if assigned:
                assignment_index += 1
                assigned_this_round += 1
        if assigned_this_round == 0:
            return processed


def update_worker_busy(connection: Connection) -> int:
    """Set ready and busy workers according to their active job count."""
    changed = connection.execute(
        text(
            "UPDATE workers AS w SET status = CASE WHEN EXISTS ("
            "SELECT 1 FROM jobs WHERE jobs.worker_id = w.id "
            "AND jobs.status IN ('assigned', 'running', 'cancelling')"
            ") THEN 'busy' ELSE 'ready' END "
            "WHERE w.status IN ('ready', 'busy') AND w.status IS DISTINCT FROM CASE WHEN EXISTS ("
            "SELECT 1 FROM jobs WHERE jobs.worker_id = w.id "
            "AND jobs.status IN ('assigned', 'running', 'cancelling')"
            ") THEN 'busy' ELSE 'ready' END RETURNING w.id"
        )
    ).all()
    return len(changed)


def _run_step(engine: Engine, name: str, operation: Callable[[Connection], int]) -> int:
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            acquired = connection.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext('marathon.engine'))")
            ).scalar_one()
            if not acquired:
                return 0
            return operation(connection)
    except Exception as error:
        _logger.warning("Engine-Schritt %s fehlgeschlagen (%s)", name, type(error).__name__)
        return 0


def run_engine_tick(engine: Engine, now: datetime, config: EngineConfig) -> dict[str, int]:
    """Run each engine step in its own bounded transaction."""
    steps: tuple[tuple[str, Callable[[Connection], int]], ...] = (
        ("lost", lambda connection: fail_lost_workers(connection, now, config)),
        ("expired", lambda connection: expire_pickups(connection, now)),
        ("paused", finish_pausing),
        ("advanced", lambda connection: advance_assets(connection, now)),
        ("assigned", lambda connection: assign_jobs(connection, now, config)),
        ("busy", update_worker_busy),
    )
    return {name: _run_step(engine, name, operation) for name, operation in steps}


# RUNNER
class EngineRunner:
    """Run engine ticks on a stoppable daemon thread."""

    def __init__(
        self,
        engine_provider: Callable[[], Engine],
        config: EngineConfig,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine_provider = engine_provider
        self._config = config
        self._clock = clock
        self._stop_event = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._started = False

    def start(self) -> None:
        """Start one daemon thread; repeated calls do not start extra threads."""
        with self._lock:
            if self._started or self._stop_event.is_set():
                return
            self._started = True
            self._thread = Thread(target=self._run, name="marathon-engine", daemon=True)
            self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> bool:
        """Request shutdown and report whether the runner thread has ended."""
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread is None or not thread.is_alive():
            return True
        if thread is current_thread():
            return False
        thread.join(timeout_s)
        return not thread.is_alive()

    def _run(self) -> None:
        while True:
            try:
                run_engine_tick(self._engine_provider(), self._clock(), self._config)
            except Exception as error:
                _logger.warning("Engine-Takt fehlgeschlagen (%s)", type(error).__name__)
            if self._stop_event.wait(self._config.tick_interval_s):
                return

