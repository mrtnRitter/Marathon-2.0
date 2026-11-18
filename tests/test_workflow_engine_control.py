"""Workflow engine control API tests backed by isolated PostgreSQL."""

# IMPORTS
from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.server.app import create_app


# TEST HELPERS
@pytest.fixture
def api_client(db_engine, tmp_path):
    """Provide an API client backed by the isolated test database."""
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with db_engine.begin() as connection:
        module_types = (
            ("source", "Quelle", False, "none", [{"id": "success"}]),
            ("step", "Schritt", True, "worker", [{"id": "success"}, {"id": "error"}]),
            ("wait", "Warten", True, "engine", [{"id": "success"}]),
        )
        for type_id, name, has_input, execution, outputs in module_types:
            connection.execute(
                text(
                    "INSERT INTO module_types (id, name, category, icon, has_input, execution, outputs) "
                    "VALUES (:id, :name, 'test', 'test', :has_input, :execution, CAST(:outputs AS jsonb))"
                ),
                {
                    "id": type_id,
                    "name": name,
                    "has_input": has_input,
                    "execution": execution,
                    "outputs": json.dumps(outputs),
                },
            )
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        yield client


def _create_workflow(api_client, name: str = "Engine") -> dict:
    response = api_client.post("/api/v1/workflows", json={"name": name})
    assert response.status_code == 201
    return response.json()


def _module(module_id: str, type_id: str = "step", name: str | None = None, settings: dict | None = None) -> dict:
    return {
        "id": module_id,
        "type_id": type_id,
        "name": name or module_id,
        "position": {"x": 0, "y": 0},
        "settings": settings or {},
    }


def _save_graph(api_client, workflow_id: str, base_version: int, modules: list[dict], connections: list[dict] | None = None):
    response = api_client.put(
        f"/api/v1/workflows/{workflow_id}/graph",
        json={"base_version": base_version, "modules": modules, "connections": connections or []},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _insert_worker_asset_job(db_engine, workflow_id: str, module_id: str, *, status: str = "running") -> None:
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO workers (id, name, status) VALUES ('worker-1', 'Worker', 'busy')")
        )
        connection.execute(
            text(
                "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status) "
                "VALUES ('asset-1', :workflow_id, 'Asset', 'in_progress', :module_id, 'running')"
            ),
            {"workflow_id": workflow_id, "module_id": module_id},
        )
        connection.execute(
            text(
                "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id) "
                "VALUES ('job-1', :workflow_id, :module_id, :status, 'worker-1')"
            ),
            {"workflow_id": workflow_id, "module_id": module_id, "status": status},
        )
        connection.execute(text("INSERT INTO job_assets (job_id, asset_id) VALUES ('job-1', 'asset-1')"))


# TESTS
def test_pause_with_running_job_sets_pausing(api_client, db_engine):
    workflow = _create_workflow(api_client)
    _save_graph(api_client, workflow["id"], 1, [_module("m1")])
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE workflows SET active = true WHERE id = :id"), {"id": workflow["id"]})
    _insert_worker_asset_job(db_engine, workflow["id"], "m1")

    response = api_client.put(f"/api/v1/workflows/{workflow['id']}/active", json={"active": False})

    assert response.status_code == 200
    assert response.json()["active"] is False
    assert response.json()["pausing"] is True
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT status FROM jobs WHERE id = 'job-1'")).scalar_one() == "cancelling"


def test_pause_without_jobs_is_immediate(api_client):
    workflow = _create_workflow(api_client)

    response = api_client.put(f"/api/v1/workflows/{workflow['id']}/active", json={"active": False})

    assert response.status_code == 200
    assert response.json()["active"] is False
    assert response.json()["pausing"] is False


def test_start_while_pausing_returns_409(api_client, db_engine):
    workflow = _create_workflow(api_client)
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE workflows SET pausing = true WHERE id = :id"), {"id": workflow["id"]})

    response = api_client.put(f"/api/v1/workflows/{workflow['id']}/active", json={"active": True})

    assert response.status_code == 409
    assert response.json() == {"detail": "Workflow wird gerade pausiert"}


def test_workflow_records_have_pausing(api_client):
    workflow = _create_workflow(api_client)

    listed = api_client.get("/api/v1/workflows")
    detail = api_client.get(f"/api/v1/workflows/{workflow['id']}")

    assert listed.status_code == 200
    assert next(item for item in listed.json() if item["id"] == workflow["id"])["pausing"] is False
    assert detail.status_code == 200
    assert detail.json()["pausing"] is False


def test_save_graph_reroutes_assets_of_removed_module(api_client, db_engine):
    workflow = _create_workflow(api_client)
    initial_connections = [{"source_module_id": "m1", "source_output_id": "success", "target_module_id": "m2"}]
    _save_graph(api_client, workflow["id"], 1, [_module("m1", "source"), _module("m2")], initial_connections)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, "
                "from_module_id, from_output_id, wait_until, attempts) "
                "VALUES ('asset-1', :workflow_id, 'Asset', 'in_progress', 'm2', 'waiting', "
                "'m1', 'success', :wait_until, 3)"
            ),
            {"workflow_id": workflow["id"], "wait_until": datetime.now(timezone.utc) + timedelta(hours=1)},
        )

    new_connections = [{"source_module_id": "m1", "source_output_id": "success", "target_module_id": "m3"}]
    _save_graph(api_client, workflow["id"], 2, [_module("m1", "source"), _module("m3")], new_connections)

    with db_engine.connect() as connection:
        asset = connection.execute(
            text("SELECT current_module_id, step_status, wait_until FROM assets WHERE id = 'asset-1'")
        ).one()
    assert tuple(asset) == ("m3", "waiting", None)


def test_save_invalid_active_workflow_pauses(api_client, db_engine):
    workflow = _create_workflow(api_client)
    _save_graph(api_client, workflow["id"], 1, [_module("m1", "wait", settings={"wait_s": 1})])
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE workflows SET active = true WHERE id = :id"), {"id": workflow["id"]})
    _insert_worker_asset_job(db_engine, workflow["id"], "m1")

    response = api_client.put(
        f"/api/v1/workflows/{workflow['id']}/graph",
        json={"base_version": 2, "modules": [_module("m1", "wait", settings={"wait_s": 0})], "connections": []},
    )

    assert response.status_code == 200
    detail = api_client.get(f"/api/v1/workflows/{workflow['id']}").json()
    assert detail["active"] is False
    assert detail["pausing"] is True
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT status FROM jobs WHERE id = 'job-1'")).scalar_one() == "cancelling"
        assert connection.execute(
            text("SELECT COUNT(*) FROM log_lines WHERE workflow_id = :id AND text = 'Workflow pausiert: Einstellungen prüfen'"),
            {"id": workflow["id"]},
        ).scalar_one() == 1


def test_graph_impact_endpoint(api_client, db_engine):
    workflow = _create_workflow(api_client)
    connections = [{"source_module_id": "m1", "source_output_id": "success", "target_module_id": "m2"}]
    _save_graph(api_client, workflow["id"], 1, [_module("m1", "source"), _module("m2", name="Prüfen")], connections)
    _insert_worker_asset_job(db_engine, workflow["id"], "m2", status="running")
    body = {"base_version": 2, "modules": [_module("m1", "source")], "connections": []}

    response = api_client.post(f"/api/v1/workflows/{workflow['id']}/graph/impact", json=body)

    assert response.status_code == 200
    assert response.json() == {
        "removed_modules": [{"id": "m2", "name": "Prüfen", "assets": 1, "jobs": 1}],
        "assets": 1,
        "jobs": 1,
        "outcomes": {"rerouted": 0, "done": 0, "failed": 1},
    }
    missing = api_client.post("/api/v1/workflows/nope/graph/impact", json=body)
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Workflow nicht gefunden"}


def test_reprocess_resets_engine_fields(api_client, db_engine):
    workflow = _create_workflow(api_client)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO assets (id, workflow_id, name, status, current_module_id, from_module_id, "
                "from_output_id, wait_until, attempts) "
                "VALUES ('asset-1', :workflow_id, 'Asset', 'failed', 'm1', 'm0', 'success', :wait_until, 4)"
            ),
            {"workflow_id": workflow["id"], "wait_until": datetime.now(timezone.utc) + timedelta(hours=1)},
        )

    response = api_client.post("/api/v1/assets/reprocess", json={"asset_ids": ["asset-1"]})

    assert response.status_code == 200
    with db_engine.connect() as connection:
        engine_fields = connection.execute(
            text("SELECT from_module_id, from_output_id, wait_until, attempts FROM assets WHERE id = 'asset-1'")
        ).one()
    assert tuple(engine_fields) == (None, None, None, 0)
