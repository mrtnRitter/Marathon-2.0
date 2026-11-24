"""Integration tests for PostgreSQL live-change notifications."""

# IMPORTS
from alembic import command
from alembic.config import Config
import psycopg
from sqlalchemy import text

from marathon.server.db.engine import migrations_dir, upgrade_database


# CONFIG
live_tables = (
    "module_types", "workflows", "workflow_versions", "workflow_modules",
    "workflow_connections", "workers", "assets", "jobs", "job_assets",
    "asset_history", "log_lines",
)


# FUNC
def _listen(db_config):
    config, password = db_config
    connection = psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.name,
        user=config.user,
        password=password,
        autocommit=True,
    )
    connection.execute("LISTEN marathon_changes")
    return connection


def _payloads(connection, timeout=1.0):
    return [notification.payload for notification in connection.notifies(timeout=timeout)]


def test_insert_notifies_table_name(db_engine, db_config):
    with _listen(db_config) as listener:
        with db_engine.begin() as connection:
            connection.execute(text("INSERT INTO workflows (id, name) VALUES ('wf-live', 'Live')"))
        assert "workflows" in _payloads(listener)


def test_update_with_change_notifies(db_engine, db_config):
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO workflows (id, name) VALUES ('wf-live', 'Live')"))

    with _listen(db_config) as listener:
        with db_engine.begin() as connection:
            connection.execute(text("UPDATE workflows SET name = 'Neu' WHERE id = 'wf-live'"))
        assert "workflows" in _payloads(listener)


def test_noop_update_does_not_notify(db_engine, db_config):
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO workflows (id, name) VALUES ('wf-live', 'Live')"))

    with _listen(db_config) as listener:
        with db_engine.begin() as connection:
            connection.execute(text("UPDATE workflows SET name = name WHERE id = 'wf-live'"))
        assert _payloads(listener, timeout=0.5) == []


def test_delete_notifies(db_engine, db_config):
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO workflows (id, name) VALUES ('wf-live', 'Live')"))

    with _listen(db_config) as listener:
        with db_engine.begin() as connection:
            connection.execute(text("DELETE FROM workflows WHERE id = 'wf-live'"))
        assert "workflows" in _payloads(listener)


def test_one_notify_per_transaction(db_engine, db_config):
    with _listen(db_config) as listener:
        with db_engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO workflows (id, name) VALUES "
                "('wf-live-1', 'One'), ('wf-live-2', 'Two'), ('wf-live-3', 'Three')"
            ))
        assert _payloads(listener) == ["workflows"]


def test_all_live_tables_have_triggers(db_engine):
    with db_engine.connect() as connection:
        triggers = connection.execute(text(
            "SELECT c.relname, t.tgname FROM pg_trigger AS t "
            "JOIN pg_class AS c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND t.tgname LIKE 'marathon_live_%'"
        )).all()

    actual = set(triggers)
    expected = {
        (table, f"marathon_live_{table}_{kind}")
        for table in live_tables
        for kind in ("write", "update")
    }
    assert expected <= actual


def test_downgrade_removes_triggers(db_engine):
    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    with db_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0005")
        trigger_count = connection.execute(text(
            "SELECT count(*) FROM pg_trigger "
            "WHERE NOT tgisinternal AND tgname LIKE 'marathon_live_%'"
        )).scalar_one()
        function_exists = connection.execute(text(
            "SELECT to_regprocedure('marathon_notify_change()') IS NOT NULL"
        )).scalar_one()
        assert trigger_count == 0
        assert function_exists is False

    assert upgrade_database(db_engine) == "0010"
