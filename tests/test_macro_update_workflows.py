"""Tests for upgrading a user's workflows to the current macro version."""

# IMPORTS
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from auth_helpers import create_test_user, sign_in
from marathon.server.app import create_app
from marathon.server.auth.sessions import AuthUser


# FIXTURES
@pytest.fixture
def macro_update_context(db_engine, tmp_path):
    """Provide an admin module owner and another user on an isolated database."""
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id, email, display_name, role) "
            "VALUES ('test-admin', 'admin@test.local', 'Test Admin', 'admin')"
        ))
        other_user = create_test_user(connection, "other@test.local")
    admin = AuthUser("test-admin", "admin@test.local", "Test Admin", "admin", "test-session")
    app = create_app(static_dir=tmp_path, engine=db_engine, sync_catalog=True)
    sign_in(app, admin)
    with TestClient(app) as client:
        yield client, app, db_engine, admin, other_user


# HELPERS
def _definition(
    name: str = "Update-Modul",
    settings: tuple[str, ...] = ("a", "b"),
    *,
    include_rename: bool = False,
    include_success: bool = True,
) -> dict:
    """Build a valid copy macro with optional second block and outputs."""
    blocks = [{
        "id": "copy", "type_id": "copy_file", "name": "Kopieren",
        "position": {"x": 0, "y": 0}, "settings": {},
    }]
    if include_rename:
        blocks.append({
            "id": "rename", "type_id": "rename_file", "name": "Umbenennen",
            "position": {"x": 1, "y": 0}, "settings": {},
        })
    sources = ["copy", "rename"] if include_rename else ["copy"]
    outputs = []
    if include_success:
        outputs.append({
            "id": "success", "label": "Erfolg", "description": "",
            "sources": [{"block_id": sources[-1], "output_id": "success"}],
        })
    outputs.append({
        "id": "error", "label": "Fehler", "description": "",
        "sources": [{"block_id": block_id, "output_id": "error"} for block_id in sources],
    })
    exposed_settings = [
        {"key": key, "block_id": "copy", "setting_key": setting_key, "label": key, "help": ""}
        for key, setting_key in zip(settings, ("target", "parallel"), strict=False)
    ]
    return {
        "name": name,
        "category": "Dateien",
        "icon": "copy",
        "description": "",
        "sort_order": 50,
        "blocks": blocks,
        "connections": ([{
            "source_block_id": "copy", "source_output_id": "success", "target_block_id": "rename",
        }] if include_rename else []),
        "entry_block_id": "copy",
        "outputs": outputs,
        "settings": exposed_settings,
    }


def _create_macro(client: TestClient, definition: dict | None = None) -> str:
    response = client.post("/api/v1/macros", json={"definition": definition or _definition()})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_workflow(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/workflows", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _module(instance_id: str, macro_id: str, *, settings: dict | None = None) -> dict:
    return {
        "id": instance_id,
        "type_id": macro_id,
        "name": "Instanzname",
        "description": "Beschreibung behalten",
        "position": {"x": 4, "y": 8},
        "settings": settings or {},
        "locked": True,
    }


def _save_graph(
    client: TestClient,
    workflow_id: str,
    modules: list[dict],
    connections: list[dict] | None = None,
) -> None:
    response = client.put(f"/api/v1/workflows/{workflow_id}/graph", json={
        "base_version": 1, "modules": modules, "connections": connections or [],
    })
    assert response.status_code == 200, response.text


def _set_macro_version(client: TestClient, macro_id: str, definition: dict) -> None:
    response = client.put(f"/api/v1/macros/{macro_id}", json={
        "base_version": 1, "definition": definition,
    })
    assert response.status_code == 200, response.text


# TESTS
def test_update_remaps_settings_and_rebuilds_runtime_graph(macro_update_context):
    client, _, db_engine, _, _ = macro_update_context
    macro_id = _create_macro(client)
    workflow_id = _create_workflow(client, "Update Einstellungen")
    _save_graph(client, workflow_id, [_module("m1", macro_id, settings={"a": 1, "b": 2})])
    _set_macro_version(client, macro_id, _definition(settings=("a",)))

    response = client.post(f"/api/v1/macros/{macro_id}/update-workflows")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "updated": [{"id": workflow_id, "name": "Update Einstellungen", "dropped_settings": ["b"]}],
        "skipped": [],
    }
    with db_engine.connect() as connection:
        current_version = connection.execute(text(
            "SELECT current_version FROM workflows WHERE id = :id"
        ), {"id": workflow_id}).scalar_one()
        instance = connection.execute(text(
            "SELECT macro_version, settings, name, description, position_x, position_y, locked "
            "FROM workflow_instances WHERE workflow_id = :id AND version = :version"
        ), {"id": workflow_id, "version": current_version}).mappings().one()
        runtime = connection.execute(text(
            "SELECT id, instance_id, block_id, settings FROM workflow_modules "
            "WHERE workflow_id = :id AND version = :version ORDER BY id"
        ), {"id": workflow_id, "version": current_version}).mappings().all()
    assert current_version == 3
    assert dict(instance) == {
        "macro_version": 2, "settings": {"a": 1}, "name": "Instanzname",
        "description": "Beschreibung behalten", "position_x": 4.0, "position_y": 8.0, "locked": True,
    }
    assert [dict(row) for row in runtime] == [{
        "id": "m1.copy", "instance_id": "m1", "block_id": "copy", "settings": {"target": 1},
    }]


def test_removed_block_with_active_asset_skips_workflow(macro_update_context):
    client, _, db_engine, _, _ = macro_update_context
    macro_id = _create_macro(client, _definition(include_rename=True))
    workflow_id = _create_workflow(client, "Aktives Asset")
    _save_graph(client, workflow_id, [_module("m1", macro_id)])
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status) "
            "VALUES ('asset-rename', :workflow_id, 'Rename', 'in_progress', 'm1.rename', 'running')"
        ), {"workflow_id": workflow_id})
    _set_macro_version(client, macro_id, _definition(settings=("a",), include_success=True))

    response = client.post(f"/api/v1/macros/{macro_id}/update-workflows")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "updated": [],
        "skipped": [{"id": workflow_id, "name": "Aktives Asset", "reason": "Laufende Assets betroffen"}],
    }
    with db_engine.connect() as connection:
        assert connection.execute(text(
            "SELECT current_version FROM workflows WHERE id = :id"
        ), {"id": workflow_id}).scalar_one() == 2


def test_invalid_output_skips_only_that_workflow(macro_update_context):
    client, _, db_engine, _, _ = macro_update_context
    macro_id = _create_macro(client)
    invalid_workflow_id = _create_workflow(client, "A-Ausgang entfernt")
    valid_workflow_id = _create_workflow(client, "B-Weiter aktualisieren")
    _save_graph(client, invalid_workflow_id, [
        _module("source", macro_id), {
            "id": "target", "type_id": "rename_file", "name": "Ziel",
            "position": {"x": 10, "y": 8}, "settings": {},
        },
    ], [{"source_module_id": "source", "source_output_id": "success", "target_module_id": "target"}])
    _save_graph(client, valid_workflow_id, [_module("single", macro_id, settings={"a": 1, "b": 2})])
    _set_macro_version(client, macro_id, _definition(settings=("a",), include_success=False))

    response = client.post(f"/api/v1/macros/{macro_id}/update-workflows")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "updated": [{"id": valid_workflow_id, "name": "B-Weiter aktualisieren", "dropped_settings": ["b"]}],
        "skipped": [{
            "id": invalid_workflow_id, "name": "A-Ausgang entfernt",
            "reason": "Unbekannter Ausgang success an Modul source",
        }],
    }
    with db_engine.connect() as connection:
        versions = dict(connection.execute(text(
            "SELECT id, current_version FROM workflows WHERE id IN (:invalid_id, :valid_id)"
        ), {"invalid_id": invalid_workflow_id, "valid_id": valid_workflow_id}).all())
    assert versions == {invalid_workflow_id: 2, valid_workflow_id: 3}


def test_update_permissions_missing_modules_and_workflow_owner_scope(macro_update_context):
    client, app, db_engine, admin, other_user = macro_update_context
    macro_id = _create_macro(client)
    admin_workflow = _create_workflow(client, "Eigener veralteter Workflow")
    _save_graph(client, admin_workflow, [_module("mine", macro_id, settings={"a": 1, "b": 2})])

    sign_in(app, other_user)
    foreign_workflow = _create_workflow(client, "Fremder Eigentümer")
    _save_graph(client, foreign_workflow, [_module("foreign", macro_id, settings={"a": 1, "b": 2})])
    forbidden = client.post(f"/api/v1/macros/{macro_id}/update-workflows")
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "Keine Berechtigung für dieses Modul"

    sign_in(app, admin)
    _set_macro_version(client, macro_id, _definition(settings=("a",)))
    current_workflow = _create_workflow(client, "Aktuelle Modulversion")
    _save_graph(client, current_workflow, [_module("current", macro_id, settings={"a": 1})])
    updated = client.post(f"/api/v1/macros/{macro_id}/update-workflows")
    assert updated.status_code == 200, updated.text
    assert updated.json() == {
        "updated": [{"id": admin_workflow, "name": "Eigener veralteter Workflow", "dropped_settings": ["b"]}],
        "skipped": [],
    }

    missing = client.post("/api/v1/macros/unknown-module/update-workflows")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Modul nicht gefunden"
    deleted_id = _create_macro(client, _definition(name="Geloeschtes Modul"))
    assert client.delete(f"/api/v1/macros/{deleted_id}").status_code == 204
    deleted = client.post(f"/api/v1/macros/{deleted_id}/update-workflows")
    assert deleted.status_code == 404
    assert deleted.json()["detail"] == "Modul nicht gefunden"

    with db_engine.connect() as connection:
        versions = dict(connection.execute(text(
            "SELECT id, current_version FROM workflows "
            "WHERE id IN (:admin_id, :foreign_id, :current_id)"
        ), {
            "admin_id": admin_workflow, "foreign_id": foreign_workflow, "current_id": current_workflow,
        }).all())
    assert versions == {admin_workflow: 3, foreign_workflow: 2, current_workflow: 2}
