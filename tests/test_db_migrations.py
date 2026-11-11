"""Integration tests for the PostgreSQL schema and CLI."""

# IMPORTS
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from marathon.server.config import ConfigError, DatabaseConfig, pg_password_file
from marathon.server.db.engine import (
    create_db_engine,
    ensure_database,
    read_password,
    upgrade_database,
)
from marathon.server.db.__main__ import main


# CONFIG
table_names = {
    "module_types", "workflows", "workflow_versions", "workflow_modules",
    "workflow_connections", "workers", "assets", "jobs", "job_assets",
    "asset_history", "log_lines", "alembic_version",
}


# FUNC
def _write_server_config(path: Path, config: DatabaseConfig) -> None:
    """Write an isolated server config for CLI tests."""
    path.write_text(
        "host = \"127.0.0.1\"\nport = 8080\n\n[database]\n"
        f"host = \"{config.host}\"\nport = {config.port}\n"
        f"name = \"{config.name}\"\nuser = \"{config.user}\"\n",
        encoding="utf-8",
    )


def test_initial_upgrade_creates_exact_tables(db_engine):
    assert set(inspect(db_engine).get_table_names()) == table_names


def test_upgrade_is_idempotent(db_engine):
    assert upgrade_database(db_engine) == "0001"
    assert upgrade_database(db_engine) == "0001"


def test_ensure_database_creates_then_reuses(db_config):
    config, password = db_config
    assert ensure_database(config, password) is True
    assert ensure_database(config, password) is False


def test_asset_rejects_invalid_status(db_engine):
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO workflows (id, name) VALUES ('wf', 'Workflow')"))
    with pytest.raises(IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO assets (id, workflow_id, name, status) VALUES ('a', 'wf', 'Asset', 'bogus')")
            )


def test_deleting_workflow_cascades_to_children(db_engine):
    statements = [
        "INSERT INTO module_types (id, name, category, icon, has_input) VALUES ('type', 'Type', 'test', 'box', false)",
        "INSERT INTO workflows (id, name) VALUES ('wf', 'Workflow')",
        "INSERT INTO workflow_versions (workflow_id, version) VALUES ('wf', 1)",
        "INSERT INTO workflow_modules (workflow_id, version, id, type_id, name, position_x, position_y) VALUES ('wf', 1, 'm1', 'type', 'One', 0, 0), ('wf', 1, 'm2', 'type', 'Two', 1, 1)",
        "INSERT INTO workflow_connections (workflow_id, version, source_module_id, source_output_id, target_module_id) VALUES ('wf', 1, 'm1', 'out', 'm2')",
        "INSERT INTO workers (id, name, status) VALUES ('worker', 'Worker', 'ready')",
        "INSERT INTO assets (id, workflow_id, name, status) VALUES ('asset', 'wf', 'Asset', 'pending')",
        "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id) VALUES ('job', 'wf', 'm1', 'assigned', 'worker')",
        "INSERT INTO job_assets (job_id, asset_id) VALUES ('job', 'asset')",
        "INSERT INTO asset_history (asset_id, kind, text) VALUES ('asset', 'info', 'created')",
        "INSERT INTO log_lines (workflow_id, module_id, job_id, level, text) VALUES ('wf', 'm1', 'job', 'info', 'created')",
    ]
    with db_engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(text("DELETE FROM workflows WHERE id = 'wf'"))
        for table in (
            "workflow_versions", "workflow_modules", "workflow_connections", "assets",
            "jobs", "job_assets", "asset_history", "log_lines",
        ):
            assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_read_password_missing_file_raises_config_error(tmp_path):
    missing = tmp_path / "missing.pass"
    with pytest.raises(ConfigError, match="Passwortdatei fehlt:"):
        read_password(missing)


def test_cli_unreachable_database_returns_quick_error(tmp_path, capsys):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = DatabaseConfig(host="127.0.0.1", port=port, name="marathon", user="marathon")
    config_file = tmp_path / "server.toml"
    password_file = tmp_path / "postgres.pass"
    secret = "test-only-secret-do-not-print"
    _write_server_config(config_file, config)
    password_file.write_text(secret, encoding="utf-8")

    started = time.monotonic()
    result = main(["upgrade"], config_file=config_file, password_file=password_file)
    elapsed = time.monotonic() - started
    captured = capsys.readouterr()

    assert result == 1
    assert captured.err.startswith("Fehler: Datenbank nicht erreichbar")
    assert captured.out == ""
    assert secret not in captured.out + captured.err
    assert elapsed < 15


def test_cli_upgrades_test_database(tmp_path, pg_server, capsys):
    config, password = pg_server
    config_file = tmp_path / "server.toml"
    password_file = tmp_path / "postgres.pass"
    _write_server_config(config_file, config)
    password_file.write_text(password, encoding="utf-8")

    result = main(["upgrade"], config_file=config_file, password_file=password_file)
    captured = capsys.readouterr()

    assert result == 0
    assert "Revision 0001" in captured.out
    assert captured.err == ""
    assert password not in captured.out + captured.err


def test_module_help_lists_upgrade():
    result = subprocess.run(
        [sys.executable, "-m", "marathon.server.db", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert "upgrade" in result.stdout
