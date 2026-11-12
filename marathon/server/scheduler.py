"""Run scheduled workflow activation and automatic pause checks."""

# IMPORTS
from collections.abc import Callable
from datetime import datetime, timezone, tzinfo
import logging
from threading import Event, Lock, Thread, current_thread

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from marathon.server.schedule import ScheduleError, next_occurrence


# CONFIG
tick_interval_s = 15.0
_logger = logging.getLogger("marathon.scheduler")


# FUNC
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_schedule_tick(connection: Connection, now: datetime, tz: tzinfo | None = None) -> dict[str, list[str]]:
    """Advance due schedules and pause scheduled workflows without open assets."""
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '10s'"))
    due_rows = connection.execute(
        text(
            "SELECT id, active, schedule_every, schedule_unit, schedule_start "
            "FROM workflows "
            "WHERE next_run_at IS NOT NULL AND next_run_at <= :now "
            "ORDER BY id FOR UPDATE SKIP LOCKED"
        ),
        {"now": now},
    ).mappings().all()
    activated: list[str] = []

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
                    "schedule_start = NULL, next_run_at = NULL, scheduled_active = false "
                    "WHERE id = :id"
                ),
                {"id": workflow_id},
            )
            _logger.warning("Ungültiger Zeitplan für Workflow %s wird geleert: %s", workflow_id, error)
            continue

        if workflow["active"]:
            connection.execute(
                text("UPDATE workflows SET next_run_at = :next_run_at WHERE id = :id"),
                {"id": workflow_id, "next_run_at": next_run_at},
            )
        else:
            connection.execute(
                text(
                    "UPDATE workflows SET active = true, scheduled_active = true, "
                    "next_run_at = :next_run_at, updated_at = now() WHERE id = :id"
                ),
                {"id": workflow_id, "next_run_at": next_run_at},
            )
            activated.append(workflow_id)

    paused_rows = connection.execute(
        text(
            "UPDATE workflows SET active = false, scheduled_active = false, updated_at = now() "
            "WHERE active AND scheduled_active AND NOT EXISTS ("
            "SELECT 1 FROM assets WHERE assets.workflow_id = workflows.id "
            "AND assets.status IN ('pending', 'in_progress')) RETURNING id"
        )
    ).all()
    return {"activated": sorted(activated), "paused": sorted(row[0] for row in paused_rows)}


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
