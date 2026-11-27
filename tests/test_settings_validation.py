"""Workflow API tests for module-specific settings validation."""

# IMPORTS
import json

import pytest
from fastapi.testclient import TestClient

from auth_helpers import edit_lock_headers, sign_in
from sqlalchemy import text

from marathon.modules import load_catalog
from marathon.server.app import create_app
from marathon.server.config import domains_error
from marathon.server.db.catalog_writes import sync_module_types
from marathon.server.db.macro_writes import ensure_standard_macros
from marathon.server.db.settings_writes import SettingsValidationError, save_settings


# FIXTURES
@pytest.fixture
def api_client(db_engine, tmp_path):
    """Provide a client with the current module catalog installed."""
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id, email, display_name, role) VALUES ('test-admin', 'admin@test.local', 'Test Admin', 'admin')"))
        sync_module_types(connection, load_catalog())
        ensure_standard_macros(connection)
    app = create_app(static_dir=tmp_path, engine=db_engine)
    sign_in(app)
    with TestClient(app) as client:
        yield client


# FUNC
def _save_watchfolder_settings(api_client, settings):
    workflow = api_client.post("/api/v1/workflows", json={"name": "Watchfolder"}).json()
    response = api_client.put(
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
        headers=edit_lock_headers(api_client, "workflow", workflow["id"]),
    )
    return workflow["id"], response


# TESTS
def test_save_settings_rejects_invalid_allowed_domain(db_engine):
    with db_engine.begin() as connection:
        with pytest.raises(SettingsValidationError) as error:
            save_settings(
                connection,
                "",
                public_url="https://localhost",
                builtin=True,
                trusted_proxies=[],
                allowed_domains=["looks"],
            )

    assert str(error.value) == domains_error


def test_saves_missing_path_with_issue(api_client):
    workflow_id, response = _save_watchfolder_settings(api_client, {"path": ""})
    assert response.status_code == 200, response.json()
    assert api_client.get(f"/api/v1/workflows/{workflow_id}").json()["issues"] == ["Eingang"]


def test_saves_extension_conflict_with_issue(api_client):
    workflow_id, response = _save_watchfolder_settings(
        api_client, {"path": "/mnt/in", "include": ".mxf", "exclude": ".MXF"}
    )
    assert response.status_code == 200, response.json()
    assert api_client.get(f"/api/v1/workflows/{workflow_id}").json()["issues"] == ["Eingang"]


def test_saves_wait_below_minimum_with_issue(api_client):
    workflow_id, response = _save_watchfolder_settings(api_client, {"path": "/mnt/in", "wait_s": 4})
    assert response.status_code == 200, response.json()
    assert api_client.get(f"/api/v1/workflows/{workflow_id}").json()["issues"] == ["Eingang"]


def test_accepts_valid_settings(api_client):
    _, response = _save_watchfolder_settings(
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
        ensure_standard_macros(connection)
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
        headers=edit_lock_headers(api_client, "workflow", workflow["id"]),
    )
    assert response.status_code == 200, response.json()
    assert response.json()["version"] == 2
