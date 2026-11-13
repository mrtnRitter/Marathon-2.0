"""Workflow write API integration tests using isolated PostgreSQL."""

# IMPORTS
import json
import threading
from datetime import datetime, timedelta, timezone
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from marathon.server.app import create_app
from marathon.server.db import workflow_writes
from marathon.server.db.workflow_writes import WorkflowValidationError, create_workflow, update_workflow


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
        json={"name": "  Alpha  ", "description": "  Beschreibung  "},
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
        json={"name": "Beta", "description": "Neue Beschreibung"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Beta"
    assert updated.json()["description"] == "Neue Beschreibung"
    assert "priority" not in updated.json()
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
    missing = {"name": "Test", "description": ""}
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
                create_workflow(connection, "parallel", "")
            result["result"] = "created"
        except WorkflowValidationError as exc:
            result["result"] = str(exc)

    thread = threading.Thread(target=create_second_workflow)
    try:
        create_workflow(first, "Parallel", "")
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


def test_list_has_no_priority(api_client):
    created = api_client.post("/api/v1/workflows", json={"name": "Ohne Priorität"})
    assert created.status_code == 201

    response = api_client.get("/api/v1/workflows")

    assert response.status_code == 200
    assert all("priority" not in workflow for workflow in response.json())


def test_list_sorted_by_name_case_insensitive(api_client):
    for name in ("b-zwei", "A-eins", "c-drei"):
        response = api_client.post("/api/v1/workflows", json={"name": name})
        assert response.status_code == 201

    listed = api_client.get("/api/v1/workflows")

    assert [workflow["name"] for workflow in listed.json()] == ["A-eins", "b-zwei", "c-drei"]


def test_set_active_toggles(api_client):
    workflow = api_client.post("/api/v1/workflows", json={"name": "Aktiv"}).json()
    assert workflow["active"] is False

    enabled = api_client.put(f"/api/v1/workflows/{workflow['id']}/active", json={"active": True})
    assert enabled.status_code == 200
    assert enabled.json()["active"] is True
    assert api_client.get(f"/api/v1/workflows/{workflow['id']}").json()["active"] is True

    disabled = api_client.put(f"/api/v1/workflows/{workflow['id']}/active", json={"active": False})
    assert disabled.status_code == 200
    assert disabled.json()["active"] is False


def test_set_active_clears_scheduled_flag(api_client, db_engine):
    workflow = api_client.post("/api/v1/workflows", json={"name": "Zeitplan aktiv"}).json()
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE workflows SET active = true, scheduled_active = true WHERE id = :id"),
            {"id": workflow["id"]},
        )

    response = api_client.put(
        f"/api/v1/workflows/{workflow['id']}/active", json={"active": True}
    )

    assert response.status_code == 200
    with db_engine.connect() as connection:
        scheduled_active = connection.execute(
            text("SELECT scheduled_active FROM workflows WHERE id = :id"),
            {"id": workflow["id"]},
        ).scalar_one()
    assert scheduled_active is False


def test_set_active_missing_returns_404(api_client):
    response = api_client.put("/api/v1/workflows/nope/active", json={"active": True})

    assert response.status_code == 404
    assert response.json()["detail"] == "Workflow nicht gefunden"


def test_set_active_rejects_non_bool(api_client):
    response = api_client.put("/api/v1/workflows/nope/active", json={"active": "maybe"})

    assert response.status_code == 422


def test_workflow_schedule_is_returned_for_list_and_detail(api_client, db_engine):
    workflow = api_client.post("/api/v1/workflows", json={"name": "Mit Zeitplan"}).json()
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflows SET schedule_every = 2, schedule_unit = 'days', "
                "schedule_start = '2026-01-01 10:00:00+00'::timestamptz, "
                "next_run_at = '2026-01-03 10:00:00+00'::timestamptz WHERE id = :id"
            ),
            {"id": workflow["id"]},
        )

    listed = api_client.get("/api/v1/workflows").json()[0]
    detail = api_client.get(f"/api/v1/workflows/{workflow['id']}").json()
    expected = {
        "every": 2,
        "unit": "days",
        "start": "2026-01-01T10:00:00Z",
        "next_run_at": "2026-01-03T10:00:00Z",
    }

    assert listed["schedule"] == expected
    assert detail["schedule"] == expected

    updated = api_client.put(
        f"/api/v1/workflows/{workflow['id']}",
        json={"name": "Zeitplan behalten", "description": "", "schedule": expected},
    )
    assert updated.status_code == 200
    assert updated.json()["schedule"] == expected


def test_create_with_schedule_normalizes(api_client):
    response = api_client.post(
        "/api/v1/workflows",
        json={
            "name": "S1",
            "schedule": {
                "every": 60,
                "unit": "minutes",
                "start": "2030-01-01T06:00:00+01:00",
            },
        },
    )

    assert response.status_code == 201
    schedule = response.json()["schedule"]
    assert schedule["every"] == 1
    assert schedule["unit"] == "hours"
    expected = datetime.fromisoformat("2030-01-01T05:00:00+00:00")
    assert datetime.fromisoformat(schedule["start"].replace("Z", "+00:00")) == expected
    assert datetime.fromisoformat(schedule["next_run_at"].replace("Z", "+00:00")) == expected


def test_schedule_in_past_gets_next_slot(api_client):
    response = api_client.post(
        "/api/v1/workflows",
        json={
            "name": "Vergangener Zeitplan",
            "schedule": {
                "every": 1,
                "unit": "hours",
                "start": "2020-01-01T00:00:00Z",
            },
        },
    )

    assert response.status_code == 201
    next_run = datetime.fromisoformat(response.json()["schedule"]["next_run_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    assert now < next_run <= now + timedelta(hours=1)
    assert next_run.minute == 0
    assert next_run.second == 0


@pytest.mark.parametrize(
    ("schedule", "message"),
    [
        ({"every": 0, "unit": "hours", "start": "2030-01-01T06:00:00Z"}, "Intervall muss mindestens 1 sein"),
        ({"every": 1, "unit": "years", "start": "2030-01-01T06:00:00Z"}, "Unbekannte Einheit: years"),
        ({"every": 1501, "unit": "minutes", "start": "2030-01-01T06:00:00Z"}, "Intervall zu groß: höchstens 1439 Minuten"),
        ({"every": 1, "unit": "hours", "start": "2030-01-01T06:00:00"}, "Startzeit braucht eine Zeitzone"),
    ],
)
def test_schedule_validation_messages(api_client, schedule, message):
    response = api_client.post("/api/v1/workflows", json={"name": "Ungültiger Zeitplan", "schedule": schedule})

    assert response.status_code == 422
    assert response.json()["detail"] == message


def test_name_error_before_schedule_error(api_client):
    response = api_client.post(
        "/api/v1/workflows",
        json={
            "name": "",
            "schedule": {"every": 0, "unit": "hours", "start": "2030-01-01T06:00:00Z"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Name fehlt"


def test_put_without_schedule_clears_it(api_client, db_engine):
    created = api_client.post(
        "/api/v1/workflows",
        json={
            "name": "Zeitplan löschen",
            "schedule": {"every": 1, "unit": "hours", "start": "2030-01-01T06:00:00Z"},
        },
    )
    assert created.status_code == 201
    workflow_id = created.json()["id"]
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE workflows SET scheduled_active = true WHERE id = :id"), {"id": workflow_id})

    response = api_client.put(f"/api/v1/workflows/{workflow_id}", json={"name": "Zeitplan gelöscht"})

    assert response.status_code == 200
    assert response.json()["schedule"] is None
    with db_engine.begin() as connection:
        row = connection.execute(
            text("SELECT schedule_every, schedule_unit, schedule_start, next_run_at, scheduled_active FROM workflows WHERE id = :id"),
            {"id": workflow_id},
        ).one()
    assert tuple(row) == (None, None, None, None, False)


def test_put_same_schedule_keeps_next_run(api_client, db_engine):
    body = {
        "name": "Zeitplan erhalten",
        "schedule": {"every": 1, "unit": "hours", "start": "2020-01-01T00:00:00Z"},
    }
    created = api_client.post("/api/v1/workflows", json=body)
    assert created.status_code == 201
    workflow_id = created.json()["id"]
    fixed_next_run = datetime.fromisoformat("2020-01-02T03:00:00+00:00")
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE workflows SET next_run_at = :next_run_at WHERE id = :id"),
            {"next_run_at": fixed_next_run, "id": workflow_id},
        )

    unchanged = api_client.put(f"/api/v1/workflows/{workflow_id}", json=body)
    assert unchanged.status_code == 200
    unchanged_next_run = datetime.fromisoformat(unchanged.json()["schedule"]["next_run_at"].replace("Z", "+00:00"))
    assert unchanged_next_run == fixed_next_run

    changed_body = {
        "name": "Zeitplan erhalten",
        "schedule": {"every": 2, "unit": "hours", "start": "2020-01-01T00:00:00Z"},
    }
    changed = api_client.put(f"/api/v1/workflows/{workflow_id}", json=changed_body)
    assert changed.status_code == 200
    changed_next_run = datetime.fromisoformat(changed.json()["schedule"]["next_run_at"].replace("Z", "+00:00"))
    assert changed_next_run > datetime.now(timezone.utc)


def test_update_workflow_sets_timeout_before_row_lock(api_client, db_engine, monkeypatch):
    workflow_id = api_client.post("/api/v1/workflows", json={"name": "Locked", "description": ""}).json()["id"]
    holder = db_engine.connect()
    lock_transaction = holder.begin()
    holder.execute(text("SELECT id FROM workflows WHERE id = :id FOR UPDATE"), {"id": workflow_id})
    updater = db_engine.connect()
    updater.execute(text("SET lock_timeout = '4s'"))
    updater.commit()

    original_text = workflow_writes.text

    def use_short_test_timeout(statement, *args, **kwargs):
        if statement == "SET LOCAL lock_timeout = '10s'":
            statement = "SET LOCAL lock_timeout = '1s'"
        return original_text(statement, *args, **kwargs)

    monkeypatch.setattr(workflow_writes, "text", use_short_test_timeout)
    started = monotonic()
    try:
        with pytest.raises(DBAPIError):
            with updater.begin():
                update_workflow(updater, workflow_id, "Locked", "")
        elapsed = monotonic() - started
    finally:
        lock_transaction.rollback()
        holder.close()
        updater.close()

    assert elapsed < 3


def test_duplicate_workflow_copies_metadata_and_graph(api_client, db_engine):
    _insert_module_types(db_engine)
    start = datetime.now(timezone.utc) + timedelta(days=2)
    created = api_client.post(
        "/api/v1/workflows",
        json={
            "name": "Alpha",
            "description": "Beschreibung",
            "schedule": {"every": 2, "unit": "hours", "start": start.isoformat()},
        },
    )
    assert created.status_code == 201
    source = created.json()
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
                "description": "Modulbeschreibung",
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
    saved = api_client.put(f"/api/v1/workflows/{source['id']}/graph", json=graph_body)
    assert saved.status_code == 200
    source_graph = saved.json()

    duplicated = api_client.post(f"/api/v1/workflows/{source['id']}/duplicate")

    assert duplicated.status_code == 201
    copy = duplicated.json()
    assert copy["name"] == "Alpha (Kopie)"
    assert copy["description"] == "Beschreibung"
    assert copy["active"] is False
    assert copy["version"] == 1
    assert {key: copy["schedule"][key] for key in ("every", "unit", "start")} == {
        key: source["schedule"][key] for key in ("every", "unit", "start")
    }
    copied_graph_response = api_client.get(f"/api/v1/workflows/{copy['id']}/graph")
    assert copied_graph_response.status_code == 200
    copied_graph = copied_graph_response.json()
    module_fields = ("id", "type_id", "name", "description", "position", "settings", "locked")
    assert [
        {key: module[key] for key in module_fields} for module in copied_graph["modules"]
    ] == [
        {key: module[key] for key in module_fields} for module in source_graph["modules"]
    ]
    connection_fields = ("source_module_id", "source_output_id", "target_module_id")
    assert [
        {key: item[key] for key in connection_fields} for item in copied_graph["connections"]
    ] == [
        {key: item[key] for key in connection_fields} for item in source_graph["connections"]
    ]
    source_after = api_client.get(f"/api/v1/workflows/{source['id']}").json()
    assert source_after["name"] == "Alpha"
    assert source_after["version"] == 2


def test_duplicate_workflow_numbers_copies(api_client):
    source = api_client.post("/api/v1/workflows", json={"name": "Alpha"}).json()

    first = api_client.post(f"/api/v1/workflows/{source['id']}/duplicate")
    second = api_client.post(f"/api/v1/workflows/{source['id']}/duplicate")

    assert first.status_code == 201
    assert first.json()["name"] == "Alpha (Kopie)"
    assert second.status_code == 201
    assert second.json()["name"] == "Alpha (Kopie 2)"


def test_duplicate_workflow_skips_taken_name_case_insensitive(api_client):
    source = api_client.post("/api/v1/workflows", json={"name": "Beta"}).json()
    taken = api_client.post("/api/v1/workflows", json={"name": "beta (kopie)"})
    assert taken.status_code == 201

    duplicated = api_client.post(f"/api/v1/workflows/{source['id']}/duplicate")

    assert duplicated.status_code == 201
    assert duplicated.json()["name"] == "Beta (Kopie 2)"


def test_duplicate_workflow_keeps_name_limit(api_client):
    source = api_client.post("/api/v1/workflows", json={"name": "x" * 200}).json()

    duplicated = api_client.post(f"/api/v1/workflows/{source['id']}/duplicate")

    assert duplicated.status_code == 201
    assert len(duplicated.json()["name"]) == 200
    assert duplicated.json()["name"].endswith(" (Kopie)")


def test_duplicate_active_workflow_gives_paused_copy_without_assets(api_client, db_engine):
    source = api_client.post("/api/v1/workflows", json={"name": "Aktiv"}).json()
    activated = api_client.put(f"/api/v1/workflows/{source['id']}/active", json={"active": True})
    assert activated.status_code == 200
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO assets (id, workflow_id, name, status) "
                "VALUES ('asset-source', :workflow_id, 'Asset', 'pending')"
            ),
            {"workflow_id": source["id"]},
        )

    duplicated = api_client.post(f"/api/v1/workflows/{source['id']}/duplicate")

    assert duplicated.status_code == 201
    copy = duplicated.json()
    assert copy["active"] is False
    assert all(count == 0 for count in copy["asset_counts"].values())
    with db_engine.connect() as connection:
        asset_count = connection.execute(
            text("SELECT COUNT(*) FROM assets WHERE workflow_id = :id"), {"id": copy["id"]}
        ).scalar_one()
    assert asset_count == 0


def test_duplicate_unknown_workflow_returns_404(api_client):
    response = api_client.post("/api/v1/workflows/wf-missing/duplicate")

    assert response.status_code == 404
    assert response.json()["detail"] == "Workflow nicht gefunden"
