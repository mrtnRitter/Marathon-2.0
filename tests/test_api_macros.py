"""Macro API integration tests using isolated PostgreSQL."""

# IMPORTS
import copy
import hashlib
import re
from uuid import uuid4
from datetime import datetime, timedelta, timezone

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
def _definition(client: TestClient, name: str = "Mein Modul", db_engine=None) -> dict:
    """Copy a valid catalog-backed definition for mutation tests."""
    if db_engine is None:
        definition = copy.deepcopy(client.get("/api/v1/macros/copy_file").json()["definition"])
    else:
        with db_engine.connect() as connection:
            definition = copy.deepcopy(connection.execute(text(
                "SELECT definition FROM macro_versions WHERE macro_id = 'copy_file' AND version = 1"
            )).scalar_one())
    definition["name"] = name
    return definition


def _set_share(db_engine, macro_id: str, user_id: str | None, access: str) -> None:
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO object_shares (object_type, object_id, user_id, access) "
            "VALUES ('macro', :macro_id, :user_id, :access) "
            "ON CONFLICT (object_type, object_id, (COALESCE(user_id, ''))) "
            "DO UPDATE SET access = EXCLUDED.access"
        ), {"macro_id": macro_id, "user_id": user_id, "access": access})


def _set_lock(db_engine, macro_id: str, user_id: str, token: str) -> None:
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO edit_locks (object_type, object_id, user_id, token_hash, expires_at) "
            "VALUES ('macro', :macro_id, :user_id, :token_hash, :expires_at)"
        ), {
            "macro_id": macro_id,
            "user_id": user_id,
            "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        })


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


def _create_workflow(db_engine, owner_user_id: str, name: str) -> str:
    """Insert a current workflow version without depending on workflow API routes."""
    workflow_id = f"wf-{uuid4().hex[:12]}"
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO workflows (id, name, owner_user_id, current_version) "
            "VALUES (:id, :name, :owner_user_id, 1)"
        ), {"id": workflow_id, "name": name, "owner_user_id": owner_user_id})
        connection.execute(text(
            "INSERT INTO workflow_versions (workflow_id, version) VALUES (:id, 1)"
        ), {"id": workflow_id})
    return workflow_id


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
    assert by_id["copy_file"]["access"] == "owner"
    assert "can_edit" not in by_id["copy_file"]
    assert any(setting["key"] == "parallel" for setting in by_id["copy_file"]["settings"])
    sign_in(app, user)
    ordinary_items = client.get("/api/v1/macros").json()
    assert ordinary_items
    assert all(item["access"] == "use" for item in ordinary_items)


def test_create_macro_validates_and_keeps_new_modules_private(macro_context):
    client, app, db_engine, _, user = macro_context
    created = client.post("/api/v1/macros", json={"definition": _definition(client, db_engine=db_engine)})
    assert created.status_code == 201, created.text
    detail = created.json()
    assert re.fullmatch(r"mc-[0-9a-f]{12}", detail["id"])
    assert detail["version"] == 1
    assert detail["owner"] is None
    assert detail["access"] == "owner"
    with db_engine.connect() as connection:
        assert connection.execute(text(
            "SELECT count(*) FROM object_shares WHERE object_type = 'macro' AND object_id = :id"
        ), {"id": detail["id"]}).scalar_one() == 0
    invalid = client.post("/api/v1/macros", json={"definition": {}})
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "Name fehlt"

    sign_in(app, user)
    owned = client.post("/api/v1/macros", json={"definition": _definition(client, "Privat", db_engine)})
    assert owned.status_code == 201, owned.text
    assert owned.json()["owner"]["id"] == user.id
    assert owned.json()["access"] == "owner"
    with db_engine.connect() as connection:
        assert connection.execute(text(
            "SELECT count(*) FROM object_shares WHERE object_type = 'macro' AND object_id = :id"
        ), {"id": owned.json()["id"]}).scalar_one() == 0


def test_get_requires_edit_and_hides_modules_without_access(macro_context):
    client, app, db_engine, _, user = macro_context
    created = client.post("/api/v1/macros", json={"definition": _definition(client, db_engine=db_engine)}).json()
    path = f"/api/v1/macros/{created['id']}"
    sign_in(app, user)
    hidden = client.get(path)
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "Modul nicht gefunden"

    _set_share(db_engine, created["id"], user.id, "view")
    view_only = client.get(path)
    assert view_only.status_code == 403
    assert view_only.json()["detail"] == "Keine Berechtigung für dieses Modul"
    _set_share(db_engine, created["id"], user.id, "edit")
    editable = client.get(path)
    assert editable.status_code == 200
    assert editable.json()["access"] == "edit"
    assert "definition" in editable.json()


def test_update_conflict_and_outdated_workflows(macro_context):
    client, _, db_engine, admin, _ = macro_context
    created = client.post("/api/v1/macros", json={"definition": _definition(client, db_engine=db_engine)}).json()
    path = f"/api/v1/macros/{created['id']}"
    token = "valid-admin-lock"
    _set_lock(db_engine, created["id"], admin.id, token)
    headers = {"X-Edit-Lock": token}
    stale = client.put(path, headers=headers, json={"base_version": 2, "definition": _definition(client, "Neu", db_engine)})
    assert stale.status_code == 409
    assert stale.json()["detail"] == "Modul wurde zwischenzeitlich geändert. Bitte neu laden."
    workflow_id = _create_workflow(db_engine, admin.id, "Veralteter Workflow")
    _insert_workflow_instance(db_engine, workflow_id, created["id"], "Instanzname")
    updated = client.put(path, headers=headers, json={"base_version": 1, "definition": _definition(client, "Version zwei", db_engine)})
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["outdated_workflows"] == [{"id": workflow_id, "name": "Veralteter Workflow"}]


def test_delete_archives_standard_and_used_macros(macro_context):
    client, _, db_engine, _, _ = macro_context
    used = client.post("/api/v1/macros", json={"definition": _definition(client, "Verwendetes Modul", db_engine)}).json()
    unused = client.post("/api/v1/macros", json={"definition": _definition(client, "Unbenutzt", db_engine)}).json()
    workflow_id = _create_workflow(db_engine, "test-admin", "Verwendung")
    _insert_workflow_instance(db_engine, workflow_id, used["id"], "Instanz")

    deleted_used = client.delete(f"/api/v1/macros/{used['id']}")
    assert deleted_used.status_code == 204
    deleted_standard = client.delete("/api/v1/macros/copy_file")
    assert deleted_standard.status_code == 204
    deleted_unused = client.delete(f"/api/v1/macros/{unused['id']}")
    assert deleted_unused.status_code == 204
    listed_ids = {item["id"] for item in client.get("/api/v1/macros").json()}
    assert used["id"] not in listed_ids
    assert "copy_file" not in listed_ids
    assert unused["id"] not in listed_ids
    with db_engine.connect() as connection:
        assert connection.execute(text(
            "SELECT count(*) FROM workflow_instances WHERE workflow_id = :id AND macro_id = :macro_id"
        ), {"id": workflow_id, "macro_id": used["id"]}).scalar_one() == 1


def test_update_and_delete_require_access_levels(macro_context):
    client, app, db_engine, _, user = macro_context
    created = client.post("/api/v1/macros", json={"definition": _definition(client, db_engine=db_engine)}).json()
    path = f"/api/v1/macros/{created['id']}"
    sign_in(app, user)

    hidden_update = client.put(path, json={"base_version": 1, "definition": _definition(client, "Fremd", db_engine)})
    assert hidden_update.status_code == 404
    assert hidden_update.json()["detail"] == "Modul nicht gefunden"
    assert client.delete(path).status_code == 404

    _set_share(db_engine, created["id"], user.id, "use")
    forbidden_update = client.put(path, json={"base_version": 1, "definition": _definition(client, "Fremd", db_engine)})
    assert forbidden_update.status_code == 403
    assert forbidden_update.json()["detail"] == "Keine Berechtigung für dieses Modul"
    forbidden_delete = client.delete(path)
    assert forbidden_delete.status_code == 403
    assert forbidden_delete.json()["detail"] == "Keine Berechtigung für dieses Modul"


def test_put_requires_the_current_edit_lock(macro_context):
    client, app, db_engine, _, user = macro_context
    sign_in(app, user)
    created = client.post("/api/v1/macros", json={"definition": _definition(client, "Gesperrt", db_engine)}).json()
    path = f"/api/v1/macros/{created['id']}"
    payload = {"base_version": 1, "definition": _definition(client, "Version zwei", db_engine)}

    missing = client.put(path, json=payload)
    assert missing.status_code == 409
    assert missing.json()["detail"] == "Bearbeitungssperre fehlt oder wurde aufgehoben. Bitte neu laden."

    token = "the-current-token"
    _set_lock(db_engine, created["id"], user.id, token)
    wrong = client.put(path, headers={"X-Edit-Lock": "wrong-token"}, json=payload)
    assert wrong.status_code == 409
    assert wrong.json()["detail"] == "Bearbeitungssperre fehlt oder wurde aufgehoben. Bitte neu laden."

    saved = client.put(path, headers={"X-Edit-Lock": token}, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2


def test_ensure_standard_macros_is_idempotent_and_shares_added_module(db_engine):
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
        share = connection.execute(text(
            "SELECT access FROM object_shares "
            "WHERE object_type = 'macro' AND object_id = 'added_type' AND user_id IS NULL"
        )).scalar_one()
        assert share == "use"
