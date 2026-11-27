"""Macro-level access and visible-workflow integration tests."""

# IMPORTS
import copy

import pytest
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import text

from auth_helpers import create_test_user, sign_in
from marathon.server.app import create_app
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.macro_writes import (
    archive_macro,
    create_macro,
    list_macros,
    outdated_workflows,
    update_macro,
)


# FIXTURES
@pytest.fixture
def access_context(db_engine, tmp_path):
    """Provide users and an application backed by an isolated database."""
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id, email, display_name, role) "
            "VALUES ('test-admin', 'admin@test.local', 'Test Admin', 'admin')"
        ))
        owner = create_test_user(connection, "macro-owner@test.local")
        viewer = create_test_user(connection, "macro-viewer@test.local")
    admin = AuthUser("test-admin", "admin@test.local", "Test Admin", "admin", "test-session")
    app = create_app(static_dir=tmp_path, engine=db_engine, sync_catalog=True)
    sign_in(app, admin)
    with TestClient(app) as client:
        yield client, app, db_engine, admin, owner, viewer


# HELPERS
def _module_definition(connection, name: str) -> dict:
    definition = copy.deepcopy(connection.execute(text(
        "SELECT definition FROM macro_versions WHERE macro_id = 'copy_file' AND version = 1"
    )).scalar_one())
    definition["name"] = name
    return definition


def _create_module(connection, definition: dict, owner: AuthUser) -> str:
    return create_macro(connection, definition, owner)


def _create_workflow(db_engine, user: AuthUser, name: str) -> str:
    """Insert a current workflow version for ownership-filter tests."""
    workflow_id = f"wf-{uuid4().hex[:12]}"
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO workflows (id, name, owner_user_id, current_version) "
            "VALUES (:id, :name, :owner_user_id, 1)"
        ), {"id": workflow_id, "name": name, "owner_user_id": user.id})
        connection.execute(text(
            "INSERT INTO workflow_versions (workflow_id, version) VALUES (:id, 1)"
        ), {"id": workflow_id})
    return workflow_id


def _add_instance(db_engine, workflow_id: str, macro_id: str, macro_version: int = 1) -> None:
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO workflow_instances "
            "(workflow_id, version, id, macro_id, macro_version, name, position_x, position_y) "
            "VALUES (:workflow_id, 1, :instance_id, :macro_id, :macro_version, 'Instance', 0, 0)"
        ), {
            "workflow_id": workflow_id,
            "instance_id": f"instance-{workflow_id}",
            "macro_id": macro_id,
            "macro_version": macro_version,
        })


# TESTS
def test_list_macros_filters_and_reports_access_levels(access_context):
    _, _, db_engine, admin, owner, viewer = access_context
    with db_engine.begin() as connection:
        definition = _module_definition(connection, "Private none")
        no_access_id = _create_module(connection, definition, owner)
        for level in ("view", "use", "edit"):
            definition = _module_definition(connection, f"Shared {level}")
            macro_id = _create_module(connection, definition, owner)
            connection.execute(text(
                "INSERT INTO object_shares (object_type, object_id, user_id, access) "
                "VALUES ('macro', :macro_id, :user_id, :access)"
            ), {"macro_id": macro_id, "user_id": viewer.id, "access": level})
        definition = _module_definition(connection, "Viewer owner")
        owner_id = _create_module(connection, definition, viewer)

        viewer_items = list_macros(connection, viewer)
        viewer_by_id = {item["id"]: item for item in viewer_items}
        assert no_access_id not in viewer_by_id
        assert viewer_by_id[owner_id]["access"] == "owner"
        assert "can_edit" not in viewer_by_id[owner_id]
        assert {viewer_by_id[item]["access"] for item in viewer_by_id if item != "copy_file"} == {
            "view", "use", "edit", "owner",
        }
        assert viewer_by_id["copy_file"]["access"] == "use"

        admin_items = {item["id"]: item for item in list_macros(connection, admin)}
        assert no_access_id in admin_items
        assert admin_items[no_access_id]["access"] == "owner"
        assert all(item["access"] == "owner" for item in admin_items.values())


def test_usage_counts_only_workflows_visible_to_the_user(access_context):
    client, app, db_engine, admin, owner, viewer = access_context
    with db_engine.begin() as connection:
        macro_id = _create_module(connection, _module_definition(connection, "Usage module"), viewer)

    visible_workflow = _create_workflow(db_engine, viewer, "Visible usage")
    hidden_workflow = _create_workflow(db_engine, owner, "Hidden usage")
    _add_instance(db_engine, visible_workflow, macro_id)
    _add_instance(db_engine, hidden_workflow, macro_id)

    with db_engine.connect() as connection:
        visible = {item["id"]: item for item in list_macros(connection, viewer)}
        admin_items = {item["id"]: item for item in list_macros(connection, admin)}
    assert visible[macro_id]["usage"] == 1
    assert admin_items[macro_id]["usage"] == 2


def test_archive_used_and_standard_modules_logs_admin_intervention_and_preserves_shares(access_context):
    _, _, db_engine, admin, owner, viewer = access_context
    with db_engine.begin() as connection:
        macro_id = _create_module(connection, _module_definition(connection, "Owned module"), owner)
        definition = _module_definition(connection, "Owned module v2")
        assert update_macro(connection, macro_id, 1, definition, admin) == 2
        connection.execute(text(
            "INSERT INTO object_shares (object_type, object_id, user_id, access) "
            "VALUES ('macro', :macro_id, :user_id, 'view')"
        ), {"macro_id": macro_id, "user_id": viewer.id})
        connection.execute(text(
            "INSERT INTO edit_locks (object_type, object_id, user_id, token_hash, expires_at) "
            "VALUES ('macro', :macro_id, :user_id, 'hash', now() + interval '5 minutes')"
        ), {"macro_id": macro_id, "user_id": owner.id})
        workflow_id = "wf-used-archive"
        connection.execute(text(
            "INSERT INTO workflows (id, name, owner_user_id, current_version) "
            "VALUES (:id, 'Uses archived module', :owner_id, 1)"
        ), {"id": workflow_id, "owner_id": owner.id})
        connection.execute(text(
            "INSERT INTO workflow_versions (workflow_id, version) VALUES (:id, 1)"
        ), {"id": workflow_id})
        connection.execute(text(
            "INSERT INTO workflow_instances "
            "(workflow_id, version, id, macro_id, macro_version, name, position_x, position_y) "
            "VALUES (:workflow_id, 1, 'used-instance', :macro_id, 1, 'Instance', 0, 0)"
        ), {"workflow_id": workflow_id, "macro_id": macro_id})
        assert archive_macro(connection, macro_id, admin) is True
        assert archive_macro(connection, "copy_file", admin) is True

    with db_engine.connect() as connection:
        assert connection.execute(text(
            "SELECT archived_at IS NOT NULL FROM macros WHERE id = :id"
        ), {"id": macro_id}).scalar_one()
        assert connection.execute(text(
            "SELECT count(*) FROM edit_locks WHERE object_type = 'macro' AND object_id = :id"
        ), {"id": macro_id}).scalar_one() == 0
        assert connection.execute(text(
            "SELECT access FROM object_shares WHERE object_type = 'macro' AND object_id = :id AND user_id = :user_id"
        ), {"id": macro_id, "user_id": viewer.id}).scalar_one() == "view"
        events = connection.execute(text(
            "SELECT actor_name, text FROM object_events "
            "WHERE object_type = 'macro' AND object_id = :id ORDER BY id"
        ), {"id": macro_id}).all()
        assert events == [("Test Admin", "Bearbeitet"), ("Test Admin", "Gelöscht")]
        standard_archived = connection.execute(text(
            "SELECT archived_at IS NOT NULL FROM macros WHERE id = 'copy_file'"
        )).scalar_one()
        assert standard_archived


def test_outdated_workflows_respects_workflow_owner_and_admin_scope(access_context):
    client, app, db_engine, admin, owner, viewer = access_context
    with db_engine.begin() as connection:
        definition = _module_definition(connection, "Outdated module")
        macro_id = _create_module(connection, definition, viewer)

    owner_workflow = _create_workflow(db_engine, viewer, "Owner workflow")
    other_workflow = _create_workflow(db_engine, owner, "Other workflow")
    _add_instance(db_engine, owner_workflow, macro_id)
    _add_instance(db_engine, other_workflow, macro_id)

    definition["name"] = "Outdated module v2"
    with db_engine.begin() as connection:
        assert update_macro(connection, macro_id, 1, definition, viewer) == 2
        viewer_outdated = outdated_workflows(connection, macro_id, viewer)
        admin_outdated = outdated_workflows(connection, macro_id, admin)
    assert viewer_outdated == [{"id": owner_workflow, "name": "Owner workflow"}]
    assert admin_outdated == [
        {"id": other_workflow, "name": "Other workflow"},
        {"id": owner_workflow, "name": "Owner workflow"},
    ]
