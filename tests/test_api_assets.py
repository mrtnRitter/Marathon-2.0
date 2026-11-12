"""Asset reprocessing API tests backed by the local PostgreSQL fixture."""

from datetime import datetime, timezone
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.server.app import create_app
from marathon.server.db.engine import create_db_engine
from marathon.server.config import DatabaseConfig
from records import insert_records


fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.fixture
def api_client(db_engine, tmp_path):
    """Insert test records and provide a client with a UI fallback."""
    insert_records(db_engine, fixed_now)
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        yield client


def _asset(db_engine, asset_id):
    with db_engine.connect() as connection:
        return dict(connection.execute(
            text("SELECT id, workflow_id, name, status, current_module_id, step_status, list_position, metadata "
                 "FROM assets WHERE id = :id"),
            {"id": asset_id},
        ).mappings().one())


def _history(db_engine, asset_id):
    with db_engine.connect() as connection:
        return [dict(row) for row in connection.execute(
            text("SELECT kind, text FROM asset_history WHERE asset_id = :id ORDER BY id"),
            {"id": asset_id},
        ).mappings()]


def test_reprocess_same_workflow(api_client, db_engine):
    response = api_client.post("/api/v1/assets/reprocess", json={"asset_ids": ["a-failed"]})

    assert response.status_code == 200
    assert response.json() == {"asset_ids": ["a-failed"]}
    asset = _asset(db_engine, "a-failed")
    assert (asset["status"], asset["current_module_id"], asset["step_status"]) == ("pending", None, None)
    assert _history(db_engine, "a-failed")[-1] == {
        "kind": "info", "text": "Erneut verarbeiten: beginnt den Workflow von vorn"
    }


def test_reprocess_into_other_workflow(api_client, db_engine):
    response = api_client.post(
        "/api/v1/assets/reprocess",
        json={"asset_ids": ["a-failed"], "target_workflow_id": "wf-b"},
    )

    assert response.status_code == 200
    new_id = response.json()["asset_ids"][0]
    assert re.fullmatch(r"as-[0-9a-f]{12}", new_id)
    old_asset = _asset(db_engine, "a-failed")
    new_asset = _asset(db_engine, new_id)
    assert (old_asset["status"], old_asset["step_status"]) == ("done", None)
    assert _history(db_engine, "a-failed")[-1] == {
        "kind": "info", "text": f"Workflow gewechselt nach „Beta“ (neues Asset {new_id})"
    }
    assert (new_asset["workflow_id"], new_asset["name"], new_asset["status"], new_asset["list_position"]) == (
        "wf-b", "a-failed", "pending", 3
    )
    assert new_asset["metadata"] == old_asset["metadata"]
    assert _history(db_engine, new_id) == [{
        "kind": "info", "text": "Kommt aus Workflow „Alpha“ (Asset a-failed)"
    }]
    listed = api_client.get("/api/v1/assets", params={"workflow_id": "wf-b"})
    assert listed.status_code == 200
    assert any(asset["id"] == new_id for asset in listed.json())


def test_two_transfers_get_consecutive_positions(api_client, db_engine):
    response = api_client.post(
        "/api/v1/assets/reprocess",
        json={"asset_ids": ["a-failed", "a-done"], "target_workflow_id": "wf-b"},
    )

    assert response.status_code == 200
    assert len(response.json()["asset_ids"]) == 2
    positions = [_asset(db_engine, asset_id)["list_position"] for asset_id in response.json()["asset_ids"]]
    assert positions == [3, 4]


def test_target_equal_to_own_workflow_resets(api_client, db_engine):
    response = api_client.post(
        "/api/v1/assets/reprocess",
        json={"asset_ids": ["a-done"], "target_workflow_id": "wf-a"},
    )

    assert response.status_code == 200
    assert response.json() == {"asset_ids": ["a-done"]}
    assert _asset(db_engine, "a-done")["status"] == "pending"


def test_in_progress_is_rejected_atomically(api_client, db_engine):
    history_before = _history(db_engine, "a-failed")
    response = api_client.post(
        "/api/v1/assets/reprocess", json={"asset_ids": ["a-failed", "a-running"]}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Asset „a-running“ wird gerade verarbeitet"
    assert _asset(db_engine, "a-failed")["status"] == "failed"
    assert _history(db_engine, "a-failed") == history_before


def test_unknown_asset_and_target(api_client):
    missing_asset = api_client.post("/api/v1/assets/reprocess", json={"asset_ids": ["nope"]})
    assert missing_asset.status_code == 404
    assert missing_asset.json()["detail"] == "Asset nicht gefunden: nope"

    missing_target = api_client.post(
        "/api/v1/assets/reprocess",
        json={"asset_ids": ["a-done"], "target_workflow_id": "wf-x"},
    )
    assert missing_target.status_code == 404
    assert missing_target.json()["detail"] == "Ziel-Workflow nicht gefunden"


def test_empty_selection(api_client):
    response = api_client.post("/api/v1/assets/reprocess", json={"asset_ids": []})

    assert response.status_code == 422
    assert response.json()["detail"] == "Keine Assets gewählt"


def test_duplicate_ids_processed_once(api_client, db_engine):
    history_before = len(_history(db_engine, "a-done"))
    response = api_client.post(
        "/api/v1/assets/reprocess", json={"asset_ids": ["a-done", "a-done"]}
    )

    assert response.status_code == 200
    assert response.json() == {"asset_ids": ["a-done"]}
    assert len(_history(db_engine, "a-done")) == history_before + 1


def test_in_progress_conflict_uses_input_order(api_client):
    response = api_client.post(
        "/api/v1/assets/reprocess", json={"asset_ids": ["a-waiting", "a-running"]}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Asset „a-waiting“ wird gerade verarbeitet"
