"""Integration tests for the worker protocol API."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import stat
from threading import Event, Thread
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from marathon.modules import load_catalog
from marathon.server.config import EngineConfig
from marathon.server.db.catalog_writes import sync_module_types
from marathon.server.db.workflow_writes import create_workflow, save_graph
from marathon.server.engine.flow import load_graph
from marathon.server.engine.jobs import create_batch_job, create_job, start_job
from marathon.server.app import create_app


now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
headers = {"Authorization": "Bearer test-token"}


@pytest.fixture
def api_client(db_engine, tmp_path):
    """Provide a token-protected app backed by the test database."""
    with db_engine.begin() as connection:
        sync_module_types(connection, load_catalog())
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(
        static_dir=static_dir,
        engine=db_engine,
        worker_token="test-token",
        engine_config=EngineConfig(poll_timeout_s=1),
    )
    with TestClient(app) as client:
        yield client


def _worker(db_engine, worker_id="worker-1", *, status="ready", name="Worker", skills=None, last_seen=None):
    statement = text(
        "INSERT INTO workers (id, name, status, capabilities, skills, client_version, ip_address, last_seen, created_at) "
        "VALUES (:id, :name, :status, :capabilities, :skills, '', NULL, :last_seen, :created_at) "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(
        bindparam("capabilities", type_=JSONB), bindparam("skills", type_=JSONB),
    )
    with db_engine.begin() as connection:
        connection.execute(statement, {
            "id": worker_id, "name": name, "status": status, "capabilities": ["copy_file", "move_file"],
            "skills": skills if skills is not None else ["copy_file", "move_file"],
            "last_seen": last_seen, "created_at": now,
        })


def _workflow(db_engine):
    suffix = uuid4().hex[:8]
    modules = [
        {"id": "watch", "type_id": "watchfolder", "name": "Watch", "description": "",
         "position": {"x": 0, "y": 0}, "settings": {}, "locked": False},
        {"id": "copy", "type_id": "copy_file", "name": "Copy", "description": "",
         "position": {"x": 200, "y": 0}, "settings": {}, "locked": False},
        {"id": "move", "type_id": "move_file", "name": "Move", "description": "",
         "position": {"x": 400, "y": 0}, "settings": {}, "locked": False},
    ]
    connections = [
        {"source_module_id": "watch", "source_output_id": "success", "target_module_id": "copy"},
        {"source_module_id": "copy", "source_output_id": "success", "target_module_id": "move"},
    ]
    with db_engine.begin() as connection:
        workflow_id = create_workflow(connection, f"Worker API {suffix}", "")
        save_graph(connection, workflow_id, 1, modules, connections)
    return workflow_id


def _job(db_engine, worker_id="worker-1", *, status="assigned", asset_id=None):
    _worker(db_engine, worker_id)
    workflow_id = _workflow(db_engine)
    asset_id = asset_id or f"asset-{uuid4().hex[:10]}"
    statement = text(
        "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, metadata, "
        "list_position, attempts, created_at, updated_at) VALUES (:id, :workflow_id, :name, 'in_progress', "
        "'copy', 'waiting', :metadata, 1, 0, :created_at, :updated_at)"
    ).bindparams(bindparam("metadata", type_=JSONB))
    with db_engine.begin() as connection:
        connection.execute(statement, {
            "id": asset_id, "workflow_id": workflow_id, "name": f"{asset_id}.mov",
            "metadata": {"path": f"/media/{asset_id}.mov"}, "created_at": now, "updated_at": now,
        })
        graph = load_graph(connection, workflow_id)
        job_id = create_job(connection, asset_id, graph, worker_id, now, 60)
        if status in ("running", "cancelling"):
            assert start_job(connection, job_id, worker_id, now)
        if status == "cancelling":
            connection.execute(text("UPDATE jobs SET status = 'cancelling' WHERE id = :id"), {"id": job_id})
    return job_id, workflow_id, asset_id


def _batch_job(db_engine, worker_id="worker-1", *, status="running"):
    _worker(db_engine, worker_id)
    workflow_id = _workflow(db_engine)
    asset_ids = [f"asset-{uuid4().hex[:10]}" for _ in range(2)]
    statement = text(
        "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, metadata, "
        "list_position, attempts, created_at, updated_at) VALUES (:id, :workflow_id, :name, 'in_progress', "
        "'copy', 'waiting', :metadata, :list_position, 0, :created_at, :updated_at)"
    ).bindparams(bindparam("metadata", type_=JSONB))
    with db_engine.begin() as connection:
        for index, asset_id in enumerate(asset_ids, start=1):
            connection.execute(statement, {
                "id": asset_id, "workflow_id": workflow_id, "name": f"{asset_id}.mov",
                "metadata": {"path": f"/media/{asset_id}.mov"}, "list_position": index,
                "created_at": now, "updated_at": now,
            })
        graph = load_graph(connection, workflow_id)
        job_id = create_batch_job(connection, asset_ids, graph, worker_id, now, 60)
        if status == "running":
            assert start_job(connection, job_id, worker_id, now)
    return job_id, workflow_id, asset_ids


def _row(db_engine, table, key, value):
    with db_engine.connect() as connection:
        row = connection.execute(text(f"SELECT * FROM {table} WHERE {key} = :value"), {"value": value}).mappings().one()
    return dict(row)


def test_requires_token(api_client):
    without_token = api_client.post("/api/v1/worker/register", json={})
    wrong_token = api_client.post("/api/v1/worker/register", headers={"Authorization": "Bearer wrong"}, json={})
    assert without_token.status_code == wrong_token.status_code == 401
    assert without_token.json() == wrong_token.json() == {"detail": "Worker-Token ungültig"}


def test_load_or_create_token(tmp_path):
    from marathon.server.worker_auth import load_or_create_token

    path = tmp_path / "nested" / "worker.token"
    first = load_or_create_token(path)
    assert len(first) >= 32
    assert path.is_file()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_or_create_token(path) == first


def test_register_new_worker(api_client, db_engine):
    register_payload = {
        "worker_id": "new", "name": "GPU", "client_version": "2.1",
        "capabilities": ["move_file", "copy_file"], "protocol": 2,
    }
    register_payload["max_parallel"] = 4
    register_payload["last" + "_choice"] = ["copy_file"]
    response = api_client.post("/api/v1/worker/register", headers=headers, json=register_payload)
    assert response.status_code == 200
    assert response.json() == {"worker_id": "new", "status": "stopped", "protocol": 2, "poll_timeout_s": 1.0}
    worker = _row(db_engine, "workers", "id", "new")
    assert worker["status"] == "stopped"
    assert worker["skills"] == worker["capabilities"] == ["move_file", "copy_file"]
    assert worker["online"] is True

    server_worker = api_client.post("/api/v1/worker/register", headers=headers, json={
        "worker_id": "server-worker", "name": "Server", "client_version": "2.1",
        "capabilities": [], "protocol": 2,
    })
    assert server_worker.status_code == 200
    assert server_worker.json()["status"] == "ready"

    without_legacy_field = api_client.post("/api/v1/worker/register", headers=headers, json={
        "worker_id": "new-without-legacy-field", "name": "GPU", "client_version": "2.1",
        "capabilities": ["copy_file"], "protocol": 2,
    })
    assert without_legacy_field.status_code == 200


def test_register_rejects_protocol(api_client):
    response = api_client.post("/api/v1/worker/register", headers=headers, json={
        "worker_id": "new", "name": "GPU", "client_version": "2.1", "capabilities": [],
        "protocol": 1,
    })
    assert response.status_code == 409
    assert response.json() == {"detail": "Protokollversion nicht unterstützt"}


def test_register_known_worker_resets_jobs(api_client, db_engine):
    original_skills = ["move_file", "copy_file"]
    _worker(db_engine, "worker-1", status="stopped", name="Original", skills=original_skills,
            last_seen=now - timedelta(days=1))
    assigned, _, _ = _job(db_engine, "worker-1", status="assigned")
    running, _, _ = _job(db_engine, "worker-1", status="running")
    response = api_client.post("/api/v1/worker/register", headers=headers, json={
        "worker_id": "worker-1", "name": "Changed", "client_version": "2.2", "capabilities": ["move_file"],
        "protocol": 2,
    })
    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    worker = _row(db_engine, "workers", "id", "worker-1")
    assert worker["name"] == "Original"
    assert worker["skills"] == original_skills
    assert worker["status"] == "stopped"
    assert worker["online"] is True
    assert worker["capabilities"] == ["move_file"]
    assert worker["client_version"] == "2.2"
    assert worker["ip_address"] == "testclient"
    assert worker["last_seen"] > now
    assert _row(db_engine, "jobs", "id", assigned)["status"] == "cancelled"
    interrupted = _row(db_engine, "jobs", "id", running)
    assert interrupted["status"] == "interrupted"
    assert interrupted["error"] == "Worker neu gestartet"


def test_poll_returns_assigned_jobs(api_client, db_engine):
    job_id, _, asset_ids = _batch_job(db_engine, status="assigned")
    response = api_client.post("/api/v1/worker/poll", headers=headers,
                               json={"worker_id": "worker-1"})
    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0] == {
        "job_id": job_id, "type_id": "copy_file", "settings": {},
        "assets": [
            {"id": asset_id, "name": f"{asset_id}.mov", "path": f"/media/{asset_id}.mov"}
            for asset_id in sorted(asset_ids)
        ],
    }
    assert _row(db_engine, "workers", "id", "worker-1")["online"] is True
    assert response.json()["cancel"] == []


def test_poll_times_out_empty(api_client, db_engine):
    _worker(db_engine)
    started = monotonic()
    response = api_client.post("/api/v1/worker/poll", headers=headers, json={"worker_id": "worker-1"})
    duration = monotonic() - started
    assert response.status_code == 200
    assert response.json() == {"jobs": [], "cancel": []}
    assert 0.9 <= duration <= 3


def test_poll_returns_early_when_job_appears(api_client, db_engine):
    _worker(db_engine)
    results = []
    started = monotonic()
    poll_thread = Thread(target=lambda: results.append(api_client.post(
        "/api/v1/worker/poll", headers=headers, json={"worker_id": "worker-1"}
    )))
    poll_thread.start()
    sleep(0.3)
    job_id, _, _ = _job(db_engine, "worker-1")
    poll_thread.join(timeout=1.5)
    assert not poll_thread.is_alive()
    assert monotonic() - started < 1
    assert results[0].status_code == 200
    assert results[0].json()["jobs"][0]["job_id"] == job_id


def test_poll_cancel_lists(api_client, db_engine):
    _worker(db_engine)
    cancelling, _, _ = _job(db_engine, status="cancelling")
    running, _, _ = _job(db_engine, status="running")
    response = api_client.post("/api/v1/worker/poll", headers=headers, json={
        "worker_id": "worker-1", "running": [cancelling, running, "unknown-job"],
    })
    assert response.status_code == 200
    assert response.json() == {"jobs": [], "cancel": [cancelling, "unknown-job"]}


def test_poll_unknown_worker_404(api_client):
    response = api_client.post("/api/v1/worker/poll", headers=headers, json={"worker_id": "missing"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Worker unbekannt"}


def test_poll_touches_worker(api_client, db_engine):
    _worker(db_engine, "stopped-worker", status="stopped", last_seen=now - timedelta(days=1))
    _job(db_engine, "stopped-worker")
    response = api_client.post("/api/v1/worker/poll", headers=headers, json={"worker_id": "stopped-worker"})
    assert response.status_code == 200
    stopped = _row(db_engine, "workers", "id", "stopped-worker")
    assert stopped["status"] == "stopped"
    assert stopped["online"] is True
    assert stopped["last_seen"] > now

    _worker(db_engine, "locked-worker", status="stopped", last_seen=now - timedelta(days=1))
    _job(db_engine, "locked-worker")
    locked_response = api_client.post("/api/v1/worker/poll", headers=headers,
                                      json={"worker_id": "locked-worker"})
    assert locked_response.status_code == 200
    inactive = _row(db_engine, "workers", "id", "locked-worker")
    assert inactive["status"] == "stopped"
    assert inactive["online"] is True
    assert inactive["last_seen"] > now


def test_start_endpoint(api_client, db_engine):
    job_id, _, _ = _job(db_engine)
    response = api_client.post(f"/api/v1/worker/jobs/{job_id}/start", headers=headers,
                               json={"worker_id": "worker-1"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    cancelled, _, _ = _job(db_engine)
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE jobs SET status = 'cancelled' WHERE id = :id"), {"id": cancelled})
    invalid = api_client.post(f"/api/v1/worker/jobs/{cancelled}/start", headers=headers,
                              json={"worker_id": "worker-1"})
    missing = api_client.post("/api/v1/worker/jobs/missing/start", headers=headers,
                              json={"worker_id": "worker-1"})
    assert invalid.status_code == 409
    assert invalid.json() == {"detail": "Job nicht mehr gültig"}
    assert missing.status_code == 404


def test_progress_endpoint(api_client, db_engine):
    job_id, _, _ = _job(db_engine, status="running")
    response = api_client.post(f"/api/v1/worker/jobs/{job_id}/progress", headers=headers,
                               json={"worker_id": "worker-1", "progress": 45, "log": ["working"]})
    assert response.status_code == 200
    assert response.json() == {"cancel": False}
    job = _row(db_engine, "jobs", "id", job_id)
    assert job["progress"] == 45
    with db_engine.connect() as connection:
        log = connection.execute(text("SELECT job_id, module_id, level, text FROM log_lines WHERE job_id = :id AND text = :text"),
                                 {"id": job_id, "text": "working"}).mappings().one()
    assert dict(log) == {"job_id": job_id, "module_id": "copy", "level": "info", "text": "working"}

    with db_engine.begin() as connection:
        connection.execute(text("UPDATE jobs SET status = 'cancelling' WHERE id = :id"), {"id": job_id})
    cancelling = api_client.post(f"/api/v1/worker/jobs/{job_id}/progress", headers=headers,
                                 json={"worker_id": "worker-1", "progress": 50})
    assert cancelling.status_code == 200
    assert cancelling.json() == {"cancel": True}
    foreign = api_client.post(f"/api/v1/worker/jobs/{job_id}/progress", headers=headers,
                              json={"worker_id": "worker-2", "progress": 50})
    missing = api_client.post("/api/v1/worker/jobs/missing/progress", headers=headers,
                              json={"worker_id": "worker-1", "progress": 50})
    assert foreign.status_code == 403
    assert missing.status_code == 404


def test_result_endpoint(api_client, db_engine):
    job_id, _, _ = _job(db_engine, status="running")
    payload = {"worker_id": "worker-1", "outcome": "result", "output": "success", "log": ["done"]}
    accepted = api_client.post(f"/api/v1/worker/jobs/{job_id}/result", headers=headers, json=payload)
    late = api_client.post(f"/api/v1/worker/jobs/{job_id}/result", headers=headers, json=payload)
    assert accepted.status_code == 200
    assert accepted.json() == {"accepted": True}
    assert late.status_code == 200
    assert late.json() == {"accepted": False}

    foreign_job, _, _ = _job(db_engine, status="running")
    foreign = api_client.post(f"/api/v1/worker/jobs/{foreign_job}/result", headers=headers,
                              json={**payload, "worker_id": "worker-2"})
    missing = api_client.post("/api/v1/worker/jobs/missing/result", headers=headers, json=payload)
    assert foreign.status_code == 403
    assert missing.status_code == 404



def test_asset_result_endpoint_completes_batch_one_asset_at_a_time(api_client, db_engine):
    job_id, _, asset_ids = _batch_job(db_engine, status="running")
    first_asset, second_asset = sorted(asset_ids)
    payload = {"worker_id": "worker-1", "outcome": "result", "output": "success", "log": ["done"]}
    route = f"/api/v1/worker/jobs/{job_id}/assets/{first_asset}/result"

    first = api_client.post(route, headers=headers, json=payload)
    assert first.status_code == 200
    assert first.json() == {"accepted": True}
    assert _row(db_engine, "jobs", "id", job_id)["status"] == "running"
    assert _row(db_engine, "jobs", "id", job_id)["progress"] == 50

    duplicate = api_client.post(route, headers=headers, json=payload)
    assert duplicate.status_code == 200
    assert duplicate.json() == {"accepted": False}
    assert _row(db_engine, "jobs", "id", job_id)["status"] == "running"

    second = api_client.post(
        f"/api/v1/worker/jobs/{job_id}/assets/{second_asset}/result", headers=headers, json=payload
    )
    assert second.status_code == 200
    assert second.json() == {"accepted": True}
    assert _row(db_engine, "jobs", "id", job_id)["status"] == "succeeded"


def test_asset_result_endpoint_rejects_foreign_worker_and_asset(api_client, db_engine):
    job_id, _, asset_ids = _batch_job(db_engine, status="running")
    payload = {"worker_id": "worker-2", "outcome": "result", "output": "success"}
    foreign_worker = api_client.post(
        f"/api/v1/worker/jobs/{job_id}/assets/{asset_ids[0]}/result", headers=headers, json=payload
    )
    assert foreign_worker.status_code == 403
    assert foreign_worker.json() == {"detail": "Job gehört einem anderen Worker"}

    foreign_asset = api_client.post(
        f"/api/v1/worker/jobs/{job_id}/assets/not-in-job/result",
        headers=headers, json={**payload, "worker_id": "worker-1"},
    )
    assert foreign_asset.status_code == 404
    assert foreign_asset.json() == {"detail": "Job nicht gefunden"}

    missing_job = api_client.post(
        f"/api/v1/worker/jobs/missing/assets/{asset_ids[0]}/result",
        headers=headers, json={**payload, "worker_id": "worker-1"},
    )
    assert missing_job.status_code == 404
    assert missing_job.json() == {"detail": "Job nicht gefunden"}


def test_asset_result_rejects_foreign_worker_after_job_finished(api_client, db_engine):
    job_id, _, asset_id = _job(db_engine, status="running")
    result_url = f"/api/v1/worker/jobs/{job_id}/assets/{asset_id}/result"
    accepted = api_client.post(
        result_url, headers=headers,
        json={"worker_id": "worker-1", "outcome": "result", "output": "success"},
    )
    assert accepted.status_code == 200
    assert accepted.json() == {"accepted": True}

    foreign = api_client.post(
        result_url, headers=headers,
        json={"worker_id": "worker-2", "outcome": "result", "output": "success"},
    )
    assert foreign.status_code == 403
    assert foreign.json() == {"detail": "Job gehört einem anderen Worker"}


def test_result_validation(api_client):
    response = api_client.post("/api/v1/worker/jobs/job-1/result", headers=headers, json={
        "worker_id": "worker-1", "outcome": "invalid",
    })
    assert response.status_code == 422


def test_poll_returns_when_shutdown_requested(api_client, db_engine):
    _worker(db_engine)
    app = api_client.app
    app.state.engine_config = EngineConfig(poll_timeout_s=25)
    if not hasattr(app.state, "shutdown_event"):
        app.state.shutdown_event = Event()
    responses = []
    poll_thread = Thread(target=lambda: responses.append(api_client.post(
        "/api/v1/worker/poll", headers=headers, json={"worker_id": "worker-1"}
    )), daemon=True)
    poll_thread.start()
    sleep(0.3)
    shutdown_at = monotonic()
    app.state.shutdown_event.set()
    poll_thread.join(timeout=1.45)
    elapsed = monotonic() - shutdown_at

    assert not poll_thread.is_alive()
    assert len(responses) == 1
    assert responses[0].status_code == 200
    assert responses[0].json() == {"jobs": [], "cancel": []}
    assert elapsed < 1.5


def test_poll_bounded_when_worker_row_locked(api_client, db_engine):
    _worker(db_engine)
    lock_connection = db_engine.connect()
    lock_transaction = lock_connection.begin()
    lock_connection.execute(text("SELECT id FROM workers WHERE id = 'worker-1' FOR UPDATE"))
    responses = []
    started = monotonic()
    poll_thread = Thread(target=lambda: responses.append(api_client.post(
        "/api/v1/worker/poll", headers=headers, json={"worker_id": "worker-1"}
    )))

    def waiting_updates():
        with db_engine.connect() as connection:
            return connection.execute(text(
                "SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND wait_event_type = 'Lock' AND query LIKE 'UPDATE workers SET last_seen%'"
            )).scalar_one()

    try:
        poll_thread.start()
        deadline = started + api_client.app.state.engine_config.poll_timeout_s + 3.0
        while monotonic() < deadline and not waiting_updates() and poll_thread.is_alive():
            sleep(0.05)
        assert waiting_updates() > 0
        poll_thread.join(timeout=max(0, deadline - monotonic()))
        assert not poll_thread.is_alive()
        assert monotonic() - started <= api_client.app.state.engine_config.poll_timeout_s + 3.0
        assert responses[0].status_code in (200, 503)
        while monotonic() < deadline and waiting_updates():
            sleep(0.05)
        assert waiting_updates() == 0
    finally:
        lock_transaction.rollback()
        lock_connection.close()
        poll_thread.join(timeout=2)


def test_poll_returns_with_shutdown_during_threadpool_query(api_client, monkeypatch):
    import marathon.server.api.worker as worker_api

    started = Event()
    release = Event()
    responses = []

    async def blocked_query(function, *args):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return True

    monkeypatch.setattr(worker_api, "run_in_threadpool", blocked_query)
    poll_thread = Thread(target=lambda: responses.append(api_client.post(
        "/api/v1/worker/poll", headers=headers, json={"worker_id": "worker-1"}
    )), daemon=True)
    poll_thread.start()
    assert started.wait(1)
    shutdown_at = monotonic()
    api_client.app.state.shutdown_event.set()
    poll_thread.join(timeout=0.5)
    returned_within_half_second = not poll_thread.is_alive()
    release.set()
    poll_thread.join(timeout=2)

    assert returned_within_half_second
    assert not poll_thread.is_alive()
    assert len(responses) == 1
    assert responses[0].status_code == 200
    assert responses[0].json() == {"jobs": [], "cancel": []}
    assert monotonic() - shutdown_at <= 0.5


def test_threadpool_poll_cancellation_propagates(monkeypatch):
    import marathon.server.api.worker as worker_api

    async def exercise():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = asyncio.Event()
        unhandled = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

        async def failing_query(function, *args):
            started.set()
            await release.wait()
            raise RuntimeError("query failed after poll cancellation")

        async def keep_waiting():
            return False

        monkeypatch.setattr(worker_api, "run_in_threadpool", failing_query)
        poll = asyncio.create_task(worker_api._run_threadpool_until(loop.time() + 5, keep_waiting, lambda: None))
        await started.wait()
        poll.cancel()
        with pytest.raises(asyncio.CancelledError):
            await poll
        release.set()
        await asyncio.sleep(0.02)
        loop.set_exception_handler(previous_handler)
        assert unhandled == []

    asyncio.run(exercise())
