"""Load the deterministic demo fixture into an empty Marathon database."""

# IMPORTS
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, Engine


# CONFIG
demo_data_path = Path(__file__).with_name("demo_data.json")
empty_check_tables = ("module_types", "workflows", "workers", "assets")


# FUNC
def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _insert(
    connection: Connection,
    statement: str,
    rows: list[dict[str, Any]],
    json_columns: tuple[str, ...] = (),
) -> None:
    if not rows:
        return
    query = text(statement)
    if json_columns:
        query = query.bindparams(*(bindparam(name, type_=JSONB()) for name in json_columns))
    connection.execute(query, rows)


def _shifted(value: str | None, delta: timedelta) -> datetime | None:
    timestamp = _parse_timestamp(value)
    return timestamp + delta if timestamp is not None else None


def load_demo_data(engine: Engine, now: datetime | None = None) -> bool:
    """Load demo records atomically, only when all demo tables are empty."""
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout = '30000ms'"))
        if any(
            connection.execute(text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar_one()
            for table in empty_check_tables
        ):
            return False

        data = json.loads(demo_data_path.read_text(encoding="utf-8"))
        generated_at = _parse_timestamp(data["generated_at"])
        if generated_at is None:
            raise ValueError("Demo-Fixture benötigt generated_at.")
        target_now = now or datetime.now(timezone.utc)
        if target_now.tzinfo is None:
            target_now = target_now.replace(tzinfo=timezone.utc)
        delta = target_now.astimezone(timezone.utc) - generated_at
        generated_time = generated_at + delta

        module_types = data["module_types"]
        workflows = data["workflows"]
        _insert(connection,
            "INSERT INTO module_types (id, name, category, icon, description, has_input, outputs, settings, sort_order) "
            "VALUES (:id, :name, :category, :icon, :description, :has_input, :outputs, :settings, :sort_order)",
            module_types, ("outputs", "settings"))
        _insert(connection,
            "INSERT INTO workflows (id, name, description, active, priority, current_version, created_at, updated_at) "
            "VALUES (:id, :name, :description, :active, :priority, :current_version, :created_at, :updated_at)",
            [{"id": item["id"], "name": item["name"], "description": item["description"],
              "active": item["active"], "priority": item["priority"], "current_version": item["version"],
              "created_at": generated_time, "updated_at": generated_time} for item in workflows])
        _insert(connection,
            "INSERT INTO workflow_versions (workflow_id, version, created_at) VALUES (:workflow_id, :version, :created_at)",
            [{"workflow_id": item["id"], "version": item["version"], "created_at": generated_time} for item in workflows])
        module_rows = []
        connection_rows = []
        for workflow in workflows:
            for module in workflow["modules"]:
                module_rows.append({
                    "workflow_id": workflow["id"], "version": workflow["version"], "id": module["id"],
                    "type_id": module["type_id"], "name": module["name"],
                    "position_x": module["position"]["x"], "position_y": module["position"]["y"],
                    "settings": module["settings"], "locked": module["locked"],
                })
            connection_rows.extend({"workflow_id": workflow["id"], "version": workflow["version"], **item}
                                   for item in workflow["connections"])
        _insert(connection,
            "INSERT INTO workflow_modules (workflow_id, version, id, type_id, name, position_x, position_y, settings, locked) "
            "VALUES (:workflow_id, :version, :id, :type_id, :name, :position_x, :position_y, :settings, :locked)",
            module_rows, ("settings",))
        _insert(connection,
            "INSERT INTO workflow_connections (workflow_id, version, source_module_id, source_output_id, target_module_id) "
            "VALUES (:workflow_id, :version, :source_module_id, :source_output_id, :target_module_id)",
            connection_rows)

        workers = [{**item, "last_seen": _shifted(item["last_seen"], delta), "created_at": generated_time}
                   for item in data["workers"]]
        _insert(connection,
            "INSERT INTO workers (id, name, status, ip_address, capabilities, max_parallel, client_version, last_seen, created_at) "
            "VALUES (:id, :name, :status, :ip_address, :capabilities, :max_parallel, :client_version, :last_seen, :created_at)",
            workers, ("capabilities",))
        assets = [{**item, "created_at": _shifted(item["created_at"], delta),
                   "updated_at": _shifted(item["updated_at"], delta)} for item in data["assets"]]
        _insert(connection,
            "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, list_position, metadata, created_at, updated_at) "
            "VALUES (:id, :workflow_id, :name, :status, :current_module_id, :step_status, :list_position, :metadata, :created_at, :updated_at)",
            assets, ("metadata",))
        jobs = [{**item, "created_at": _shifted(item["created_at"], delta),
                 "started_at": _shifted(item["started_at"], delta),
                 "finished_at": _shifted(item["finished_at"], delta)} for item in data["jobs"]]
        _insert(connection,
            "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt, progress, output_id, created_at, started_at, finished_at) "
            "VALUES (:id, :workflow_id, :module_id, :status, :worker_id, :attempt, :progress, :output_id, :created_at, :started_at, :finished_at)",
            jobs)
        job_assets = [{"job_id": job["id"], "asset_id": asset_id}
                      for job in data["jobs"] for asset_id in job["asset_ids"]]
        _insert(connection, "INSERT INTO job_assets (job_id, asset_id) VALUES (:job_id, :asset_id)", job_assets)
        history = [{**item, "ts": _shifted(item["ts"], delta)} for item in data["asset_history"]]
        _insert(connection, "INSERT INTO asset_history (asset_id, ts, kind, text) VALUES (:asset_id, :ts, :kind, :text)", history)
        logs = [{**item, "ts": _shifted(item["ts"], delta)} for item in data["log_lines"]]
        _insert(connection,
            "INSERT INTO log_lines (workflow_id, module_id, job_id, ts, level, text) "
            "VALUES (:workflow_id, :module_id, :job_id, :ts, :level, :text)", logs)
    return True
