"""Tests for saved workflow setting issues and blocked activation."""

# IMPORTS
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.modules import load_catalog
from marathon.server.app import create_app
from marathon.server.db.catalog_writes import sync_module_types
from marathon.server.scheduler import run_schedule_tick


# FIXTURES
@pytest.fixture
def api_client(db_engine, tmp_path):
    """Provide a client with the current module catalog installed."""
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with db_engine.begin() as connection:
        sync_module_types(connection, load_catalog())
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        yield client


# FUNC
def _save_watchfolder(api_client, name="Issues", path=""):
    workflow = api_client.post("/api/v1/workflows", json={"name": name}).json()
    response = api_client.put(
        f"/api/v1/workflows/{workflow['id']}/graph",
        json={
            "base_version": 1,
            "modules": [{
                "id": "watch-1",
                "type_id": "watchfolder",
                "name": "Watch",
                "position": {"x": 0, "y": 0},
                "settings": {"path": path},
            }],
            "connections": [],
        },
    )
    return workflow["id"], response


# TESTS
def test_invalid_modules_without_db():
    from marathon.server.db.workflow_checks import invalid_modules

    invalid = {"id": "w", "name": "W", "type_id": "wait", "settings": {"wait_s": 0}}
    valid = {"id": "w", "name": "W", "type_id": "wait", "settings": {"wait_s": 5}}

    assert invalid_modules([invalid]) == [invalid]
    assert invalid_modules([valid]) == []


def test_invalid_settings_are_saved(api_client):
    workflow_id, response = _save_watchfolder(api_client, path="")

    assert response.status_code == 200, response.json()
    detail = api_client.get(f"/api/v1/workflows/{workflow_id}")
    listing = api_client.get("/api/v1/workflows")
    assert detail.json()["issues"] == ["Watch"]
    assert next(item for item in listing.json() if item["id"] == workflow_id)["issues"] == ["Watch"]


def test_start_invalid_returns_409(api_client, db_engine):
    workflow_id, response = _save_watchfolder(api_client, path="")
    assert response.status_code == 200, response.json()

    started = api_client.put(f"/api/v1/workflows/{workflow_id}/active", json={"active": True})

    assert started.status_code == 409
    assert started.json()["detail"] == "Workflow kann nicht gestartet werden. Einstellungen prüfen: Watch"
    with db_engine.connect() as connection:
        active = connection.execute(
            text("SELECT active FROM workflows WHERE id = :id"), {"id": workflow_id}
        ).scalar_one()
    assert active is False


def test_start_valid_still_works(api_client, tmp_path):
    workflow_id, response = _save_watchfolder(api_client, path=str(tmp_path))
    assert response.status_code == 200, response.json()

    started = api_client.put(f"/api/v1/workflows/{workflow_id}/active", json={"active": True})

    assert started.status_code == 200, started.json()
    assert started.json()["active"] is True
    assert started.json()["issues"] == []


def test_save_invalid_pauses_active_workflow(api_client, db_engine, tmp_path):
    workflow_id, response = _save_watchfolder(api_client, path=str(tmp_path))
    assert response.status_code == 200, response.json()
    started = api_client.put(f"/api/v1/workflows/{workflow_id}/active", json={"active": True})
    assert started.status_code == 200, started.json()

    saved = api_client.put(
        f"/api/v1/workflows/{workflow_id}/graph",
        json={
            "base_version": 2,
            "modules": [{
                "id": "watch-1",
                "type_id": "watchfolder",
                "name": "Watch",
                "position": {"x": 0, "y": 0},
                "settings": {"path": ""},
            }],
            "connections": [],
        },
    )

    assert saved.status_code == 200, saved.json()
    with db_engine.connect() as connection:
        state = connection.execute(
            text("SELECT active, scheduled_active FROM workflows WHERE id = :id"),
            {"id": workflow_id},
        ).mappings().one()
        log = connection.execute(
            text("SELECT module_id, level, text FROM log_lines WHERE workflow_id = :id"),
            {"id": workflow_id},
        ).mappings().all()
    assert state["active"] is False
    assert state["scheduled_active"] is False
    assert any(
        row["module_id"] == "watch-1"
        and row["level"] == "warn"
        and row["text"] == "Workflow pausiert: Einstellungen prüfen"
        for row in log
    )


def test_scheduled_start_skips_invalid(api_client, db_engine):
    workflow_id, response = _save_watchfolder(api_client, path="")
    assert response.status_code == 200, response.json()
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET schedule_every = 1, schedule_unit = 'hours', "
                "schedule_start = :start, next_run_at = :due WHERE id = :id"
            ),
            {"id": workflow_id, "start": now - timedelta(hours=2), "due": now - timedelta(minutes=1)},
        )
        result = run_schedule_tick(connection, now)

    assert result["activated"] == []
    with db_engine.connect() as connection:
        state = connection.execute(
            text("SELECT active, next_run_at FROM workflows WHERE id = :id"),
            {"id": workflow_id},
        ).mappings().one()
        logs = connection.execute(
            text("SELECT level, text FROM log_lines WHERE workflow_id = :id"),
            {"id": workflow_id},
        ).mappings().all()
    assert state["active"] is False
    assert state["next_run_at"] > now
    assert any(
        row["level"] == "warn" and row["text"] == "Zeitgesteuerter Start übersprungen: Einstellungen prüfen"
        for row in logs
    )
