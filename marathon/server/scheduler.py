"""Run scheduled workflow activation and automatic pause checks."""

# IMPORTS
from collections.abc import Callable
from datetime import datetime, timezone, tzinfo
import logging
from threading import Event, Lock, Thread, current_thread

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from marathon.server.db.workflow_checks import workflow_invalid_modules
from marathon.server.schedule import ScheduleError, next_occurrence


# CONFIG
tick_interval_s = 15.0
_logger = logging.getLogger("marathon.scheduler")


# FUNC
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_schedule_tick(connection: Connection, now: datetime, tz: tzinfo | None = None) -> dict[str, list[str]]:
    """Advance due schedules and move completed scheduled runs into waiting."""
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '10s'"))
    due_rows = connection.execute(
        text(
            "SELECT id, active, pausing, schedule_waiting, schedule_every, schedule_unit, schedule_start "
            "FROM workflows "
            "WHERE next_run_at IS NOT NULL AND next_run_at <= :now "
            "ORDER BY id FOR UPDATE SKIP LOCKED"
        ),
        {"now": now},
    ).mappings().all()
    started: list[str] = []

    for workflow in due_rows:
        workflow_id = workflow["id"]
        try:
            next_run_at = next_occurrence(
                workflow["schedule_start"],
                workflow["schedule_every"],
                workflow["schedule_unit"],
                after=now,
                tz=tz,
            )
        except ScheduleError as error:
            connection.execute(
                text(
                    "UPDATE workflows SET schedule_every = NULL, schedule_unit = NULL, "
                    "schedule_start = NULL, next_run_at = NULL, schedule_waiting = false, "
                    "run_started_at = NULL WHERE id = :id"
                ),
                {"id": workflow_id},
            )
            _logger.warning("Ungültiger Zeitplan für Workflow %s wird geleert: %s", workflow_id, error)
            continue

        connection.execute(
            text("UPDATE workflows SET next_run_at = :next_run_at WHERE id = :id"),
            {"id": workflow_id, "next_run_at": next_run_at},
        )
        if not workflow["active"] or workflow["pausing"] or not workflow["schedule_waiting"]:
            continue

        invalid = workflow_invalid_modules(connection, workflow_id)
        if invalid:
            for module in invalid:
                connection.execute(
                    text(
                        "INSERT INTO log_lines (workflow_id, module_id, job_id, level, text) "
                        "VALUES (:workflow_id, :module_id, NULL, 'warn', 'Zeitgesteuerter Lauf übersprungen: Einstellungen prüfen')"
                    ),
                    {"workflow_id": workflow_id, "module_id": module["id"]},
                )
            continue

        connection.execute(
            text(
                "UPDATE workflows SET schedule_waiting = false, run_started_at = :now, "
                "updated_at = now() WHERE id = :id"
            ),
            {"id": workflow_id, "now": now},
        )
        started.append(workflow_id)

    waiting_rows = connection.execute(
        text(
            "UPDATE workflows SET schedule_waiting = true, updated_at = now() "
            "WHERE active AND NOT pausing AND NOT schedule_waiting AND schedule_every IS NOT NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM assets AS asset WHERE asset.workflow_id = workflows.id "
            "AND asset.status IN ('pending', 'in_progress')) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM watch_modules AS watched "
            "WHERE watched.workflow_id = workflows.id "
            "AND (watched.last_scan_at IS NULL "
            "OR watched.last_scan_at < COALESCE(workflows.run_started_at, '-infinity'::timestamptz))) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM workflows AS workflow "
            "JOIN workflow_modules AS module "
            "ON module.workflow_id = workflow.id AND module.version = workflow.current_version "
            "LEFT JOIN watch_modules AS watched "
            "ON watched.workflow_id = workflow.id AND watched.module_id = module.id "
            "WHERE workflow.id = workflows.id AND module.type_id = 'watchfolder' "
            "AND (watched.module_id IS NULL OR watched.last_scan_at IS NULL "
            "OR watched.last_scan_at < COALESCE(workflows.run_started_at, '-infinity'::timestamptz))) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM watch_files AS watched_file "
            "WHERE watched_file.workflow_id = workflows.id "
            "AND watched_file.asset_id IS NULL AND watched_file.missing_since IS NULL) "
            "RETURNING id"
        )
    ).all()
    return {"started": sorted(started), "waiting": sorted(row[0] for row in waiting_rows)}

class ScheduleRunner:
    """Run schedule ticks on a stoppable daemon thread."""

    def __init__(
        self,
        engine_provider: Callable[[], Engine],
        interval_s: float = tick_interval_s,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine_provider = engine_provider
        self._interval_s = interval_s
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
            self._thread = Thread(target=self._run, name="marathon-scheduler", daemon=True)
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
                engine = self._engine_provider()
                with engine.begin() as connection:
                    run_schedule_tick(connection, self._clock())
            except Exception as error:
                _logger.warning("Zeitplan-Prüfung fehlgeschlagen (%s)", type(error).__name__)
            if self._stop_event.wait(self._interval_s):
                return
