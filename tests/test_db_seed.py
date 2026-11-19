from sqlalchemy import text

from marathon.server.config import load_server_config


def _server_config(tmp_path, address="10.0.77.11"):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text(
        f'[folder_dialog]\nserver_address = "{address}"\n\n[server_worker]\nskills = ["move_file", "copy_file"]\n',
        encoding="utf-8",
    )
    return load_server_config(config_file)


def test_seed_defaults_inserts_once_with_config_values(db_engine, tmp_path):
    from marathon.server.db.seed import seed_defaults

    config = _server_config(tmp_path)
    with db_engine.begin() as connection:
        seed_defaults(connection, config)
        seed_defaults(connection, config)
        settings = connection.execute(
            text("SELECT key, value, updated_at FROM settings WHERE key = 'folder_dialog.server_address'")
        ).mappings().all()
        worker = connection.execute(
            text("SELECT name, status, online, skills, capabilities, client_version FROM workers WHERE id = 'server-worker'")
        ).mappings().one()
        worker_count = connection.execute(
            text("SELECT count(*) FROM workers WHERE id = 'server-worker'")
        ).scalar_one()

    assert len(settings) == 1
    assert settings[0]["value"] == "10.0.77.11"
    assert settings[0]["updated_at"] is not None
    assert worker_count == 1
    assert worker == {
        "name": "Server-Worker",
        "status": "ready",
        "online": False,
        "skills": ["move_file", "copy_file"],
        "capabilities": [],
        "client_version": "",
    }


def test_seed_defaults_preserves_existing_values(db_engine, tmp_path):
    from marathon.server.db.seed import seed_defaults

    config = _server_config(tmp_path)
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO settings (key, value) VALUES ('folder_dialog.server_address', '\"existing\"'::jsonb)"))
        connection.execute(
            text("INSERT INTO workers (id, name, status, skills) VALUES ('server-worker', 'Existing', 'stopped', '[\"move_file\"]'::jsonb)")
        )
        seed_defaults(connection, config)
        setting = connection.execute(
            text("SELECT value = '\"existing\"'::jsonb FROM settings WHERE key = 'folder_dialog.server_address'")
        ).scalar_one()
        worker = connection.execute(
            text("SELECT name, status, skills FROM workers WHERE id = 'server-worker'")
        ).mappings().one()

    assert setting is True
    assert worker == {"name": "Existing", "status": "stopped", "skills": ["move_file"]}
