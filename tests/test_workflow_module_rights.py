"""Database tests for workflow module-level access during graph saves."""

# IMPORTS
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from auth_helpers import create_test_user
from marathon.server.app import create_app
from marathon.server.db import workflow_writes
from marathon.server.db.macro_writes import create_macro, update_macro
from marathon.server.db.workflow_writes import (
    WorkflowModuleForbidden,
    WorkflowValidationError,
    apply_module_rights,
    create_workflow,
    save_graph,
)


# FIXTURES
@pytest.fixture
def access_context(db_engine, tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(static_dir=tmp_path, engine=db_engine, sync_catalog=True)
    with TestClient(app):
        with db_engine.begin() as connection:
            admin = create_test_user(connection, f"admin-{uuid4().hex}@test.local", "admin")
            user = create_test_user(connection, f"user-{uuid4().hex}@test.local")
            macro_id = create_macro(connection, _macro_definition(), admin)
            workflow_id = create_workflow(connection, "Module rights", "")
            assert save_graph(
                connection, workflow_id, 1, [_stored_instance(macro_id)], [], user=None
            ) == 2
        yield db_engine, admin, user, macro_id, workflow_id


# HELPERS
def _macro_definition(name: str = "Two-block macro") -> dict:
    return {
        "name": name,
        "category": "Dateien",
        "icon": "copy",
        "description": "",
        "sort_order": 50,
        "blocks": [
            {"id": "copy", "type_id": "copy_file", "name": "Copy", "position": {"x": 0, "y": 0}, "settings": {}},
            {"id": "rename", "type_id": "rename_file", "name": "Rename", "position": {"x": 1, "y": 0}, "settings": {}},
        ],
        "connections": [{"source_block_id": "copy", "source_output_id": "success", "target_block_id": "rename"}],
        "entry_block_id": "copy",
        "outputs": [
            {"id": "success", "label": "Success", "description": "", "sources": [{"block_id": "rename", "output_id": "success"}]},
            {"id": "error", "label": "Error", "description": "", "sources": [
                {"block_id": "copy", "output_id": "error"},
                {"block_id": "rename", "output_id": "error"},
            ]},
        ],
        "settings": [
            {"key": "target", "block_id": "copy", "setting_key": "target", "label": "Target", "help": ""},
            {"key": "prefix", "block_id": "rename", "setting_key": "prefix", "label": "Prefix", "help": ""},
        ],
    }


def _stored_instance(macro_id: str, instance_id: str = "stored") -> dict:
    return {
        "id": instance_id,
        "type_id": macro_id,
        "name": "Saved name",
        "description": "Saved description",
        "position": {"x": 1, "y": 2},
        "settings": {"target": "saved-target", "prefix": "saved-prefix"},
        "locked": True,
        "macro_version": 1,
    }


def _input_instance(macro_id: str, instance_id: str = "stored", **changes) -> dict:
    instance = _stored_instance(macro_id, instance_id)
    instance.update({
        "type_id": "",
        "name": "Edited name",
        "description": "Edited description",
        "position": {"x": 8, "y": 9},
        "settings": {"target": "edited-target", "prefix": "edited-prefix"},
        "locked": False,
    })
    instance.update(changes)
    return instance


def _share(connection, macro_id: str, user_id: str | None, access: str) -> None:
    connection.execute(text(
        "INSERT INTO object_shares (object_type, object_id, user_id, access) "
        "VALUES ('macro', :macro_id, :user_id, :access)"
    ), {"macro_id": macro_id, "user_id": user_id, "access": access})



# TESTS
def test_user_none_returns_independent_copies_without_checks(access_context):
    db_engine, _, _, _, workflow_id = access_context
    modules = [{"id": "x", "type_id": "missing", "settings": {"nested": [1]}}]
    original = deepcopy(modules)
    with db_engine.connect() as connection:
        result = apply_module_rights(connection, workflow_id, modules, None)
    assert result == original
    assert result is not modules and result[0] is not modules[0]
    assert result[0]["settings"] is not modules[0]["settings"]
    assert modules == original


def test_blind_existing_instance_restores_saved_values_but_keeps_layout(access_context):
    db_engine, _, user, macro_id, workflow_id = access_context
    module = _input_instance(macro_id)
    original = deepcopy(module)
    with db_engine.connect() as connection:
        result = apply_module_rights(connection, workflow_id, [module], user)[0]
    assert result["type_id"] == macro_id
    assert (result["name"], result["description"], result["settings"]) == (
        "Saved name", "Saved description", {"target": "saved-target", "prefix": "saved-prefix"}
    )
    assert result["position"] == {"x": 8, "y": 9}
    assert result["locked"] is False
    assert result["macro_version"] == 1
    assert module == original


def test_blind_existing_without_version_keeps_saved_version(access_context):
    db_engine, _, user, macro_id, workflow_id = access_context
    module = _input_instance(macro_id)
    module.pop("macro_version")
    with db_engine.begin() as connection:
        definition = _macro_definition("Version two")
        assert update_macro(connection, macro_id, 1, definition, access_context[1]) == 2
    with db_engine.connect() as connection:
        result = apply_module_rights(connection, workflow_id, [module], user)[0]
    assert result["macro_version"] == 1
    assert result["settings"] == {"target": "saved-target", "prefix": "saved-prefix"}


def test_blind_existing_new_version_remaps_saved_settings(access_context):
    db_engine, admin, user, macro_id, workflow_id = access_context
    definition = _macro_definition("Version two")
    definition["settings"] = [definition["settings"][0]]
    with db_engine.begin() as connection:
        assert update_macro(connection, macro_id, 1, definition, admin) == 2
    module = _input_instance(macro_id, macro_version=2)
    with db_engine.connect() as connection:
        result = apply_module_rights(connection, workflow_id, [module], user)[0]
    assert result["macro_version"] == 2
    assert result["settings"] == {"target": "saved-target"}


def test_blind_existing_unknown_version_is_forbidden(access_context):
    db_engine, _, user, macro_id, workflow_id = access_context
    with db_engine.connect() as connection:
        with pytest.raises(WorkflowModuleForbidden) as error:
            apply_module_rights(
                connection, workflow_id, [_input_instance(macro_id, macro_version=99)], user
            )
    assert str(error.value) == "Keine Berechtigung für dieses Modul"


@pytest.mark.parametrize("access", ["use", "edit", "owner"])
def test_existing_module_with_write_access_accepts_input(access_context, access):
    db_engine, _, user, macro_id, workflow_id = access_context
    with db_engine.begin() as connection:
        if access == "owner":
            connection.execute(text(
                "UPDATE macros SET owner_user_id = :user_id WHERE id = :macro_id"
            ), {"user_id": user.id, "macro_id": macro_id})
        else:
            _share(connection, macro_id, user.id, access)
    module = _input_instance(macro_id)
    original = deepcopy(module)
    with db_engine.connect() as connection:
        result = apply_module_rights(connection, workflow_id, [module], user)[0]
    assert result == {**original, "type_id": macro_id}
    assert module == original


def test_existing_view_access_cannot_change_instance_name(access_context):
    db_engine, _, user, macro_id, workflow_id = access_context
    with db_engine.begin() as connection:
        _share(connection, macro_id, user.id, "view")
    module = _input_instance(macro_id)
    with db_engine.connect() as connection:
        result = apply_module_rights(connection, workflow_id, [module], user)[0]
    assert result["name"] == "Saved name"
    assert result["description"] == "Saved description"
    assert result["settings"] == {"target": "saved-target", "prefix": "saved-prefix"}


@pytest.mark.parametrize("share_access", [None, "view"])
def test_new_private_or_view_only_module_is_forbidden(access_context, share_access):
    db_engine, _, user, macro_id, workflow_id = access_context
    if share_access is not None:
        with db_engine.begin() as connection:
            _share(connection, macro_id, user.id, share_access)
    module = _stored_instance(macro_id, "new")
    with db_engine.connect() as connection:
        with pytest.raises(WorkflowModuleForbidden) as error:
            apply_module_rights(connection, workflow_id, [module], user)
    assert str(error.value) == "Keine Berechtigung für dieses Modul"


def test_new_unknown_module_is_forbidden_to_non_admin(access_context):
    db_engine, _, user, _, workflow_id = access_context
    module = _stored_instance("missing", "new")
    with db_engine.connect() as connection:
        with pytest.raises(WorkflowModuleForbidden):
            apply_module_rights(connection, workflow_id, [module], user)


@pytest.mark.parametrize("global_share", [False, True])
def test_new_module_with_use_share_is_allowed(access_context, global_share):
    db_engine, _, user, macro_id, workflow_id = access_context
    with db_engine.begin() as connection:
        _share(connection, macro_id, None if global_share else user.id, "use")
    module = _stored_instance(macro_id, "new")
    original = deepcopy(module)
    with db_engine.connect() as connection:
        result = apply_module_rights(connection, workflow_id, [module], user)
    assert result == [original]
    assert result[0] is not module
    assert module == original


def test_existing_id_with_different_macro_is_checked_as_new(access_context):
    db_engine, admin, user, _, workflow_id = access_context
    with db_engine.begin() as connection:
        other_macro_id = create_macro(connection, _macro_definition("Other macro"), admin)
    module = _stored_instance(other_macro_id, "stored")
    with db_engine.connect() as connection:
        with pytest.raises(WorkflowModuleForbidden):
            apply_module_rights(connection, workflow_id, [module], user)


def test_admin_unknown_new_module_reaches_existing_validation(access_context):
    db_engine, admin, _, _, workflow_id = access_context
    module = _stored_instance("missing", "new")
    with db_engine.begin() as connection:
        with pytest.raises(WorkflowValidationError, match="^Unbekanntes Modul: missing$"):
            save_graph(connection, workflow_id, 2, [module], [], user=admin)




def test_missing_workflow_returns_independent_copies(access_context):
    db_engine, _, user, macro_id, _ = access_context
    modules = [_stored_instance(macro_id)]
    with db_engine.connect() as connection:
        result = apply_module_rights(connection, "missing-workflow", modules, user)
    assert result == modules
    assert result[0] is not modules[0]


def test_explicit_null_type_id_is_new_and_forbidden(access_context):
    db_engine, _, user, macro_id, workflow_id = access_context
    module = _input_instance(macro_id)
    module["type_id"] = None
    with db_engine.connect() as connection:
        with pytest.raises(WorkflowModuleForbidden):
            apply_module_rights(connection, workflow_id, [module], user)


def test_view_existing_unknown_version_reaches_graph_validation(access_context):
    db_engine, _, user, macro_id, workflow_id = access_context
    with db_engine.begin() as connection:
        _share(connection, macro_id, user.id, "view")
    with db_engine.begin() as connection:
        with pytest.raises(WorkflowValidationError, match="Unbekannte Modulversion"):
            save_graph(
                connection,
                workflow_id,
                2,
                [_input_instance(macro_id, macro_version=99)],
                [],
                user=user,
            )
        current_version = connection.execute(text(
            "SELECT current_version FROM workflows WHERE id = :workflow_id"
        ), {"workflow_id": workflow_id}).scalar_one()
    assert current_version == 2


def test_access_is_resolved_once_for_all_affected_macros(access_context, monkeypatch):
    db_engine, admin, user, macro_id, workflow_id = access_context
    with db_engine.begin() as connection:
        second_macro = create_macro(connection, _macro_definition("Second macro"), admin)
        _share(connection, second_macro, user.id, "use")

    calls = []
    original_map = workflow_writes.object_access_map

    def track_access_map(connection, actor, object_type, object_ids):
        calls.append((actor, object_type, list(object_ids)))
        return original_map(connection, actor, object_type, object_ids)

    monkeypatch.setattr(workflow_writes, "object_access_map", track_access_map)
    modules = [_input_instance(macro_id), _stored_instance(second_macro, "new")]
    with db_engine.connect() as connection:
        result = apply_module_rights(connection, workflow_id, modules, user)

    assert len(calls) == 1
    assert calls[0][0] is user
    assert calls[0][1] == "macro"
    assert set(calls[0][2]) == {macro_id, second_macro}
    assert len(result) == 2


def test_save_graph_persists_saved_values_for_blind_existing_module(access_context):
    db_engine, _, user, macro_id, workflow_id = access_context
    module = _input_instance(macro_id)
    with db_engine.begin() as connection:
        assert save_graph(connection, workflow_id, 2, [module], [], user=user) == 3
        saved = connection.execute(text(
            "SELECT macro_id, macro_version, name, description, settings, position_x, position_y, locked "
            "FROM workflow_instances WHERE workflow_id = :workflow_id AND version = 3 AND id = 'stored'"
        ), {"workflow_id": workflow_id}).mappings().one()
    assert dict(saved) == {
        "macro_id": macro_id,
        "macro_version": 1,
        "name": "Saved name",
        "description": "Saved description",
        "settings": {"target": "saved-target", "prefix": "saved-prefix"},
        "position_x": 8.0,
        "position_y": 9.0,
        "locked": False,
    }


def test_rejected_graph_does_not_create_a_new_version(access_context):
    db_engine, _, user, macro_id, workflow_id = access_context
    with db_engine.begin() as connection:
        _share(connection, macro_id, user.id, "view")
    with db_engine.begin() as connection:
        with pytest.raises(WorkflowModuleForbidden):
            save_graph(connection, workflow_id, 2, [_stored_instance(macro_id, "new")], [], user=user)
        current_version = connection.execute(text(
            "SELECT current_version FROM workflows WHERE id = :workflow_id"
        ), {"workflow_id": workflow_id}).scalar_one()
        inserted_versions = connection.execute(text(
            "SELECT count(*) FROM workflow_versions WHERE workflow_id = :workflow_id AND version = 3"
        ), {"workflow_id": workflow_id}).scalar_one()
    assert current_version == 2
    assert inserted_versions == 0
