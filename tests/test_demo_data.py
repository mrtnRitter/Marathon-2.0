"""Integration tests for loading the database demo fixture."""

# IMPORTS
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from marathon.server.db import demo
from marathon.server.db.__main__ import main
from marathon.server.db.demo import load_demo_data


# CONFIG
demo_data_path = Path(__file__).resolve().parents[1] / "marathon" / "server" / "db" / "demo_data.json"
count_tables = (
    "module_types", "workflows", "workflow_versions", "workflow_modules",
    "workflow_connections", "workers", "assets", "jobs", "job_assets",
    "asset_history", "log_lines",
)


# FUNC
def _counts(engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in count_tables
        }


def _fixture() -> dict:
    return json.loads(demo_data_path.read_text(encoding="utf-8"))


def _expected_counts(data: dict) -> dict[str, int]:
    return {
        "module_types": len(data["module_types"]),
        "workflows": len(data["workflows"]),
        "workflow_versions": len(data["workflows"]),
        "workflow_modules": sum(len(workflow["modules"]) for workflow in data["workflows"]),
        "workflow_connections": sum(len(workflow["connections"]) for workflow in data["workflows"]),
        "workers": len(data["workers"]),
        "assets": len(data["assets"]),
        "jobs": len(data["jobs"]),
        "job_assets": sum(len(job["asset_ids"]) for job in data["jobs"]),
        "asset_history": len(data["asset_history"]),
        "log_lines": len(data["log_lines"]),
    }


def _write_server_config(path: Path, config) -> None:
    path.write_text(
        'host = "127.0.0.1"\nport = 8080\n\n[database]\n'
        f'host = "{config.host}"\nport = {config.port}\n'
        f'name = "{config.name}"\nuser = "{config.user}"\n',
        encoding="utf-8",
    )


# TESTS
def test_load_demo_data_counts_and_is_idempotent(db_engine):
    fixture = _fixture()
    expected = _expected_counts(fixture)

    assert load_demo_data(db_engine) is True
    loaded_counts = _counts(db_engine)
    assert loaded_counts == expected
    assert loaded_counts["workers"] == 5
    assert loaded_counts["workflows"] == 3
    assert loaded_counts["assets"] == 42

    assert load_demo_data(db_engine) is False
    assert _counts(db_engine) == loaded_counts


def test_demo_timestamps_shift_to_now(db_engine):
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert load_demo_data(db_engine, now=now) is True
    with db_engine.connect() as connection:
        latest = connection.execute(text("SELECT max(updated_at) FROM assets")).scalar_one()
    assert now - timedelta(hours=31) <= latest <= now


def test_only_ingest_input_module_is_locked(db_engine):
    assert load_demo_data(db_engine) is True
    with db_engine.connect() as connection:
        locked = connection.execute(
            text("SELECT workflow_id, id FROM workflow_modules WHERE locked ORDER BY workflow_id, id")
        ).all()
    assert locked == [("wf-ingest", "m1")]


def test_worker_ip_address_and_job_worker_references(db_engine):
    assert load_demo_data(db_engine) is True
    with db_engine.connect() as connection:
        ip_address = connection.execute(
            text("SELECT ip_address FROM workers WHERE name = 'RENDER-01'")
        ).scalar_one()
        missing_workers = connection.execute(
            text("SELECT count(*) FROM jobs j LEFT JOIN workers w ON w.id = j.worker_id "
                 "WHERE j.worker_id IS NOT NULL AND w.id IS NULL")
        ).scalar_one()
    assert ip_address == "192.168.10.21"
    assert missing_workers == 0


def test_asset_current_modules_match_current_workflow_version(db_engine):
    assert load_demo_data(db_engine) is True
    with db_engine.connect() as connection:
        invalid = connection.execute(
            text("SELECT count(*) FROM assets a JOIN workflows w ON w.id = a.workflow_id "
                 "WHERE a.current_module_id IS NOT NULL AND NOT EXISTS ("
                 "SELECT 1 FROM workflow_modules wm WHERE wm.workflow_id = a.workflow_id "
                 "AND wm.version = w.current_version AND wm.id = a.current_module_id)")
        ).scalar_one()
        pending = connection.execute(
            text("SELECT count(*) FROM assets WHERE status = 'pending' AND current_module_id IS NOT NULL")
        ).scalar_one()
    assert invalid == 0
    assert pending == 0


def test_failed_insert_rolls_back_and_can_be_retried(db_engine, tmp_path, monkeypatch):
    invalid_data = _fixture()
    invalid_data["jobs"][0]["worker_id"] = "missing-worker"
    invalid_path = tmp_path / "invalid-demo-data.json"
    invalid_path.write_text(json.dumps(invalid_data), encoding="utf-8")
    monkeypatch.setattr(demo, "demo_data_path", invalid_path)

    with pytest.raises(IntegrityError):
        load_demo_data(db_engine)
    assert _counts(db_engine) == {table: 0 for table in count_tables}

    monkeypatch.setattr(demo, "demo_data_path", demo_data_path)
    assert load_demo_data(db_engine) is True


def test_cli_demo_data_loads_once(db_config, tmp_path, capsys):
    config, password = db_config
    config_file = tmp_path / "server.toml"
    password_file = tmp_path / "postgres.pass"
    _write_server_config(config_file, config)
    password_file.write_text(password, encoding="utf-8")

    assert main(["demo-data"], config_file=config_file, password_file=password_file) == 0
    first = capsys.readouterr()
    assert "Demo-Daten geladen" in first.out
    assert first.err == ""

    assert main(["demo-data"], config_file=config_file, password_file=password_file) == 0
    second = capsys.readouterr()
    assert "übersprungen" in second.out
    assert second.err == ""
