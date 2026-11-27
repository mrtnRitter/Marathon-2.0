"""Integration test for scheduled watchfolder pause transitions."""

# IMPORTS
from datetime import datetime, timedelta, timezone
from threading import Event
from uuid import uuid4

from sqlalchemy import text

from marathon.modules import load_catalog
from marathon.server.db.catalog_writes import sync_module_types
from marathon.server.db.macro_writes import ensure_standard_macros
from marathon.server.db.workflow_writes import create_workflow, save_graph
from marathon.server.scheduler import run_schedule_tick
from marathon.server.watcher import run_watch_tick


# TESTS
def test_failed_watch_scan_completes_scheduled_run_and_returns_to_waiting(db_engine, tmp_path):
    now = datetime.now(timezone.utc)
    settings = {
        "path": str(tmp_path / "missing-folder"),
        "include": "",
        "exclude": "",
        "wait_s": 5,
        "recursive": False,
    }
    module = {
        "id": "watch-1",
        "type_id": "watchfolder",
        "name": "Watchfolder",
        "description": "",
        "position": {"x": 0, "y": 0},
        "settings": settings,
        "locked": False,
    }

    with db_engine.begin() as connection:
        sync_module_types(connection, load_catalog())
        ensure_standard_macros(connection)
        workflow_id = create_workflow(connection, f"Schedule pause {uuid4().hex[:8]}", "")
        save_graph(connection, workflow_id, 1, [module], [])
        connection.execute(
            text(
                "UPDATE workflows SET active = true, schedule_every = 1, schedule_unit = 'hours', "
                "schedule_start = :schedule_start, next_run_at = :next_run_at, schedule_waiting = true "
                "WHERE id = :id"
            ),
            {
                "id": workflow_id,
                "schedule_start": now - timedelta(hours=2),
                "next_run_at": now - timedelta(seconds=1),
            },
        )

    with db_engine.begin() as connection:
        started = run_schedule_tick(connection, now)
    assert started == {"started": [workflow_id], "waiting": []}

    scan = run_watch_tick(db_engine, lambda: datetime.now(timezone.utc), Event())
    assert scan["claimed"] == 1
    assert scan["errors"] == 1
    assert scan["assets"] == 0
    assert scan["stale"] == 0
    with db_engine.connect() as connection:
        watch = connection.execute(
            text("SELECT last_scan_at, last_error FROM watch_modules WHERE workflow_id = :id AND module_id = 'watch-1.main'"),
            {"id": workflow_id},
        ).mappings().one()
        assert watch["last_scan_at"] is not None
        assert watch["last_error"] is not None

    with db_engine.begin() as connection:
        finished = run_schedule_tick(connection, datetime.now(timezone.utc) + timedelta(seconds=1))
    assert finished == {"started": [], "waiting": [workflow_id]}


def test_scheduled_run_waits_for_stable_watchfolder_asset_and_completion(db_engine, tmp_path):
    from marathon.server.config import EngineConfig
    from marathon.server.engine.tick import run_engine_tick

    base = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    run_started_at = base + timedelta(seconds=2)
    first_scan_at = base
    stable_scan_at = base + timedelta(seconds=5)
    folder = tmp_path / "incoming"
    folder.mkdir()
    source_file = folder / "clip.mov"
    source_file.write_bytes(b"unfinished")
    settings = {
        "path": str(folder),
        "include": "",
        "exclude": "",
        "wait_s": 5,
        "recursive": False,
    }
    module = {
        "id": "watch-stable",
        "type_id": "watchfolder",
        "name": "Watchfolder",
        "description": "",
        "position": {"x": 0, "y": 0},
        "settings": settings,
        "locked": False,
    }

    with db_engine.begin() as connection:
        sync_module_types(connection, load_catalog())
        ensure_standard_macros(connection)
        workflow_id = create_workflow(connection, f"Stable schedule {uuid4().hex[:8]}", "")
        save_graph(connection, workflow_id, 1, [module], [])
        connection.execute(
            text(
                "UPDATE workflows SET active = true, schedule_every = 1, schedule_unit = 'hours', "
                "schedule_start = :schedule_start, next_run_at = :next_run_at, schedule_waiting = true "
                "WHERE id = :id"
            ),
            {
                "id": workflow_id,
                "schedule_start": run_started_at - timedelta(hours=2),
                "next_run_at": run_started_at - timedelta(seconds=1),
            },
        )

    assert run_watch_tick(db_engine, lambda: base - timedelta(seconds=1), Event())["claimed"] == 0
    with db_engine.begin() as connection:
        started = run_schedule_tick(connection, run_started_at)
    assert started == {"started": [workflow_id], "waiting": []}

    first_scan = run_watch_tick(db_engine, lambda: first_scan_at, Event())
    assert first_scan == {"claimed": 1, "assets": 0, "errors": 0, "stale": 0}
    with db_engine.connect() as connection:
        watched_file = connection.execute(
            text(
                "SELECT stable_since, asset_id FROM watch_files "
                "WHERE workflow_id = :workflow_id AND module_id = 'watch-stable.main'"
            ),
            {"workflow_id": workflow_id},
        ).mappings().one()
        assert watched_file["stable_since"] == first_scan_at
        assert watched_file["asset_id"] is None
    with db_engine.begin() as connection:
        before_stable = run_schedule_tick(connection, run_started_at + timedelta(seconds=1))
    assert before_stable == {"started": [], "waiting": []}

    second_scan = run_watch_tick(db_engine, lambda: stable_scan_at, Event())
    assert second_scan == {"claimed": 1, "assets": 1, "errors": 0, "stale": 0}
    with db_engine.connect() as connection:
        asset = connection.execute(
            text("SELECT id, status FROM assets WHERE workflow_id = :workflow_id"),
            {"workflow_id": workflow_id},
        ).mappings().one()
        assert asset["status"] == "pending"
    with db_engine.begin() as connection:
        asset_pending = run_schedule_tick(connection, stable_scan_at + timedelta(seconds=1))
    assert asset_pending == {"started": [], "waiting": []}

    run_engine_tick(db_engine, stable_scan_at + timedelta(seconds=1), EngineConfig())
    with db_engine.connect() as connection:
        assert connection.execute(
            text("SELECT status FROM assets WHERE id = :id"), {"id": asset["id"]}
        ).scalar_one() == "done"
    with db_engine.begin() as connection:
        completed = run_schedule_tick(connection, stable_scan_at + timedelta(seconds=2))
    assert completed == {"started": [], "waiting": [workflow_id]}
