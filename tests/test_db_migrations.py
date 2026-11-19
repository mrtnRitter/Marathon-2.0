"""Integration tests for the PostgreSQL schema and CLI."""

# IMPORTS
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
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
    "asset_history", "log_lines", "watch_modules", "watch_files", "alembic_version",
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
    assert upgrade_database(db_engine) == "0007"
    assert upgrade_database(db_engine) == "0007"


def test_engine_columns_exist(db_engine):
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO module_types (id, name, category, icon, has_input) VALUES ('type', 'Type', 'test', 'box', false)"))
        connection.execute(text("INSERT INTO workflows (id, name) VALUES ('wf-engine', 'Engine')"))
        connection.execute(text("INSERT INTO workers (id, name, status) VALUES ('worker-engine', 'Worker', 'ready')"))
        connection.execute(text("INSERT INTO assets (id, workflow_id, name, status) VALUES ('asset-engine', 'wf-engine', 'Asset', 'pending')"))
        connection.execute(text("INSERT INTO jobs (id, workflow_id, module_id, status) VALUES ('job-running', 'wf-engine', 'module', 'running')"))
        connection.execute(text("INSERT INTO job_assets (job_id, asset_id) VALUES ('job-running', 'asset-engine')"))
        defaults = connection.execute(text(
            "SELECT assets.attempts, workflows.pausing, workers.priorities, workers.client_slots, "
            "jobs.settings, job_assets.output_id, module_types.execution "
            "FROM assets CROSS JOIN workflows CROSS JOIN workers CROSS JOIN jobs "
            "CROSS JOIN job_assets CROSS JOIN module_types "
            "WHERE assets.id = 'asset-engine' AND workflows.id = 'wf-engine' "
            "AND workers.id = 'worker-engine' AND jobs.id = 'job-running' "
            "AND job_assets.job_id = 'job-running' AND module_types.id = 'type'"
        )).mappings().one()

    assert defaults == {
        "attempts": 0,
        "pausing": False,
        "priorities": {},
        "client_slots": 1,
        "settings": {},
        "output_id": None,
        "execution": "none",
    }
    with pytest.raises(IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO jobs (id, workflow_id, module_id, status) "
                "VALUES ('job-bogus', 'wf-engine', 'module', 'bogus')"
            ))


def test_downgrade_0005(db_engine):
    from alembic import command
    from alembic.config import Config

    from marathon.server.db.engine import migrations_dir

    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO workflows (id, name) VALUES ('wf-downgrade', 'Downgrade')"))
        connection.execute(text(
            "INSERT INTO jobs (id, workflow_id, module_id, status) "
            "VALUES ('job-downgrade', 'wf-downgrade', 'module', 'running')"
        ))
        config = Config()
        config.set_main_option("script_location", str(migrations_dir))
        config.attributes["connection"] = connection
        command.downgrade(config, "0004")

    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT status FROM jobs WHERE id = 'job-downgrade'")).scalar_one() == "cancelled"
    assert "attempts" not in {column["name"] for column in inspect(db_engine).get_columns("assets")}
    assert upgrade_database(db_engine) == "0007"


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
    assert "Revision 0007" in captured.out
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


def test_module_description_defaults_to_empty(db_engine):
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO module_types (id, name, category, icon, has_input) VALUES ('type', 'Type', 'test', 'box', false)"))
        connection.execute(text("INSERT INTO workflows (id, name) VALUES ('wf', 'Workflow')"))
        connection.execute(text("INSERT INTO workflow_versions (workflow_id, version) VALUES ('wf', 1)"))
        connection.execute(text("INSERT INTO workflow_modules (workflow_id, version, id, type_id, name, position_x, position_y) VALUES ('wf', 1, 'm1', 'type', 'One', 0, 0)"))
        description = connection.execute(text("SELECT description FROM workflow_modules")).scalar_one()
    assert description == ""


def test_workflow_schedule_columns(db_engine):
    columns = {column["name"] for column in inspect(db_engine).get_columns("workflows")}

    assert {
        "schedule_every", "schedule_unit", "schedule_start", "next_run_at", "schedule_waiting", "run_started_at"
    } <= columns
    assert {"schedule_waiting", "run_started_at"} <= columns
    assert "scheduled_active" not in columns
    watch_columns = {column["name"] for column in inspect(db_engine).get_columns("watch_modules")}
    assert "last_scan_at" in watch_columns
    assert "priority" not in columns


def test_schedule_waiting_migration_up_down_up_preserves_data_rules(db_engine):
    from alembic import command
    from alembic.config import Config

    from marathon.server.db.engine import migrations_dir

    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    with db_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0006")
        connection.execute(
            text(
                "INSERT INTO workflows (id, name, active, schedule_every, schedule_unit, schedule_start, next_run_at) "
                "VALUES ('legacy-waiting', 'Legacy waiting', false, 1, 'hours', :start, :next), "
                "('legacy-running', 'Legacy running', true, 1, 'hours', :start, :next)"
            ),
            {"start": datetime(2025, 1, 1, tzinfo=timezone.utc), "next": datetime(2025, 1, 2, tzinfo=timezone.utc)},
        )
        connection.execute(text("UPDATE workflows SET scheduled_active = true WHERE id = 'legacy-running'"))
        command.upgrade(config, "head")
        upgraded = connection.execute(
            text("SELECT id, active, schedule_waiting, run_started_at FROM workflows WHERE id LIKE 'legacy-%' ORDER BY id")
        ).mappings().all()
        assert [(row["id"], row["active"], row["schedule_waiting"], row["run_started_at"] is not None) for row in upgraded] == [
            ("legacy-running", True, False, True),
            ("legacy-waiting", True, True, False),
        ]
        connection.execute(
            text("INSERT INTO workflows (id, name, active) VALUES ('legacy-manual', 'Legacy manual', true)")
        )
        command.downgrade(config, "0006")
        downgraded = connection.execute(
            text("SELECT id, active, scheduled_active FROM workflows WHERE id LIKE 'legacy-%' ORDER BY id")
        ).all()
        assert [tuple(row) for row in downgraded] == [
            ("legacy-manual", True, False),
            ("legacy-running", True, True),
            ("legacy-waiting", False, False),
        ]
        command.upgrade(config, "head")
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0007"


def test_schedule_constraints(db_engine):
    from datetime import datetime, timezone

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    next_run = datetime(2025, 1, 2, tzinfo=timezone.utc)
    with pytest.raises(IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO workflows (id, name, schedule_every) VALUES ('partial', 'Partial', 1)")
            )

    with pytest.raises(IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO workflows "
                    "(id, name, schedule_every, schedule_unit, schedule_start, next_run_at) "
                    "VALUES ('invalid-unit', 'Invalid unit', 1, 'years', :start, :next_run)"
                ),
                {"start": start, "next_run": next_run},
            )

    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workflows "
                "(id, name, schedule_every, schedule_unit, schedule_start, next_run_at) "
                "VALUES ('valid-schedule', 'Valid schedule', 1, 'days', :start, :next_run)"
            ),
            {"start": start, "next_run": next_run},
        )
        assert connection.execute(
            text("SELECT schedule_unit FROM workflows WHERE id = 'valid-schedule'")
        ).scalar_one() == "days"


def test_downgrade_restores_priority(db_engine):
    from alembic import command
    from alembic.config import Config

    from marathon.server.db.engine import migrations_dir

    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    with db_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0002")

    columns = {column["name"] for column in inspect(db_engine).get_columns("workflows")}
    assert "priority" in columns
    assert not {"schedule_every", "schedule_unit", "schedule_start", "next_run_at", "schedule_waiting", "run_started_at"} & columns



def test_watch_tables_cascade(db_engine):
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO workflows (id, name) VALUES ('wf-watch', 'Watch')"))
        connection.execute(text(
            "INSERT INTO watch_modules (workflow_id, module_id, next_scan_at) "
            "VALUES ('wf-watch', 'watch', '2025-01-01T00:00:00+00:00')"
        ))
        connection.execute(text(
            "INSERT INTO watch_files "
            "(workflow_id, module_id, path, size, mtime_ns, stable_since) "
            "VALUES ('wf-watch', 'watch', '/input/file', 10, 20, '2025-01-01T00:00:00+00:00')"
        ))
        connection.execute(text("DELETE FROM workflows WHERE id = 'wf-watch'"))
        for table in ("watch_modules", "watch_files"):
            assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_watch_file_asset_set_null(db_engine):
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO workflows (id, name) VALUES ('wf-watch', 'Watch')"))
        connection.execute(text(
            "INSERT INTO watch_modules (workflow_id, module_id, next_scan_at) "
            "VALUES ('wf-watch', 'watch', '2025-01-01T00:00:00+00:00')"
        ))
        connection.execute(text(
            "INSERT INTO assets (id, workflow_id, name, status) "
            "VALUES ('asset-watch', 'wf-watch', 'Asset', 'pending')"
        ))
        connection.execute(text(
            "INSERT INTO watch_files "
            "(workflow_id, module_id, path, size, mtime_ns, stable_since, asset_id) "
            "VALUES ('wf-watch', 'watch', '/input/file', 10, 20, "
            "'2025-01-01T00:00:00+00:00', 'asset-watch')"
        ))
        connection.execute(text("DELETE FROM assets WHERE id = 'asset-watch'"))
        assert connection.execute(text(
            "SELECT asset_id FROM watch_files WHERE workflow_id = 'wf-watch' "
            "AND module_id = 'watch' AND path = '/input/file'"
        )).scalar_one() is None


def test_downgrade_removes_watch_tables(db_engine):
    from alembic import command
    from alembic.config import Config

    from marathon.server.db.engine import migrations_dir

    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    with db_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0003")

    assert not inspect(db_engine).has_table("watch_modules")
    assert not inspect(db_engine).has_table("watch_files")
