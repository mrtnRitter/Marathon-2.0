"""Insert isolated records used by API tests."""

# IMPORTS
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine


# FUNC
def insert_records(engine: Engine, now: datetime) -> None:
    """Insert the API test records in one transaction."""
    def ago(minutes: int) -> datetime:
        return now - timedelta(minutes=minutes)

    module_types: list[dict[str, Any]] = [
        {
            "id": "source", "name": "Quelle", "category": "Test", "icon": "log-in",
            "description": "", "has_input": False,
            "outputs": [{"id": "success", "label": "Weiter", "description": "Asset tritt ein"}],
            "settings": [], "sort_order": 0,
        },
        {
            "id": "step", "name": "Schritt", "category": "Test", "icon": "clock",
            "description": "", "has_input": True,
            "outputs": [
                {"id": "success", "label": "Erfolg", "description": "ok"},
                {"id": "failure", "label": "Fehler", "description": "fehlgeschlagen"},
            ],
            "settings": [{"key": "duration_s", "label": "Dauer", "type": "number", "default": 5}],
            "sort_order": 1,
        },
    ]
    workflows = [
        {"id": "wf-a", "name": "Alpha", "description": "", "priority": 10, "active": True, "current_version": 2},
        {"id": "wf-b", "name": "Beta", "description": "", "priority": 5, "active": True, "current_version": 1},
        {"id": "wf-c", "name": "Gamma", "description": "", "priority": 0, "active": False, "current_version": 1},
    ]
    versions = [
        {"workflow_id": "wf-a", "version": 1, "created_at": ago(1430)},
        {"workflow_id": "wf-a", "version": 2, "created_at": ago(1420)},
        {"workflow_id": "wf-b", "version": 1, "created_at": ago(1410)},
        {"workflow_id": "wf-c", "version": 1, "created_at": ago(1400)},
    ]
    modules = [
        {"workflow_id": "wf-a", "version": 1, "id": "m1", "type_id": "source", "name": "Quelle", "description": "", "position_x": 0, "position_y": 0, "settings": {}, "locked": False},
        {"workflow_id": "wf-a", "version": 2, "id": "m1", "type_id": "source", "name": "Quelle", "description": "", "position_x": 0, "position_y": 0, "settings": {}, "locked": True},
        {"workflow_id": "wf-a", "version": 2, "id": "m2", "type_id": "step", "name": "Prüfen", "description": "", "position_x": 200, "position_y": 0, "settings": {}, "locked": False},
        {"workflow_id": "wf-a", "version": 2, "id": "m3", "type_id": "step", "name": "Ablegen", "description": "", "position_x": 400, "position_y": 0, "settings": {}, "locked": False},
        {"workflow_id": "wf-b", "version": 1, "id": "m1", "type_id": "source", "name": "Quelle", "description": "", "position_x": 0, "position_y": 0, "settings": {}, "locked": False},
        {"workflow_id": "wf-b", "version": 1, "id": "m2", "type_id": "step", "name": "Schritt", "description": "", "position_x": 200, "position_y": 0, "settings": {}, "locked": False},
    ]
    connections = [
        {"workflow_id": "wf-a", "version": 2, "source_module_id": "m1", "source_output_id": "success", "target_module_id": "m2"},
        {"workflow_id": "wf-a", "version": 2, "source_module_id": "m2", "source_output_id": "success", "target_module_id": "m3"},
        {"workflow_id": "wf-b", "version": 1, "source_module_id": "m1", "source_output_id": "success", "target_module_id": "m2"},
    ]
    workers = [
        {"id": "w1", "name": "WORKER-01", "status": "busy", "ip_address": "10.0.0.21", "capabilities": ["test"], "max_parallel": 1, "client_version": "2.0.0", "last_seen": now, "created_at": ago(1439)},
        {"id": "w2", "name": "WORKER-02", "status": "ready", "ip_address": None, "capabilities": [], "max_parallel": 1, "client_version": "", "last_seen": None, "created_at": ago(1438)},
    ]
    assets = [
        {"id": "a-done", "workflow_id": "wf-a", "name": "a-done", "status": "done", "current_module_id": "m3", "step_status": None, "list_position": 1, "metadata": {}, "created_at": ago(90), "updated_at": ago(1)},
        {"id": "a-failed", "workflow_id": "wf-a", "name": "a-failed", "status": "failed", "current_module_id": "m3", "step_status": None, "list_position": 2, "metadata": {}, "created_at": ago(80), "updated_at": ago(2)},
        {"id": "a-running", "workflow_id": "wf-a", "name": "a-running", "status": "in_progress", "current_module_id": "m2", "step_status": "running", "list_position": 3, "metadata": {}, "created_at": ago(70), "updated_at": ago(3)},
        {"id": "a-waiting", "workflow_id": "wf-a", "name": "a-waiting", "status": "in_progress", "current_module_id": "m2", "step_status": "waiting", "list_position": 4, "metadata": {}, "created_at": ago(60), "updated_at": ago(4)},
        {"id": "a-pending", "workflow_id": "wf-a", "name": "a-pending", "status": "pending", "current_module_id": None, "step_status": None, "list_position": 5, "metadata": {}, "created_at": ago(50), "updated_at": ago(5)},
        {"id": "b-done", "workflow_id": "wf-b", "name": "b-done", "status": "done", "current_module_id": "m2", "step_status": None, "list_position": 1, "metadata": {}, "created_at": ago(40), "updated_at": ago(6)},
        {"id": "b-hold", "workflow_id": "wf-b", "name": "b-hold", "status": "on_hold", "current_module_id": "m1", "step_status": None, "list_position": 2, "metadata": {}, "created_at": ago(30), "updated_at": ago(7)},
    ]
    jobs = [
        {"id": "j1", "workflow_id": "wf-a", "module_id": "m2", "status": "succeeded", "worker_id": "w1", "attempt": 1, "output_id": "success", "created_at": ago(420), "started_at": ago(419), "finished_at": ago(418)},
        {"id": "j2", "workflow_id": "wf-a", "module_id": "m3", "status": "succeeded", "worker_id": "w1", "attempt": 1, "output_id": "success", "created_at": ago(360), "started_at": ago(359), "finished_at": ago(358)},
        {"id": "j3", "workflow_id": "wf-a", "module_id": "m2", "status": "succeeded", "worker_id": "w2", "attempt": 1, "output_id": "success", "created_at": ago(300), "started_at": ago(299), "finished_at": ago(298)},
        {"id": "j4", "workflow_id": "wf-a", "module_id": "m3", "status": "failed", "worker_id": "w2", "attempt": 3, "output_id": "failure", "created_at": ago(240), "started_at": ago(239), "finished_at": ago(238)},
        {"id": "j5", "workflow_id": "wf-a", "module_id": "m2", "status": "assigned", "worker_id": "w1", "attempt": 1, "output_id": None, "created_at": ago(180), "started_at": ago(179), "finished_at": None},
        {"id": "j6", "workflow_id": "wf-a", "module_id": "m2", "status": "open", "worker_id": None, "attempt": 1, "output_id": None, "created_at": ago(120), "started_at": None, "finished_at": None},
        {"id": "j7", "workflow_id": "wf-b", "module_id": "m2", "status": "succeeded", "worker_id": "w1", "attempt": 1, "output_id": "success", "created_at": ago(60), "started_at": ago(59), "finished_at": ago(58)},
    ]
    job_assets = [
        {"job_id": "j1", "asset_id": "a-done"}, {"job_id": "j2", "asset_id": "a-done"},
        {"job_id": "j3", "asset_id": "a-failed"}, {"job_id": "j4", "asset_id": "a-failed"},
        {"job_id": "j5", "asset_id": "a-running"}, {"job_id": "j6", "asset_id": "a-waiting"},
        {"job_id": "j7", "asset_id": "b-done"},
    ]
    history = [
        {"asset_id": "a-done", "ts": ago(1), "kind": "success", "text": "Asset completed"},
        {"asset_id": "a-failed", "ts": ago(2), "kind": "error", "text": "Asset failed"},
    ]
    logs = [
        {"workflow_id": "wf-a", "module_id": "m2", "job_id": "j1", "ts": ago(417), "level": "info", "text": "Step completed"},
        {"workflow_id": "wf-a", "module_id": "m3", "job_id": "j2", "ts": ago(357), "level": "info", "text": "Step completed"},
        {"workflow_id": "wf-a", "module_id": "m3", "job_id": "j4", "ts": ago(237), "level": "error", "text": "Step failed"},
        {"workflow_id": "wf-a", "module_id": "m2", "job_id": None, "ts": ago(20), "level": "info", "text": "Module started"},
        {"workflow_id": "wf-a", "module_id": "m2", "job_id": None, "ts": ago(10), "level": "warn", "text": "Module waiting"},
    ]

    statements = [
        (
            "INSERT INTO module_types (id, name, category, icon, description, has_input, outputs, settings, sort_order) "
            "VALUES (:id, :name, :category, :icon, :description, :has_input, :outputs, :settings, :sort_order)",
            module_types, {"outputs": JSONB(), "settings": JSONB()},
        ),
        (
            "INSERT INTO workflows (id, name, description, priority, active, current_version, created_at, updated_at) "
            "VALUES (:id, :name, :description, :priority, :active, :current_version, :created_at, :updated_at)",
            [{**row, "created_at": ago(1439), "updated_at": now} for row in workflows], {},
        ),
        (
            "INSERT INTO workflow_versions (workflow_id, version, created_at) "
            "VALUES (:workflow_id, :version, :created_at)", versions, {},
        ),
        (
            "INSERT INTO workflow_modules (workflow_id, version, id, type_id, name, description, position_x, position_y, settings, locked) "
            "VALUES (:workflow_id, :version, :id, :type_id, :name, :description, :position_x, :position_y, :settings, :locked)",
            modules, {"settings": JSONB()},
        ),
        (
            "INSERT INTO workflow_connections (workflow_id, version, source_module_id, source_output_id, target_module_id) "
            "VALUES (:workflow_id, :version, :source_module_id, :source_output_id, :target_module_id)",
            connections, {},
        ),
        (
            "INSERT INTO workers (id, name, status, ip_address, capabilities, max_parallel, client_version, last_seen, created_at) "
            "VALUES (:id, :name, :status, :ip_address, :capabilities, :max_parallel, :client_version, :last_seen, :created_at)",
            workers, {"capabilities": JSONB()},
        ),
        (
            "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, list_position, metadata, created_at, updated_at) "
            "VALUES (:id, :workflow_id, :name, :status, :current_module_id, :step_status, :list_position, :metadata, :created_at, :updated_at)",
            assets, {"metadata": JSONB()},
        ),
        (
            "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt, output_id, created_at, started_at, finished_at) "
            "VALUES (:id, :workflow_id, :module_id, :status, :worker_id, :attempt, :output_id, :created_at, :started_at, :finished_at)",
            jobs, {},
        ),
        ("INSERT INTO job_assets (job_id, asset_id) VALUES (:job_id, :asset_id)", job_assets, {}),
        (
            "INSERT INTO asset_history (asset_id, ts, kind, text) VALUES (:asset_id, :ts, :kind, :text)",
            history, {},
        ),
        (
            "INSERT INTO log_lines (workflow_id, module_id, job_id, ts, level, text) "
            "VALUES (:workflow_id, :module_id, :job_id, :ts, :level, :text)",
            logs, {},
        ),
    ]

    with engine.begin() as connection:
        for statement, rows, json_columns in statements:
            query = text(statement)
            if json_columns:
                query = query.bindparams(*(bindparam(name, type_=column_type) for name, column_type in json_columns.items()))
            connection.execute(query, rows)
