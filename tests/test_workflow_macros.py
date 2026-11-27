"""Workflow macro persistence, impact, and engine-equivalence tests."""

# IMPORTS
import copy

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from auth_helpers import sign_in
from marathon.server.app import create_app
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.macro_writes import create_macro, update_macro
from marathon.server.db.workflow_checks import workflow_invalid_modules
from marathon.server.db import workflow_writes
from test_engine_e2e import (
    _asset_rows,
    _connection,
    _insert_asset,
    _job_statuses,
    _module,
    _running_server_and_worker,
    _wait_until,
    _watch_settings,
    _workflow as _e2e_workflow,
)


# FIXTURES
@pytest.fixture
def macro_client(db_engine, tmp_path):
    """Provide a catalog-synced API client and admin macro owner."""
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id, email, display_name, role) "
            "VALUES ('test-admin', 'admin@test.local', 'Test Admin', 'admin')"
        ))
    app = create_app(static_dir=tmp_path, engine=db_engine, sync_catalog=True)
    sign_in(app)
    with TestClient(app) as client:
        yield client, db_engine, AuthUser("test-admin", "admin@test.local", "Test Admin", "admin", "test-session")


# HELPERS
def _workflow(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/workflows", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _save(client: TestClient, workflow_id: str, version: int, modules: list[dict], connections: list[dict] | None = None):
    return client.put(
        f"/api/v1/workflows/{workflow_id}/graph",
        json={"base_version": version, "modules": modules, "connections": connections or []},
    )


def _macro_definition(name: str = "Kopieren und umbenennen") -> dict:
    return {
        "name": name,
        "category": "Dateien",
        "icon": "copy",
        "description": "",
        "sort_order": 50,
        "blocks": [
            {"id": "copy", "type_id": "copy_file", "name": "Kopieren", "position": {"x": 0, "y": 0}, "settings": {}},
            {"id": "rename", "type_id": "rename_file", "name": "Umbenennen", "position": {"x": 1, "y": 0}, "settings": {}},
        ],
        "connections": [{"source_block_id": "copy", "source_output_id": "success", "target_block_id": "rename"}],
        "entry_block_id": "copy",
        "outputs": [
            {"id": "success", "label": "Erfolg", "description": "", "sources": [{"block_id": "rename", "output_id": "success"}]},
            {"id": "error", "label": "Fehler", "description": "", "sources": [
                {"block_id": "copy", "output_id": "error"},
                {"block_id": "rename", "output_id": "error"},
            ]},
        ],
        "settings": [
            {"key": "target", "block_id": "copy", "setting_key": "target", "label": "Ziel", "help": ""},
            {"key": "prefix", "block_id": "rename", "setting_key": "prefix", "label": "Präfix", "help": ""},
        ],
    }


def _create_two_block_macro(db_engine, owner, name: str = "Kopieren und umbenennen") -> str:
    with db_engine.begin() as connection:
        return create_macro(connection, _macro_definition(name), owner)


def _macro_instance(instance_id: str, macro_id: str, name: str = "Paket", settings: dict | None = None, version: int | None = None) -> dict:
    result = {
        "id": instance_id,
        "type_id": macro_id,
        "name": name,
        "position": {"x": 4, "y": 8},
        "settings": settings or {},
        "locked": True,
    }
    if version is not None:
        result["macro_version"] = version
    return result


# TESTS
def test_standard_graph_stores_instances_and_runtime_rows(macro_client):
    client, db_engine, _ = macro_client
    workflow_id = _workflow(client, "Standardmodul")
    modules = [{
        "id": "m1", "type_id": "copy_file", "name": "Kopieren",
        "position": {"x": 1, "y": 2}, "settings": {},
    }]
    original = copy.deepcopy(modules)
    saved = _save(client, workflow_id, 1, modules)
    assert saved.status_code == 200, saved.text
    assert modules == original

    with db_engine.connect() as connection:
        instance = connection.execute(text(
            "SELECT id, macro_id, macro_version, name FROM workflow_instances "
            "WHERE workflow_id = :id AND version = 2"
        ), {"id": workflow_id}).mappings().one()
        runtime = connection.execute(text(
            "SELECT id, instance_id, block_id, type_id, name, description, position_x, position_y, locked, exits "
            "FROM workflow_modules WHERE workflow_id = :id AND version = 2"
        ), {"id": workflow_id}).mappings().all()
        resolved = workflow_writes.resolve_graph(connection, workflow_id, modules, [])
    assert dict(instance) == {"id": "m1", "macro_id": "copy_file", "macro_version": 1, "name": "Kopieren"}
    assert [dict(row) for row in runtime] == [{
        "id": "m1.main", "instance_id": "m1", "block_id": "main", "type_id": "copy_file",
        "name": "Kopieren", "description": "", "position_x": 1.0, "position_y": 2.0,
        "locked": False, "exits": {"success": "success", "error": "error"},
    }]
    assert modules == original
    assert resolved[0][0]["macro_version"] == 1
    assert resolved[2][0]["id"] == "m1.main"


def test_two_block_macro_persists_internal_and_external_connections(macro_client):
    client, db_engine, owner = macro_client
    macro_id = _create_two_block_macro(db_engine, owner)
    workflow_id = _workflow(client, "Zwei Bausteine")
    modules = [_macro_instance("m1", macro_id), {
        "id": "m2", "type_id": "rename_file", "name": "Danach",
        "position": {"x": 10, "y": 8}, "settings": {"suffix": "_ende"},
    }]
    connections = [{"source_module_id": "m1", "source_output_id": "success", "target_module_id": "m2"}]
    saved = _save(client, workflow_id, 1, modules, connections)
    assert saved.status_code == 200, saved.text
    with db_engine.connect() as connection:
        runtime = connection.execute(text(
            "SELECT id, instance_id, block_id, type_id, exits FROM workflow_modules "
            "WHERE workflow_id = :id AND version = 2 ORDER BY id"
        ), {"id": workflow_id}).mappings().all()
        edges = connection.execute(text(
            "SELECT source_module_id, source_output_id, target_module_id FROM workflow_connections "
            "WHERE workflow_id = :id AND version = 2 ORDER BY source_module_id, source_output_id"
        ), {"id": workflow_id}).mappings().all()
        instance_edge_count = connection.execute(text(
            "SELECT count(*) FROM workflow_instance_connections WHERE workflow_id = :id AND version = 2"
        ), {"id": workflow_id}).scalar_one()
    assert [(row["id"], row["instance_id"], row["block_id"], row["type_id"]) for row in runtime] == [
        ("m1.copy", "m1", "copy", "copy_file"),
        ("m1.rename", "m1", "rename", "rename_file"),
        ("m2.main", "m2", "main", "rename_file"),
    ]
    assert runtime[0]["exits"] == {"error": "error"}
    assert runtime[1]["exits"] == {"success": "success", "error": "error"}
    assert [(row["source_module_id"], row["source_output_id"], row["target_module_id"]) for row in edges] == [
        ("m1.copy", "success", "m1.rename"),
        ("m1.rename", "success", "m2.main"),
    ]
    assert instance_edge_count == 1


def test_none_uses_current_version_and_saved_instance_stays_pinned(macro_client):
    client, db_engine, owner = macro_client
    macro_id = _create_two_block_macro(db_engine, owner)
    first_workflow = _workflow(client, "Angeheftet")
    first = _save(client, first_workflow, 1, [_macro_instance("old", macro_id)])
    assert first.status_code == 200, first.text
    updated_definition = _macro_definition("Neue Modulversion")
    updated_definition["blocks"][1]["settings"]["suffix"] = "_v2"
    with db_engine.begin() as connection:
        assert update_macro(connection, macro_id, 1, updated_definition, owner) == 2
    second_workflow = _workflow(client, "Aktuelle Version")
    second = _save(client, second_workflow, 1, [_macro_instance("new", macro_id)])
    assert second.status_code == 200, second.text
    with db_engine.connect() as connection:
        versions = connection.execute(text(
            "SELECT workflow_id, macro_version FROM workflow_instances WHERE workflow_id IN (:first, :second) "
            "ORDER BY workflow_id"
        ), {"first": first_workflow, "second": second_workflow}).all()
    assert {row[0]: row[1] for row in versions} == {first_workflow: 1, second_workflow: 2}


def test_deleted_macro_allowed_only_for_existing_exact_version(macro_client):
    client, db_engine, owner = macro_client
    macro_id = _create_two_block_macro(db_engine, owner)
    workflow_id = _workflow(client, "Bestehende gelöschte Instanz")
    initial = [_macro_instance("m1", macro_id)]
    assert _save(client, workflow_id, 1, initial).status_code == 200
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE macros SET archived_at = now() WHERE id = :id"), {"id": macro_id})
    allowed = _save(client, workflow_id, 2, initial)
    assert allowed.status_code == 200, allowed.text
    other_workflow = _workflow(client, "Neue gelöschte Instanz")
    rejected = _save(client, other_workflow, 1, [_macro_instance("m1", macro_id)])
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == f"Modul ist gelöscht: {macro_id}"


def test_unknown_macro_version_is_rejected(macro_client):
    client, db_engine, owner = macro_client
    macro_id = _create_two_block_macro(db_engine, owner)
    workflow_id = _workflow(client, "Unbekannte Version")
    rejected = _save(client, workflow_id, 1, [_macro_instance("m1", macro_id, version=9)])
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == f"Unbekannte Modulversion: {macro_id} v9"


def test_impact_groups_removed_runtime_modules_by_instance(macro_client):
    client, db_engine, owner = macro_client
    macro_id = _create_two_block_macro(db_engine, owner)
    workflow_id = _workflow(client, "Impact")
    saved = _save(client, workflow_id, 1, [_macro_instance("m1", macro_id)])
    assert saved.status_code == 200, saved.text
    with db_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status) VALUES "
            "('asset-copy', :workflow_id, 'Copy', 'in_progress', 'm1.copy', 'running'), "
            "('asset-rename', :workflow_id, 'Rename', 'in_progress', 'm1.rename', 'running')"
        ), {"workflow_id": workflow_id})
        connection.execute(text(
            "INSERT INTO jobs (id, workflow_id, module_id, status) VALUES "
            "('job-copy', :workflow_id, 'm1.copy', 'running'), "
            "('job-rename', :workflow_id, 'm1.rename', 'assigned')"
        ), {"workflow_id": workflow_id})
    response = client.post(f"/api/v1/workflows/{workflow_id}/graph/impact", json={
        "base_version": 2, "modules": [], "connections": [],
    })
    assert response.status_code == 200, response.text
    assert response.json()["removed_modules"] == [{"id": "m1", "name": "Paket", "assets": 2, "jobs": 2}]
    assert response.json()["assets"] == 2
    assert response.json()["jobs"] == 2


def test_duplicate_copies_instance_and_runtime_graph_rows(macro_client):
    client, db_engine, owner = macro_client
    macro_id = _create_two_block_macro(db_engine, owner)
    workflow_id = _workflow(client, "Kopierbarer Graph")
    saved = _save(
        client,
        workflow_id,
        1,
        [_macro_instance("m1", macro_id), {
            "id": "m2", "type_id": "rename_file", "name": "Danach",
            "position": {"x": 8, "y": 10}, "settings": {},
        }],
        [{"source_module_id": "m1", "source_output_id": "success", "target_module_id": "m2"}],
    )
    assert saved.status_code == 200, saved.text
    duplicated = client.post(f"/api/v1/workflows/{workflow_id}/duplicate")
    assert duplicated.status_code == 201, duplicated.text
    copy_id = duplicated.json()["id"]
    with db_engine.connect() as connection:
        instances = connection.execute(text(
            "SELECT id, macro_id, macro_version, name FROM workflow_instances WHERE workflow_id = :id AND version = 1"
        ), {"id": copy_id}).mappings().all()
        runtime = connection.execute(text(
            "SELECT id, instance_id, block_id, type_id, exits FROM workflow_modules WHERE workflow_id = :id AND version = 1 ORDER BY id"
        ), {"id": copy_id}).mappings().all()
        source_edges = connection.execute(text(
            "SELECT count(*) FROM workflow_instance_connections WHERE workflow_id = :id AND version = 1"
        ), {"id": copy_id}).scalar_one()
        runtime_edges = connection.execute(text(
            "SELECT count(*) FROM workflow_connections WHERE workflow_id = :id AND version = 1"
        ), {"id": copy_id}).scalar_one()
    assert [dict(row) for row in instances] == [
        {"id": "m1", "macro_id": macro_id, "macro_version": 1, "name": "Paket"},
        {"id": "m2", "macro_id": "rename_file", "macro_version": 1, "name": "Danach"},
    ]
    assert [row["id"] for row in runtime] == ["m1.copy", "m1.rename", "m2.main"]
    assert runtime[0]["exits"] == {"error": "error"}
    assert source_edges == 1
    assert runtime_edges == 2


def test_invalid_macro_blocks_map_once_to_instance(macro_client):
    client, db_engine, owner = macro_client
    definition = {
        "name": "Ungültige Wartefolge", "category": "Test", "icon": "workflow", "description": "", "sort_order": 1,
        "blocks": [
            {"id": "first", "type_id": "wait", "name": "Warten 1", "position": {"x": 0, "y": 0}, "settings": {"wait_s": 0}},
            {"id": "second", "type_id": "wait", "name": "Warten 2", "position": {"x": 1, "y": 0}, "settings": {"wait_s": 0}},
        ],
        "connections": [{"source_block_id": "first", "source_output_id": "success", "target_block_id": "second"}],
        "entry_block_id": "first",
        "outputs": [{"id": "success", "label": "Erfolg", "description": "", "sources": [{"block_id": "second", "output_id": "success"}]}],
        "settings": [],
    }
    with db_engine.begin() as connection:
        macro_id = create_macro(connection, definition, owner)
    workflow_id = _workflow(client, "Ungültige Instanz")
    saved = _save(client, workflow_id, 1, [_macro_instance("only-instance", macro_id, "Warten")])
    assert saved.status_code == 200, saved.text
    with db_engine.connect() as connection:
        assert workflow_invalid_modules(connection, workflow_id) == [{"id": "only-instance", "name": "Warten"}]


def test_active_workflow_logs_one_invalid_runtime_block_per_instance(macro_client):
    client, db_engine, owner = macro_client
    definition = {
        "name": "Gültige Wartefolge", "category": "Test", "icon": "workflow", "description": "", "sort_order": 1,
        "blocks": [
            {"id": "first", "type_id": "wait", "name": "Warten 1", "position": {"x": 0, "y": 0}, "settings": {"wait_s": 1}},
            {"id": "second", "type_id": "wait", "name": "Warten 2", "position": {"x": 1, "y": 0}, "settings": {"wait_s": 1}},
        ],
        "connections": [{"source_block_id": "first", "source_output_id": "success", "target_block_id": "second"}],
        "entry_block_id": "first",
        "outputs": [{"id": "success", "label": "Erfolg", "description": "", "sources": [{"block_id": "second", "output_id": "success"}]}],
        "settings": [],
    }
    with db_engine.begin() as connection:
        macro_id = create_macro(connection, definition, owner)
    workflow_id = _workflow(client, "Aktive ungültige Instanz")
    saved = _save(client, workflow_id, 1, [_macro_instance("only-instance", macro_id, "Warten")])
    assert saved.status_code == 200, saved.text
    activated = client.put(f"/api/v1/workflows/{workflow_id}/active", json={"active": True})
    assert activated.status_code == 200, activated.text

    invalid_definition = copy.deepcopy(definition)
    invalid_definition["name"] = "Ungültige Wartefolge"
    for block in invalid_definition["blocks"]:
        block["settings"]["wait_s"] = 0
    with db_engine.begin() as connection:
        assert update_macro(connection, macro_id, 1, invalid_definition, owner) == 2
    saved_invalid = _save(client, workflow_id, 2, [_macro_instance("only-instance", macro_id, "Warten")])
    assert saved_invalid.status_code == 200, saved_invalid.text
    with db_engine.connect() as connection:
        logs = connection.execute(text(
            "SELECT module_id, level, text FROM log_lines WHERE workflow_id = :id ORDER BY id"
        ), {"id": workflow_id}).mappings().all()
    assert [dict(row) for row in logs] == [{
        "module_id": "only-instance.first",
        "level": "warn",
        "text": "Workflow gestoppt: Einstellungen prüfen",
    }]


def test_macro_two_block_engine_history_matches_two_standard_modules(db_engine, tmp_path):
    from marathon.server.db.macro_writes import create_macro as create_macro_in_db

    source_dirs = [tmp_path / "macro-source", tmp_path / "standard-source"]
    target_dirs = [tmp_path / "macro-target", tmp_path / "standard-target"]
    for source_dir, target_dir in zip(source_dirs, target_dirs):
        source_dir.mkdir()
        target_dir.mkdir()
        (source_dir / "clip.mov").write_bytes(b"macro workflow equivalence")

    with _running_server_and_worker(db_engine, tmp_path) as url, httpx.Client(base_url=url, timeout=5) as client:
        owner = AuthUser("test-admin", "admin@test.local", "Test Admin", "admin", "test-session")
        with db_engine.begin() as connection:
            macro_id = create_macro_in_db(connection, _macro_definition(), owner)
        macro_workflow = _e2e_workflow(
            client,
            "Macro Engine Verlauf",
            [
                _module("watch", "watchfolder", _watch_settings(source_dirs[0])),
                _module("pack", macro_id, {"target": str(target_dirs[0]), "prefix": "done_"}),
            ],
            [_connection("watch", "success", "pack")],
        )
        standard_workflow = _e2e_workflow(
            client,
            "Standard Engine Verlauf",
            [
                _module("watch", "watchfolder", _watch_settings(source_dirs[1])),
                _module("copy", "copy_file", {"target": str(target_dirs[1])}),
                _module("rename", "rename_file", {"prefix": "done_"}),
            ],
            [_connection("watch", "success", "copy"), _connection("copy", "success", "rename")],
        )
        macro_asset = _insert_asset(db_engine, macro_workflow, str(source_dirs[0] / "clip.mov"))
        standard_asset = _insert_asset(db_engine, standard_workflow, str(source_dirs[1] / "clip.mov"))

        def finished() -> bool:
            macro_jobs = _job_statuses(db_engine, macro_workflow)
            standard_jobs = _job_statuses(db_engine, standard_workflow)
            macro_assets = _asset_rows(db_engine, macro_workflow)
            standard_assets = _asset_rows(db_engine, standard_workflow)
            return (
                len(macro_jobs) == len(standard_jobs) == 2
                and all(status == "succeeded" for status in macro_jobs + standard_jobs)
                and len(macro_assets) == len(standard_assets) == 2
                and all(row["status"] == "done" for row in macro_assets + standard_assets)
            )

        _wait_until(finished, 20, "Makro- und Standardgraph wurden nicht vollständig abgeschlossen")
        assert (target_dirs[0] / "done_clip.mov").read_bytes() == b"macro workflow equivalence"
        assert (target_dirs[1] / "done_clip.mov").read_bytes() == b"macro workflow equivalence"
        with db_engine.connect() as connection:
            histories = {}
            for label, workflow_id in (("macro", macro_workflow), ("standard", standard_workflow)):
                jobs = connection.execute(text(
                    "SELECT module_id, type_id, attempt, status FROM jobs WHERE workflow_id = :id ORDER BY type_id, module_id"
                ), {"id": workflow_id}).mappings().all()
                logs = connection.execute(text(
                    "SELECT module_id, level, text FROM log_lines WHERE workflow_id = :id ORDER BY id"
                ), {"id": workflow_id}).mappings().all()
                assets = connection.execute(text(
                    "SELECT id, status, current_module_id, attempts FROM assets WHERE workflow_id = :id ORDER BY id"
                ), {"id": workflow_id}).mappings().all()
                histories[label] = (jobs, logs, assets)
    macro_jobs, macro_logs, macro_assets = histories["macro"]
    standard_jobs, standard_logs, standard_assets = histories["standard"]
    assert [(row["type_id"], row["attempt"], row["status"]) for row in macro_jobs] == [
        ("copy_file", 1, "succeeded"), ("rename_file", 1, "succeeded"),
    ]
    assert [(row["type_id"], row["attempt"], row["status"]) for row in macro_jobs] == [
        (row["type_id"], row["attempt"], row["status"]) for row in standard_jobs
    ]
    assert sorted((row["status"], row["attempts"]) for row in macro_assets) == sorted(
        (row["status"], row["attempts"]) for row in standard_assets
    ) == [("done", 0), ("done", 0)]
    def normalized_logs(rows, workflow_kind: str):
        replacements = (
            (str(source_dirs[0] if workflow_kind == "macro" else source_dirs[1]), "<source>"),
            (str(target_dirs[0] if workflow_kind == "macro" else target_dirs[1]), "<target>"),
        )
        result = []
        for row in rows:
            module_id = row["module_id"]
            if workflow_kind == "macro":
                module_id = module_id.replace("pack.copy", "copy").replace("pack.rename", "rename")
            else:
                module_id = module_id.replace("copy.main", "copy").replace("rename.main", "rename")
            message = row["text"]
            for source, target in replacements:
                message = message.replace(source, target)
            result.append((module_id, row["level"], message))
        return result
    assert normalized_logs(macro_logs, "macro") == normalized_logs(standard_logs, "standard")
    assert macro_assets and standard_assets
    assert macro_asset in {row["id"] for row in macro_assets}
    assert standard_asset in {row["id"] for row in standard_assets}
    def normalized_assets(rows, workflow_kind: str):
        result = []
        for row in rows:
            module_id = row["current_module_id"] or ""
            if workflow_kind == "macro":
                module_id = module_id.replace("pack.copy", "copy").replace("pack.rename", "rename")
            else:
                module_id = module_id.replace("copy.main", "copy").replace("rename.main", "rename")
            result.append((row["status"], module_id, row["attempts"]))
        return sorted(result)
    assert normalized_assets(macro_assets, "macro") == normalized_assets(standard_assets, "standard")
