"""Workflow API tests for module-specific settings validation."""

# IMPORTS
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.modules import load_catalog
from marathon.server.app import create_app
from marathon.server.db.catalog_writes import sync_module_types


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
def _save_watchfolder_settings(api_client, settings):
    workflow = api_client.post("/api/v1/workflows", json={"name": "Watchfolder"}).json()
    return api_client.put(
        f"/api/v1/workflows/{workflow['id']}/graph",
        json={
            "base_version": 1,
            "modules": [
                {
                    "id": "w1",
                    "type_id": "watchfolder",
                    "name": "Eingang",
                    "position": {"x": 0, "y": 0},
                    "settings": settings,
                }
            ],
            "connections": [],
        },
    )


# TESTS
def test_rejects_missing_path(api_client):
    response = _save_watchfolder_settings(api_client, {"path": ""})
    assert response.status_code == 422
    assert response.json()["detail"] == "Eingang: Ordner: Ordner fehlt"


def test_rejects_extension_conflict(api_client):
    response = _save_watchfolder_settings(
        api_client, {"path": "/mnt/in", "include": ".mxf", "exclude": ".MXF"}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Eingang: Exclude-Endungen: .mxf steht bereits in der Include-Liste"


def test_rejects_wait_below_minimum(api_client):
    response = _save_watchfolder_settings(api_client, {"path": "/mnt/in", "wait_s": 4})
    assert response.status_code == 422
    assert response.json()["detail"] == "Eingang: Wartezeit: Mindestens 5 s"


def test_accepts_valid_settings(api_client):
    response = _save_watchfolder_settings(
        api_client,
        {
            "path": "\\\\server\\share",
            "include": ".mxf",
            "exclude": "",
            "wait_s": 60,
            "recursive": False,
        },
    )
    assert response.status_code == 200, response.json()
    assert response.json()["version"] == 2


def test_type_without_validator_is_unchanged(api_client, db_engine):
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO module_types (id, name, category, icon, has_input, outputs) "
                "VALUES (:id, :name, 'test', 'test', false, CAST(:outputs AS jsonb))"
            ),
            {
                "id": "custom",
                "name": "Custom",
                "outputs": json.dumps([{"id": "success", "label": "Weiter", "description": ""}]),
            },
        )
    workflow = api_client.post("/api/v1/workflows", json={"name": "Custom"}).json()
    response = api_client.put(
        f"/api/v1/workflows/{workflow['id']}/graph",
        json={
            "base_version": 1,
            "modules": [
                {
                    "id": "c1",
                    "type_id": "custom",
                    "name": "Beliebig",
                    "position": {"x": 0, "y": 0},
                    "settings": {"arbitrary": "anything"},
                }
            ],
            "connections": [],
        },
    )
    assert response.status_code == 200, response.json()
    assert response.json()["version"] == 2
