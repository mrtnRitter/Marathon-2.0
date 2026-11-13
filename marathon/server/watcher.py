"""Run persistent watchfolder scans on a stoppable background thread."""

# IMPORTS
from collections.abc import Callable
from datetime import datetime, timezone
import logging
from threading import Event, Lock, Thread, current_thread

from sqlalchemy.engine import Engine

from marathon.modules.watchfolder.rules import read_settings
from marathon.modules.watchfolder.scan import FolderUnavailable, ScanStopped, list_folder
from marathon.server.db.watch_writes import (
    apply_scan,
    claim_due_modules,
    record_watch_error,
    sync_watch_modules,
)


# CONFIG
tick_interval_s = 5.0
listing_timeout_s = 60.0
_logger = logging.getLogger("marathon.watcher")


# FUNC
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Watchfolder-Zeitstempel müssen timezone-aware sein.")
    return value.astimezone(timezone.utc)


class _StopRequested(Exception):
    pass


def run_watch_tick(engine: Engine, clock: Callable[[], datetime], stop: Event) -> dict[str, int]:
    """Claim due modules and scan each one independently."""
    now = _as_utc(clock())
    with engine.begin() as connection:
        sync_watch_modules(connection, now)
        claims = claim_due_modules(connection, now)

    result = {"claimed": len(claims), "assets": 0, "errors": 0, "stale": 0}
    for claim in claims:
        if stop.is_set():
            break
        try:
            listing = list_folder(read_settings(claim.settings), listing_timeout_s, stop)
            if stop.is_set():
                break
            scanned_at = _as_utc(clock())
            with engine.begin() as connection:
                if stop.is_set():
                    break
                asset_ids = apply_scan(connection, claim, listing, scanned_at)
                if stop.is_set():
                    raise _StopRequested()
            if asset_ids is None:
                result["stale"] += 1
            else:
                result["assets"] += len(asset_ids)
        except ScanStopped:
            break
        except FolderUnavailable as error:
            if stop.is_set():
                break
            try:
                with engine.begin() as connection:
                    record_watch_error(connection, claim.workflow_id, claim.module_id, str(error))
            except Exception as record_error:
                _logger.warning("Watchfolder-Fehler konnte nicht gespeichert werden (%s)", type(record_error).__name__)
            result["errors"] += 1
        except _StopRequested:
            break
        except Exception as error:
            _logger.warning("Watchfolder-Modul fehlgeschlagen (%s)", type(error).__name__)
            result["errors"] += 1
    return result


class WatchRunner:
    """Run watchfolder ticks on a stoppable daemon thread."""

    def __init__(
        self,
        engine_provider: Callable[[], Engine],
        interval_s: float = tick_interval_s,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("Das Watch-Intervall muss größer als 0 sein.")
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
            self._thread = Thread(target=self._run, name="marathon-watcher", daemon=True)
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
        while not self._stop_event.is_set():
            try:
                run_watch_tick(self._engine_provider(), self._clock, self._stop_event)
            except Exception as error:
                _logger.warning("Watchfolder-Prüfung fehlgeschlagen (%s)", type(error).__name__)
            if self._stop_event.wait(self._interval_s):
                return
