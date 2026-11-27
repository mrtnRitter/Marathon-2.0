"""Macro API integration tests using isolated PostgreSQL."""

# IMPORTS
import copy
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from auth_helpers import create_test_user, sign_in
from marathon.server.app import create_app
from marathon.server.auth.sessions import AuthUser


# FIXTURES
@pytest.fixture
def macro_context(db_engine, tmp_path):
    """Provide an admin and ordinary-user API session backed by PostgreSQL."""
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id, email, display_name, role) "
            "VALUES ('test-admin', 'admin@test.local', 'Test Admin', 'admin')"
        ))
        user = create_test_user(connection, "macro-user@test.local")
    admin = AuthUser("test-admin", "admin@test.local", "Test Admin", "admin", "test-session")
    app = create_app(static_dir=tmp_path, engine=db_engine, sync_catalog=True)
    sign_in(app, admin)
    with TestClient(app) as client:
        yield client, app, db_engine, admin, user


# HELPERS
def _definition(client: TestClient, name: str = "Mein Modul") -> dict:
    """Copy a valid catalog-backed standard definition for mutation tests."""
    definition = copy.deepcopy(client.get("/api/v1/macros/copy_file").json()["definition"])
    definition["name"] = name
    return definition


def _insert_workflow_instance(db_engine, workflow_id: str, macro_id: str, name: str) -> None:
    """Make a module appear in the workflow's current version."""
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO workflow_instances "
            "(workflow_id, version, id, macro_id, macro_version, name, position_x, position_y) "
            "VALUES (:workflow_id, 1, :instance_id, :macro_id, 1, :name, 0, 0)"
        ), {
            "workflow_id": workflow_id,
            "instance_id": f"instance-{workflow_id}",
            "macro_id": macro_id,
            "name": name,
        })


def _create_workflow(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/workflows", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


# TESTS
def test_startup_seeds_standard_modules_for_catalog(macro_context):
    client, app, db_engine, _, user = macro_context
    listed = client.get("/api/v1/macros")
    assert listed.status_code == 200
    items = listed.json()
    with db_engine.connect() as connection:
        type_ids = set(connection.execute(text("SELECT id FROM module_types")).scalars())
    assert {item["id"] for item in items} == type_ids
    by_id = {item["id"]: item for item in items}
    assert by_id["copy_file"]["owner"] is None
    assert by_id["copy_file"]["can_edit"] is True
    assert any(setting["key"] == "parallel" for setting in by_id["copy_file"]["settings"])
    sign_in(app, user)
    ordinary_items = client.get("/api/v1/macros").json()
    assert all(item["can_edit"] is False for item in ordinary_items)


def test_create_macro_validates_and_returns_detail(macro_context):
    client, _, _, _, _ = macro_context
    created = client.post("/api/v1/macros", json={"definition": _definition(client)})
    assert created.status_code == 201, created.text
    detail = created.json()
    assert re.fullmatch(r"mc-[0-9a-f]{12}", detail["id"])
    assert detail["version"] == 1
    assert detail["owner"]["id"] == "test-admin"
    assert detail["can_edit"] is True
    invalid = client.post("/api/v1/macros", json={"definition": {}})
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "Name fehlt"


def test_get_permissions_and_deleted_macro(macro_context):
    client, app, _, _, user = macro_context
    created = client.post("/api/v1/macros", json={"definition": _definition(client)}).json()
    sign_in(app, user)
    forbidden = client.get(f"/api/v1/macros/{created['id']}")
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "Keine Berechtigung für dieses Modul"
    sign_in(app, AuthUser("test-admin", "admin@test.local", "Test Admin", "admin", "test-session"))
    assert client.delete(f"/api/v1/macros/{created['id']}").status_code == 204
    missing = client.get(f"/api/v1/macros/{created['id']}")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Modul nicht gefunden"


def test_update_conflict_and_outdated_workflows(macro_context):
    client, _, db_engine, _, _ = macro_context
    created = client.post("/api/v1/macros", json={"definition": _definition(client)}).json()
    path = f"/api/v1/macros/{created['id']}"
    stale = client.put(path, json={"base_version": 2, "definition": _definition(client, "Neu")})
    assert stale.status_code == 409
    assert stale.json()["detail"] == "Modul wurde zwischenzeitlich geändert. Bitte neu laden."
    workflow_id = _create_workflow(client, "Veralteter Workflow")
    _insert_workflow_instance(db_engine, workflow_id, created["id"], "Instanzname")
    updated = client.put(path, json={"base_version": 1, "definition": _definition(client, "Version zwei")})
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["outdated_workflows"] == [{"id": workflow_id, "name": "Veralteter Workflow"}]


def test_delete_standard_used_and_unused_macros(macro_context):
    client, _, db_engine, _, _ = macro_context
    standard = client.delete("/api/v1/macros/copy_file")
    assert standard.status_code == 409
    assert standard.json()["detail"] == "Standardmodule können nicht gelöscht werden"
    used = client.post("/api/v1/macros", json={"definition": _definition(client, "Verwendetes Modul")}).json()
    workflow_id = _create_workflow(client, "Verwendung")
    _insert_workflow_instance(db_engine, workflow_id, used["id"], "Instanz")
    blocked = client.delete(f"/api/v1/macros/{used['id']}")
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Modul wird in Workflows verwendet: Verwendung"
    unused = client.post("/api/v1/macros", json={"definition": _definition(client, "Unbenutzt")}).json()
    deleted = client.delete(f"/api/v1/macros/{unused['id']}")
    assert deleted.status_code == 204
    assert unused["id"] not in {item["id"] for item in client.get("/api/v1/macros").json()}


def test_update_and_delete_require_ownership(macro_context):
    client, app, _, _, user = macro_context
    created = client.post("/api/v1/macros", json={"definition": _definition(client)}).json()
    foreign_definition = _definition(client, "Fremd")
    sign_in(app, user)
    path = f"/api/v1/macros/{created['id']}"
    updated = client.put(path, json={"base_version": 1, "definition": foreign_definition})
    assert updated.status_code == 403
    assert updated.json()["detail"] == "Keine Berechtigung für dieses Modul"
    deleted = client.delete(path)
    assert deleted.status_code == 403
    assert deleted.json()["detail"] == "Keine Berechtigung für dieses Modul"


def test_ensure_standard_macros_is_idempotent_and_adds_module_type(db_engine):
    from marathon.server.db.macro_writes import ensure_standard_macros

    with db_engine.begin() as connection:
        assert ensure_standard_macros(connection) == 0
        connection.execute(text(
            "INSERT INTO module_types "
            "(id, name, category, icon, description, has_input, outputs, settings, sort_order, execution) "
            "VALUES ('added_type', 'Zusatz', 'Test', 'blocks', '', true, '[]'::jsonb, '[]'::jsonb, 1, 'none')"
        ))
        assert ensure_standard_macros(connection) == 1
        assert ensure_standard_macros(connection) == 0
        assert connection.execute(text("SELECT current_version FROM macros WHERE id = 'added_type'")).scalar_one() == 1
