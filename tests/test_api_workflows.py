"""Workflow write API integration tests using isolated PostgreSQL."""

# IMPORTS
import json
import threading
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.server.app import create_app
from marathon.server.db.workflow_writes import WorkflowValidationError, create_workflow


# FUNC
def _insert_module_types(db_engine) -> None:
    """Insert module types used by graph write tests."""
    module_types = (
        (
            "source",
            "Quelle",
            False,
            [{"id": "success", "label": "Weiter", "description": ""}],
        ),
        (
            "step",
            "Schritt",
            True,
            [
                {"id": "success", "label": "Weiter", "description": ""},
                {"id": "failure", "label": "Fehler", "description": ""},
            ],
        ),
    )
    with db_engine.begin() as connection:
        for type_id, name, has_input, outputs in module_types:
            connection.execute(
                text(
                    "INSERT INTO module_types (id, name, category, icon, has_input, outputs) "
                    "VALUES (:id, :name, 'test', 'test', :has_input, CAST(:outputs AS jsonb))"
                ),
                {
                    "id": type_id,
                    "name": name,
                    "has_input": has_input,
                    "outputs": json.dumps(outputs),
                },
            )


def _module(module_id: str, type_id: str = "step", name: str = "Modul") -> dict:
    return {
        "id": module_id,
        "type_id": type_id,
        "name": name,
        "position": {"x": 1.5, "y": 2.5},
    }


# FIXTURES
@pytest.fixture
def api_client(db_engine, tmp_path):
    """Provide a client backed by the isolated test database."""
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        yield client


# TESTS
def test_create_update_and_delete_workflow(api_client):
    created = api_client.post(
        "/api/v1/workflows",
        json={"name": "  Alpha  ", "description": "  Beschreibung  ", "priority": 4},
    )
    assert created.status_code == 201
    workflow = created.json()
    assert workflow["version"] == 1
    assert workflow["active"] is False
    assert workflow["id"].startswith("wf-")
    assert workflow["name"] == "Alpha"
    assert workflow["description"] == "Beschreibung"

    graph = api_client.get(f"/api/v1/workflows/{workflow['id']}/graph")
    assert graph.status_code == 200
    assert graph.json()["modules"] == []
    assert graph.json()["connections"] == []

    updated = api_client.put(
        f"/api/v1/workflows/{workflow['id']}",
        json={"name": "Beta", "description": "Neue Beschreibung", "priority": 9},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Beta"
    assert updated.json()["description"] == "Neue Beschreibung"
    assert updated.json()["priority"] == 9
    assert api_client.get(f"/api/v1/workflows/{workflow['id']}").json()["name"] == "Beta"

    deleted = api_client.delete(f"/api/v1/workflows/{workflow['id']}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert api_client.get(f"/api/v1/workflows/{workflow['id']}").status_code == 404
    repeated_delete = api_client.delete(f"/api/v1/workflows/{workflow['id']}")
    assert repeated_delete.status_code == 404
    assert repeated_delete.json()["detail"] == "Workflow nicht gefunden"


def test_workflow_name_validation(api_client):
    missing = api_client.post("/api/v1/workflows", json={"name": "   "})
    assert missing.status_code == 422
    assert missing.json()["detail"] == "Name fehlt"

    too_long = api_client.post("/api/v1/workflows", json={"name": "x" * 201})
    assert too_long.status_code == 422
    assert too_long.json()["detail"] == "Name ist zu lang (max. 200 Zeichen)"

    created = api_client.post("/api/v1/workflows", json={"name": "Alpha"})
    assert created.status_code == 201
    duplicate = api_client.post("/api/v1/workflows", json={"name": "ALPHA"})
    assert duplicate.status_code == 422
    assert duplicate.json()["detail"] == "Ein Workflow mit diesem Namen existiert bereits"

    same_name = api_client.put(
        f"/api/v1/workflows/{created.json()['id']}", json={"name": "Alpha"}
    )
    assert same_name.status_code == 200
    assert same_name.json()["name"] == "Alpha"


def test_save_graph_creates_new_version(api_client, db_engine):
    _insert_module_types(db_engine)
    workflow = api_client.post("/api/v1/workflows", json={"name": "Graph"}).json()
    graph_body = {
        "base_version": 1,
        "modules": [
            {
                "id": "m1",
                "type_id": "source",
                "name": "Quelle",
                "position": {"x": 10, "y": 20},
                "locked": True,
            },
            {
                "id": "m2",
                "type_id": "step",
                "name": "Schritt",
                "description": "Zeile 1\nZeile 2",
                "position": {"x": 30, "y": 40},
                "settings": {"duration_s": 5},
            },
        ],
        "connections": [
            {
                "source_module_id": "m1",
                "source_output_id": "success",
                "target_module_id": "m2",
            }
        ],
    }
    saved = api_client.put(f"/api/v1/workflows/{workflow['id']}/graph", json=graph_body)
    assert saved.status_code == 200
    graph = saved.json()
    assert graph["version"] == 2
    modules = {module["id"]: module for module in graph["modules"]}
    assert modules["m1"]["type_id"] == "source"
    assert modules["m1"]["locked"] is True
    assert modules["m1"]["position"] == {"x": 10.0, "y": 20.0}
    assert modules["m2"]["description"] == "Zeile 1\nZeile 2"
    assert modules["m2"]["settings"] == {"duration_s": 5}
    assert modules["m2"]["position"] == {"x": 30.0, "y": 40.0}
    assert [
        {key: item[key] for key in ("source_module_id", "source_output_id", "target_module_id")}
        for item in graph["connections"]
    ] == graph_body["connections"]
    assert api_client.get(f"/api/v1/workflows/{workflow['id']}").json()["version"] == 2
    with db_engine.connect() as connection:
        version_count = connection.execute(
            text("SELECT COUNT(*) FROM workflow_versions WHERE workflow_id = :id"),
            {"id": workflow["id"]},
        ).scalar_one()
    assert version_count == 2


def test_save_graph_conflict(api_client, db_engine):
    workflow = api_client.post("/api/v1/workflows", json={"name": "Konflikt"}).json()
    body = {"base_version": 1, "modules": [], "connections": []}
    first = api_client.put(f"/api/v1/workflows/{workflow['id']}/graph", json=body)
    assert first.status_code == 200
    second = api_client.put(f"/api/v1/workflows/{workflow['id']}/graph", json=body)
    assert second.status_code == 409
    assert second.json()["detail"] == "Workflow wurde zwischenzeitlich geändert. Bitte neu laden."
    with db_engine.connect() as connection:
        current_version = connection.execute(
            text("SELECT current_version FROM workflows WHERE id = :id"),
            {"id": workflow["id"]},
        ).scalar_one()
    assert current_version == 2


@pytest.mark.parametrize(
    ("modules", "connections", "expected_detail"),
    [
        ([_module("bad id", "source")], [], "Ungültige Modul-ID: bad id"),
        (
            [_module("m1", "source"), _module("m1", "step")],
            [],
            "Modul-ID doppelt: m1",
        ),
        ([_module("m1", "missing")], [], "Unbekannter Modultyp: missing"),
        ([_module("m1", "source", "  ")], [], "Modulname fehlt: m1"),
        (
            [_module("m1", "source"), _module("m2")],
            [{"source_module_id": "nope", "source_output_id": "success", "target_module_id": "m2"}],
            "Verbindung zu unbekanntem Modul: nope",
        ),
        (
            [_module("m1", "source")],
            [{"source_module_id": "m1", "source_output_id": "success", "target_module_id": "nope"}],
            "Verbindung zu unbekanntem Modul: nope",
        ),
        (
            [_module("m1", "source")],
            [{"source_module_id": "m1", "source_output_id": "success", "target_module_id": "m1"}],
            "Modul m1 kann nicht mit sich selbst verbunden werden",
        ),
        (
            [_module("m1", "source"), _module("m2")],
            [{"source_module_id": "m1", "source_output_id": "missing", "target_module_id": "m2"}],
            "Unbekannter Ausgang missing an Modul m1",
        ),
        (
            [_module("m1", "source"), _module("m2", "source")],
            [{"source_module_id": "m1", "source_output_id": "success", "target_module_id": "m2"}],
            "Modul m2 hat keinen Eingang",
        ),
        (
            [_module("m1", "source"), _module("m2"), _module("m3")],
            [
                {"source_module_id": "m1", "source_output_id": "success", "target_module_id": "m2"},
                {"source_module_id": "m1", "source_output_id": "success", "target_module_id": "m3"},
            ],
            "Ausgang success an Modul m1 ist mehrfach verbunden",
        ),
    ],
)
def test_save_graph_validation(api_client, db_engine, modules, connections, expected_detail):
    _insert_module_types(db_engine)
    workflow = api_client.post("/api/v1/workflows", json={"name": "Validierung"}).json()
    response = api_client.put(
        f"/api/v1/workflows/{workflow['id']}/graph",
        json={"base_version": 1, "modules": modules, "connections": connections},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == expected_detail
    with db_engine.connect() as connection:
        current_version = connection.execute(
            text("SELECT current_version FROM workflows WHERE id = :id"),
            {"id": workflow["id"]},
        ).scalar_one()
        version_count = connection.execute(
            text("SELECT COUNT(*) FROM workflow_versions WHERE workflow_id = :id"),
            {"id": workflow["id"]},
        ).scalar_one()
    assert current_version == 1
    assert version_count == 1


def test_delete_cascades_assets_and_jobs(api_client, db_engine):
    workflow = api_client.post("/api/v1/workflows", json={"name": "Kaskade"}).json()
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO assets (id, workflow_id, name, status) "
                "VALUES ('asset-1', :workflow_id, 'Asset', 'pending')"
            ),
            {"workflow_id": workflow["id"]},
        )
        connection.execute(
            text(
                "INSERT INTO jobs (id, workflow_id, module_id, status) "
                "VALUES ('job-1', :workflow_id, 'm1', 'open')"
            ),
            {"workflow_id": workflow["id"]},
        )
        connection.execute(
            text("INSERT INTO job_assets (job_id, asset_id) VALUES ('job-1', 'asset-1')")
        )

    deleted = api_client.delete(f"/api/v1/workflows/{workflow['id']}")
    assert deleted.status_code == 204
    with db_engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM assets WHERE workflow_id = :id"), {"id": workflow["id"]}
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM jobs WHERE workflow_id = :id"), {"id": workflow["id"]}
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM job_assets WHERE job_id = 'job-1'")
        ).scalar_one() == 0


def test_unknown_workflow_returns_404(api_client):
    missing = {"name": "Test", "description": "", "priority": 0}
    put = api_client.put("/api/v1/workflows/nope", json=missing)
    assert put.status_code == 404
    assert put.json()["detail"] == "Workflow nicht gefunden"

    deleted = api_client.delete("/api/v1/workflows/nope")
    assert deleted.status_code == 404
    assert deleted.json()["detail"] == "Workflow nicht gefunden"

    graph = api_client.put(
        "/api/v1/workflows/nope/graph",
        json={"base_version": 1, "modules": [], "connections": []},
    )
    assert graph.status_code == 404
    assert graph.json()["detail"] == "Workflow nicht gefunden"


def test_concurrent_create_with_same_name_is_rejected(db_engine):
    first = db_engine.connect()
    tx = first.begin()
    result: dict[str, str] = {}

    def create_second_workflow() -> None:
        try:
            with db_engine.begin() as connection:
                create_workflow(connection, "parallel", "", 0)
            result["result"] = "created"
        except WorkflowValidationError as exc:
            result["result"] = str(exc)

    thread = threading.Thread(target=create_second_workflow)
    try:
        create_workflow(first, "Parallel", "", 0)
        thread.start()
        deadline = monotonic() + 5
        with db_engine.connect() as observer:
            while not observer.execute(text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND NOT granted)"
            )).scalar_one():
                assert thread.is_alive() and monotonic() < deadline, "Zweiter Aufruf wartet nicht auf die Namenssperre"
                sleep(0.05)
        tx.commit()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert result.get("result") == "Ein Workflow mit diesem Namen existiert bereits"
        with db_engine.connect() as connection:
            workflow_count = connection.execute(
                text("SELECT COUNT(*) FROM workflows WHERE lower(name) = 'parallel'")
            ).scalar_one()
        assert workflow_count == 1
    finally:
        if tx.is_active:
            tx.rollback()
        first.close()
        if thread.ident is not None:
            thread.join(timeout=10)
