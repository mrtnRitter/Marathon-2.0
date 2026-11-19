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
    apply_asset_result,
    create_batch_job,
    create_engine_job,
    drop_unstarted_job,
    interrupt_job,
)


# CONFIG
_logger = logging.getLogger("marathon.engine")


# FUNC
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fail_lost_workers(connection: Connection, now: datetime, config: EngineConfig) -> int:
    """Interrupt jobs of silent workers and mark them offline without changing status."""
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
            "UPDATE workers SET online = false WHERE online "
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


def finish_worker_stops(connection: Connection) -> int:
    """Stop workers once they have no active jobs."""
    workers = connection.execute(
        text(
            "UPDATE workers SET status = 'stopped' WHERE status = 'stopping' AND NOT EXISTS ("
            "SELECT 1 FROM jobs WHERE jobs.worker_id = workers.id "
            "AND jobs.status IN ('assigned', 'running', 'cancelling')) RETURNING id"
        )
    ).all()
    return len(workers)


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


def advance_assets(
    connection: Connection,
    now: datetime,
    config: EngineConfig | None = None,
) -> int:
    """Enter assets, start engine waits, fail unsupported modules, and route expired waits."""
    config = config or EngineConfig()
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

        waiting_ids = _asset_ids(connection, workflow_id, status="in_progress", step_status="waiting")
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
                try:
                    create_engine_job(connection, asset_id, graph, now)
                except (ValueError, LookupError):
                    continue
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
                processed += 1
                continue

            job_id = connection.execute(
                text(
                    "SELECT j.id FROM jobs AS j JOIN job_assets AS ja ON ja.job_id = j.id "
                    "WHERE ja.asset_id = :asset_id AND j.worker_id IS NULL "
                    "AND j.status IN ('assigned', 'running', 'cancelling') ORDER BY j.created_at, j.id LIMIT 1"
                ),
                {"asset_id": asset_id},
            ).scalar_one_or_none()
            if job_id is not None:
                try:
                    applied = apply_asset_result(
                        connection,
                        job_id,
                        asset_id,
                        None,
                        {"outcome": "result", "output": "success", "log": ["Wartezeit abgelaufen"]},
                        now,
                        config.max_attempts,
                    )
                except (ValueError, LookupError):
                    continue
                processed += int(applied)
                continue

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
            "SELECT w.id, w.name, w.skills, w.capabilities, "
            "(SELECT count(*) FROM jobs AS j WHERE j.worker_id = w.id "
            "AND j.status IN ('assigned', 'running', 'cancelling')) AS active_jobs "
            "FROM workers AS w WHERE w.status = 'ready' AND w.online ORDER BY w.id"
        )
    ).mappings()
    workers = []
    for row in rows:
        worker = dict(row)
        worker["active_jobs"] = int(worker["active_jobs"])
        workers.append(worker)
    return workers


def _worker_for_type(
    workers: list[dict[str, Any]],
    type_id: str,
    assigned_worker_ids: set[str],
) -> dict[str, Any] | None:
    candidates = []
    for worker in workers:
        if worker["active_jobs"] or worker["id"] in assigned_worker_ids:
            continue
        capabilities = worker["capabilities"] or []
        ranks = {}
        for index, skill in enumerate(worker["skills"] or []):
            if skill in capabilities:
                ranks.setdefault(skill, index)
        if type_id not in ranks:
            continue
        candidates.append((ranks[type_id], worker["name"], worker["id"], worker))
    return min(candidates, default=(None, None, None, None))[-1]


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


def _parallel_size(module: Any) -> int:
    value = module.settings.get("parallel")
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 16 else 1


def _assign_workflow_asset(
    connection: Connection,
    workflow_id: str,
    graph: Any,
    workers: list[dict[str, Any]],
    assigned_worker_ids: set[str],
    now: datetime,
    assignment_index: int,
    pickup_timeout_s: float,
) -> tuple[bool, int]:
    assets = _waiting_assets(connection, workflow_id)
    processed = 0
    for index, asset in enumerate(assets):
        module = graph.modules.get(asset["current_module_id"])
        if module is None or module.execution != "worker":
            continue
        if not asset_file_path(asset["metadata"] or {}):
            fail_asset(connection, asset["id"], "Asset hat keine Datei")
            processed += 1
            continue
        worker = _worker_for_type(workers, module.type_id, assigned_worker_ids)
        if worker is None:
            continue

        batch_ids = []
        for candidate in assets[index:]:
            if candidate["current_module_id"] != module.id:
                continue
            if not asset_file_path(candidate["metadata"] or {}):
                fail_asset(connection, candidate["id"], "Asset hat keine Datei")
                processed += 1
                continue
            batch_ids.append(candidate["id"])
            if len(batch_ids) == _parallel_size(module):
                break
        if not batch_ids:
            continue
        try:
            create_batch_job(connection, batch_ids, graph, worker["id"], now, pickup_timeout_s)
        except (ValueError, LookupError):
            continue
        assigned_worker_ids.add(worker["id"])
        connection.execute(
            text("UPDATE workflows SET last_assigned_at = :at WHERE id = :id"),
            {"id": workflow_id, "at": now + timedelta(microseconds=assignment_index)},
        )
        return True, processed + len(batch_ids)
    return False, processed


def assign_jobs(connection: Connection, now: datetime, config: EngineConfig) -> int:
    """Assign bundles fairly to ready online workers, at most one job per worker per tick."""
    workers = _available_workers(connection)
    processed = 0
    assignment_index = 0
    assigned_worker_ids: set[str] = set()
    while True:
        workflows = connection.execute(
            text(
                "SELECT id FROM workflows WHERE active AND NOT pausing AND NOT schedule_waiting "
                "ORDER BY last_assigned_at NULLS FIRST, id FOR UPDATE SKIP LOCKED"
            )
        ).scalars().all()
        assigned_this_round = 0
        for workflow_id in workflows:
            graph = load_graph(connection, workflow_id)
            if graph is None:
                continue
            assigned, count = _assign_workflow_asset(
                connection,
                workflow_id,
                graph,
                workers,
                assigned_worker_ids,
                now,
                assignment_index + 1,
                config.pickup_timeout_s,
            )
            processed += count
            if assigned:
                assignment_index += 1
                assigned_this_round += 1
        if assigned_this_round == 0:
            return processed


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
        ("stopped", finish_worker_stops),
        ("advanced", lambda connection: advance_assets(connection, now, config)),
        ("assigned", lambda connection: assign_jobs(connection, now, config)),
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

