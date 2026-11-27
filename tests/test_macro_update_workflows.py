"""Tests for upgrading a user's workflows to the current macro version."""

# IMPORTS
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from auth_helpers import create_test_user, sign_in
from marathon.server.app import create_app
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.edit_locks import acquire_lock


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


def _acquire_lock(client: TestClient, object_type: str, object_id: str) -> str:
    response = client.post(f"/api/v1/access/{object_type}/{object_id}/lock")
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _release_lock(client: TestClient, object_type: str, object_id: str, token: str) -> None:
    response = client.delete(
        f"/api/v1/access/{object_type}/{object_id}/lock",
        headers={"X-Edit-Lock": token},
    )
    assert response.status_code == 204, response.text


def _save_graph(
    client: TestClient,
    workflow_id: str,
    modules: list[dict],
    connections: list[dict] | None = None,
) -> None:
    token = _acquire_lock(client, "workflow", workflow_id)
    try:
        response = client.put(
            f"/api/v1/workflows/{workflow_id}/graph",
            json={"base_version": 1, "modules": modules, "connections": connections or []},
            headers={"X-Edit-Lock": token},
        )
        assert response.status_code == 200, response.text
    finally:
        _release_lock(client, "workflow", workflow_id, token)


def _set_macro_version(client: TestClient, macro_id: str, definition: dict) -> None:
    token = _acquire_lock(client, "macro", macro_id)
    try:
        response = client.put(
            f"/api/v1/macros/{macro_id}",
            json={"base_version": 1, "definition": definition},
            headers={"X-Edit-Lock": token},
        )
        assert response.status_code == 200, response.text
    finally:
        _release_lock(client, "macro", macro_id, token)


def _set_macro_share(db_engine, macro_id: str, user_id: str, access: str | None) -> None:
    with db_engine.begin() as connection:
        connection.execute(text(
            "DELETE FROM object_shares WHERE object_type = 'macro' "
            "AND object_id = :macro_id AND user_id = :user_id"
        ), {"macro_id": macro_id, "user_id": user_id})
        if access is not None:
            connection.execute(text(
                "INSERT INTO object_shares (object_type, object_id, user_id, access) "
                "VALUES ('macro', :macro_id, :user_id, :access)"
            ), {"macro_id": macro_id, "user_id": user_id, "access": access})


def _acquire_database_lock(db_engine, user: AuthUser, workflow_id: str) -> None:
    with db_engine.begin() as connection:
        acquire_lock(connection, user, "workflow", workflow_id, None, 600)


def _has_workflow_lock(db_engine, workflow_id: str) -> bool:
    with db_engine.connect() as connection:
        return connection.execute(text(
            "SELECT 1 FROM edit_locks WHERE object_type = 'workflow' AND object_id = :id"
        ), {"id": workflow_id}).scalar_one_or_none() is not None


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
    _set_macro_share(db_engine, macro_id, other_user.id, "use")
    admin_workflow = _create_workflow(client, "Eigener veralteter Workflow")
    _save_graph(client, admin_workflow, [_module("mine", macro_id, settings={"a": 1, "b": 2})])

    sign_in(app, other_user)
    foreign_workflow = _create_workflow(client, "Fremder Eigentümer")
    _save_graph(client, foreign_workflow, [_module("foreign", macro_id, settings={"a": 1, "b": 2})])
    _set_macro_share(db_engine, macro_id, other_user.id, None)
    forbidden = client.post(f"/api/v1/macros/{macro_id}/update-workflows")
    assert forbidden.status_code == 404
    assert forbidden.json()["detail"] == "Modul nicht gefunden"

    sign_in(app, admin)
    _set_macro_version(client, macro_id, _definition(settings=("a",)))
    current_workflow = _create_workflow(client, "Aktuelle Modulversion")
    _save_graph(client, current_workflow, [_module("current", macro_id, settings={"a": 1})])
    updated = client.post(f"/api/v1/macros/{macro_id}/update-workflows")
    assert updated.status_code == 200, updated.text
    assert updated.json() == {
        "updated": [
            {"id": admin_workflow, "name": "Eigener veralteter Workflow", "dropped_settings": ["b"]},
            {"id": foreign_workflow, "name": "Fremder Eigentümer", "dropped_settings": ["b"]},
        ],
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
    assert versions == {admin_workflow: 3, foreign_workflow: 3, current_workflow: 2}



def test_foreign_lock_skips_only_locked_workflow_without_force(macro_update_context):
    client, _, db_engine, _, other_user = macro_update_context
    macro_id = _create_macro(client)
    locked_id = _create_workflow(client, "Gesperrt")
    available_id = _create_workflow(client, "Weiter aktualisieren")
    _save_graph(client, locked_id, [_module("locked", macro_id)])
    _save_graph(client, available_id, [_module("available", macro_id)])
    _set_macro_version(client, macro_id, _definition(settings=("a",)))
    _acquire_database_lock(db_engine, other_user, locked_id)

    response = client.post(f"/api/v1/macros/{macro_id}/update-workflows")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "updated": [{"id": available_id, "name": "Weiter aktualisieren", "dropped_settings": []}],
        "skipped": [{
            "id": locked_id,
            "name": "Gesperrt",
            "reason": f"Wird gerade bearbeitet von {other_user.email}",
        }],
    }
    assert _has_workflow_lock(db_engine, locked_id)
    with db_engine.connect() as connection:
        versions = dict(connection.execute(text(
            "SELECT id, current_version FROM workflows WHERE id IN (:locked_id, :available_id)"
        ), {"locked_id": locked_id, "available_id": available_id}).all())
    assert versions == {locked_id: 2, available_id: 3}


def test_force_breaks_foreign_lock_logs_both_interventions_and_preserves_own_lock(macro_update_context):
    client, app, db_engine, admin, other_user = macro_update_context
    macro_id = _create_macro(client)
    _set_macro_share(db_engine, macro_id, other_user.id, "use")
    own_workflow = _create_workflow(client, "Eigener Workflow")
    _save_graph(client, own_workflow, [_module("mine", macro_id)])

    sign_in(app, other_user)
    foreign_workflow = _create_workflow(client, "Fremder Workflow")
    _save_graph(client, foreign_workflow, [_module("theirs", macro_id)])
    sign_in(app, admin)
    _set_macro_version(client, macro_id, _definition(name="Update-Modul neu", settings=("a",)))
    _acquire_database_lock(db_engine, other_user, foreign_workflow)
    _acquire_database_lock(db_engine, admin, own_workflow)

    response = client.post(f"/api/v1/macros/{macro_id}/update-workflows?force=true")

    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()["updated"]} == {own_workflow, foreign_workflow}
    assert response.json()["skipped"] == []
    assert not _has_workflow_lock(db_engine, foreign_workflow)
    assert _has_workflow_lock(db_engine, own_workflow)
    with db_engine.connect() as connection:
        events = dict(connection.execute(text(
            "SELECT object_id, array_agg(text ORDER BY id) FROM object_events "
            "WHERE object_type = 'workflow' AND object_id IN (:own_id, :foreign_id) GROUP BY object_id"
        ), {"own_id": own_workflow, "foreign_id": foreign_workflow}).all())
    assert events == {
        foreign_workflow: [
            f"Bearbeitungssperre von {other_user.email} aufgehoben",
            "Modul „Update-Modul neu“ auf v2 aktualisiert",
        ],
    }


def test_force_keeps_foreign_lock_when_running_assets_skip_workflow(macro_update_context):
    client, _, db_engine, _, other_user = macro_update_context
    macro_id = _create_macro(client, _definition(include_rename=True))
    workflow_id = _create_workflow(client, "Aktives Asset mit Sperre")
    _save_graph(client, workflow_id, [_module("m1", macro_id)])
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status) "
            "VALUES ('asset-force-rename', :workflow_id, 'Rename', 'in_progress', 'm1.rename', 'running')"
        ), {"workflow_id": workflow_id})
    _set_macro_version(client, macro_id, _definition(settings=("a",), include_success=True))
    _acquire_database_lock(db_engine, other_user, workflow_id)

    response = client.post(f"/api/v1/macros/{macro_id}/update-workflows?force=true")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "updated": [],
        "skipped": [{
            "id": workflow_id,
            "name": "Aktives Asset mit Sperre",
            "reason": "Laufende Assets betroffen",
        }],
    }
    assert _has_workflow_lock(db_engine, workflow_id)
    with db_engine.connect() as connection:
        assert connection.execute(text(
            "SELECT count(*) FROM object_events WHERE object_type = 'workflow' AND object_id = :id"
        ), {"id": workflow_id}).scalar_one() == 0


def test_macro_update_access_levels_and_force_require_admin(macro_update_context):
    client, app, db_engine, _, other_user = macro_update_context
    macro_id = _create_macro(client)
    sign_in(app, other_user)

    no_access = client.post(f"/api/v1/macros/{macro_id}/update-workflows")
    assert no_access.status_code == 404
    assert no_access.json()["detail"] == "Modul nicht gefunden"

    for access in ("use", "view"):
        _set_macro_share(db_engine, macro_id, other_user.id, access)
        denied = client.post(f"/api/v1/macros/{macro_id}/update-workflows")
        assert denied.status_code == 403
        assert denied.json()["detail"] == "Keine Berechtigung für dieses Modul"

    _set_macro_share(db_engine, macro_id, other_user.id, "edit")
    allowed = client.post(f"/api/v1/macros/{macro_id}/update-workflows")
    assert allowed.status_code == 200, allowed.text
    forced = client.post(f"/api/v1/macros/{macro_id}/update-workflows?force=true")
    assert forced.status_code == 403
    assert forced.json()["detail"] == "Nur für Admins"
