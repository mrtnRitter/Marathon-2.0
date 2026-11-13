"""Database tests for watchfolder scan writes."""

# IMPORTS
from datetime import datetime, timedelta, timezone
import os
import re

import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from marathon.modules import load_catalog
from marathon.modules.watchfolder.scan import FileStat
from marathon.server.db.catalog_writes import sync_module_types
from marathon.server.db.watch_writes import (
    WatchClaim,
    apply_scan,
    claim_due_modules,
    record_watch_error,
    sync_watch_modules,
)


# FUNC
def insert_watch_workflow(connection, workflow_id, *, active=True, module_id="watch-1", module_name="Ingest", settings=None):
    spec = next(item for item in load_catalog() if item["id"] == "watchfolder")
    sync_module_types(connection, [spec])
    connection.execute(
        text("INSERT INTO workflows (id, name, active, current_version) VALUES (:id, :name, :active, 1)"),
        {"id": workflow_id, "name": workflow_id, "active": active},
    )
    connection.execute(
        text("INSERT INTO workflow_versions (workflow_id, version) VALUES (:workflow_id, 1)"),
        {"workflow_id": workflow_id},
    )
    statement = text(
        "INSERT INTO workflow_modules "
        "(workflow_id, version, id, type_id, name, position_x, position_y, settings) "
        "VALUES (:workflow_id, 1, :module_id, 'watchfolder', :module_name, 0, 0, :settings)"
    ).bindparams(bindparam("settings", type_=JSONB()))
    connection.execute(
        statement,
        {
            "workflow_id": workflow_id,
            "module_id": module_id,
            "module_name": module_name,
            "settings": settings if settings is not None else {"path": "/mnt/in", "wait_s": 5},
        },
    )


def add_watch_module(db_engine, workflow_id="wf-watch", *, active=True, module_id="watch-1", module_name="Ingest", settings=None):
    with db_engine.begin() as connection:
        insert_watch_workflow(
            connection,
            workflow_id,
            active=active,
            module_id=module_id,
            module_name=module_name,
            settings=settings,
        )


def get_watch_row(connection, workflow_id="wf-watch", module_id="watch-1"):
    return connection.execute(
        text(
            "SELECT next_scan_at, last_error FROM watch_modules "
            "WHERE workflow_id = :workflow_id AND module_id = :module_id"
        ),
        {"workflow_id": workflow_id, "module_id": module_id},
    ).mappings().one_or_none()


# TESTS
def test_sync_watch_modules_tracks_active_modules_and_removes_stale_rows(db_engine):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with db_engine.begin() as connection:
        insert_watch_workflow(connection, "wf-active", active=True)
        insert_watch_workflow(connection, "wf-paused", active=False)
        sync_watch_modules(connection, now)
        rows = connection.execute(text("SELECT workflow_id FROM watch_modules ORDER BY workflow_id")).scalars().all()
        assert rows == ["wf-active"]

        connection.execute(
            text(
                "INSERT INTO watch_modules (workflow_id, module_id, next_scan_at) "
                "VALUES ('wf-paused', 'watch-1', :next_scan_at)"
            ),
            {"next_scan_at": now},
        )
        sync_watch_modules(connection, now)
        assert get_watch_row(connection, "wf-paused") is not None

        connection.execute(
            text("DELETE FROM workflow_modules WHERE workflow_id = 'wf-active' AND version = 1 AND id = 'watch-1'")
        )
        sync_watch_modules(connection, now)
        assert get_watch_row(connection, "wf-active") is None
        assert get_watch_row(connection, "wf-paused") is not None


def test_claim_due_modules_updates_schedule_and_skips_not_due(db_engine):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    add_watch_module(db_engine)
    with db_engine.begin() as connection:
        sync_watch_modules(connection, now)
        claims = claim_due_modules(connection, now)
        assert len(claims) == 1
        claim = claims[0]
        assert isinstance(claim, WatchClaim)
        assert claim.workflow_id == "wf-watch"
        assert claim.module_id == "watch-1"
        assert claim.module_name == "Ingest"
        assert claim.settings == {"path": "/mnt/in", "wait_s": 5}
        assert claim.next_scan_at == now.replace(second=5)
        assert get_watch_row(connection)["next_scan_at"] == claim.next_scan_at
        assert claim_due_modules(connection, now) == []


def test_two_connections_never_claim_the_same_locked_module(db_engine):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    add_watch_module(db_engine)
    with db_engine.begin() as connection:
        sync_watch_modules(connection, now)

    first = db_engine.connect()
    first_transaction = first.begin()
    try:
        assert len(claim_due_modules(first, now)) == 1
        with db_engine.connect() as second:
            with second.begin():
                assert claim_due_modules(second, now) == []
    finally:
        first_transaction.rollback()
        first.close()


def test_invalid_settings_log_once_and_retry_after_sixty_seconds(db_engine):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    add_watch_module(db_engine, settings={"path": ""})
    with db_engine.begin() as connection:
        sync_watch_modules(connection, now)
        assert claim_due_modules(connection, now) == []
        row = get_watch_row(connection)
        assert row["next_scan_at"] == now + timedelta(seconds=60)
        assert row["last_error"] == "Ordner: Ordner fehlt"
        logs = connection.execute(
            text("SELECT level, text FROM log_lines WHERE workflow_id = 'wf-watch' AND module_id = 'watch-1'")
        ).mappings().all()
        assert [(row["level"], row["text"]) for row in logs] == [("error", "Ordner: Ordner fehlt")]

        retry_at = now.replace(minute=1)
        assert claim_due_modules(connection, retry_at) == []
        row = get_watch_row(connection)
        assert row["next_scan_at"] == now.replace(minute=2)
        assert connection.execute(
            text("SELECT COUNT(*) FROM log_lines WHERE workflow_id = 'wf-watch' AND module_id = 'watch-1'")
        ).scalar_one() == 1


def test_apply_waits_for_stability_then_creates_one_asset(db_engine):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    path = "/mnt/in/clip.mxf"
    stat = FileStat(123, 1_700_000_000_123_000_000)
    expected_mtime = datetime.fromtimestamp(stat.mtime_ns / 1_000_000_000, tz=timezone.utc).isoformat()
    add_watch_module(db_engine)

    with db_engine.begin() as connection:
        sync_watch_modules(connection, now)
        first_claim = claim_due_modules(connection, now)[0]
        assert apply_scan(connection, first_claim, {path: stat}, now) == []

    second_time = now.replace(second=5)
    with db_engine.begin() as connection:
        second_claim = claim_due_modules(connection, second_time)[0]
        asset_ids = apply_scan(connection, second_claim, {path: stat}, second_time)
        assert len(asset_ids) == 1
        asset_id = asset_ids[0]
        assert re.fullmatch(r"as-[0-9a-f]{12}", asset_id)

    third_time = now.replace(second=10)
    with db_engine.begin() as connection:
        third_claim = claim_due_modules(connection, third_time)[0]
        assert apply_scan(connection, third_claim, {path: stat}, third_time) == []
        assets = connection.execute(
            text("SELECT id, name, status, current_module_id, step_status, metadata FROM assets WHERE workflow_id = 'wf-watch'")
        ).mappings().all()
        assert len(assets) == 1
        asset = assets[0]
        assert asset["id"] == asset_id
        assert asset["name"] == os.path.basename(path)
        assert asset["status"] == "pending"
        assert asset["current_module_id"] == "watch-1"
        assert asset["step_status"] is None
        assert asset["metadata"] == {"source_path": path, "size": "123", "mtime": expected_mtime}
        history = connection.execute(
            text("SELECT kind, text FROM asset_history WHERE asset_id = :asset_id"), {"asset_id": asset_id}
        ).mappings().all()
        assert [(row["kind"], row["text"]) for row in history] == [
            ("info", f"Vom Watchfolder „Ingest“ übernommen: {path}")
        ]
        logs = connection.execute(
            text("SELECT level, text FROM log_lines WHERE workflow_id = 'wf-watch' AND module_id = 'watch-1' ORDER BY id")
        ).mappings().all()
        assert [(row["level"], row["text"]) for row in logs] == [("info", "Neue Datei: clip.mxf")]


def test_apply_ignores_superseded_claim_without_writes(db_engine):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    add_watch_module(db_engine)
    with db_engine.begin() as connection:
        sync_watch_modules(connection, now)
        claim = claim_due_modules(connection, now)[0]
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE watch_modules SET next_scan_at = :next_scan_at WHERE workflow_id = 'wf-watch' AND module_id = 'watch-1'"),
            {"next_scan_at": now.replace(minute=1)},
        )
        assert apply_scan(connection, claim, {"/mnt/in/new.mxf": FileStat(1, 1)}, now) is None
        assert connection.execute(text("SELECT COUNT(*) FROM assets")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM watch_files")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM log_lines")).scalar_one() == 0


@pytest.mark.parametrize("change", ["pause", "settings"])
def test_apply_ignores_paused_or_reconfigured_workflow(db_engine, change):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    add_watch_module(db_engine)
    with db_engine.begin() as connection:
        sync_watch_modules(connection, now)
        claim = claim_due_modules(connection, now)[0]
    with db_engine.begin() as connection:
        if change == "pause":
            connection.execute(text("UPDATE workflows SET active = false WHERE id = 'wf-watch'"))
        else:
            statement = text(
                "UPDATE workflow_modules SET settings = :settings "
                "WHERE workflow_id = 'wf-watch' AND version = 1 AND id = 'watch-1'"
            ).bindparams(bindparam("settings", type_=JSONB()))
            connection.execute(statement, {"settings": {"path": "/mnt/other", "wait_s": 5}})
        assert apply_scan(connection, claim, {"/mnt/in/new.mxf": FileStat(1, 1)}, now) is None
        assert connection.execute(text("SELECT COUNT(*) FROM assets")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM watch_files")).scalar_one() == 0


def test_watch_error_is_deduplicated_and_cleared_after_success(db_engine):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    add_watch_module(db_engine)
    with db_engine.begin() as connection:
        sync_watch_modules(connection, now)
        claim = claim_due_modules(connection, now)[0]
        assert record_watch_error(connection, "wf-watch", "watch-1", "Netzwerkfehler") is True
        assert record_watch_error(connection, "wf-watch", "watch-1", "Netzwerkfehler") is False
        assert connection.execute(
            text("SELECT COUNT(*) FROM log_lines WHERE workflow_id = 'wf-watch' AND module_id = 'watch-1'")
        ).scalar_one() == 1
        assert apply_scan(connection, claim, {}, now) == []
        row = get_watch_row(connection)
        assert row["last_error"] is None
        logs = connection.execute(
            text("SELECT level, text FROM log_lines WHERE workflow_id = 'wf-watch' AND module_id = 'watch-1' ORDER BY id")
        ).mappings().all()
        assert [(row["level"], row["text"]) for row in logs] == [
            ("warn", "Netzwerkfehler"),
            ("info", "Ordner wieder erreichbar: /mnt/in"),
        ]
