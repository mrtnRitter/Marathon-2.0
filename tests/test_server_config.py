import pytest

from marathon.server.config import (
    ConfigError,
    EngineConfig,
    ServerWorkerConfig,
    load_server_config,
)


def test_defaults_without_sections(tmp_path):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text('host = "localhost"\nport = 9000\n', encoding="utf-8")

    config = load_server_config(config_file)

    assert config.engine == EngineConfig()
    assert config.server_worker == ServerWorkerConfig()
    assert config.folder_dialog.server_address == ""
    assert config.server_worker.skills == ("copy_file", "move_file", "rename_file")


def test_repo_config_file_loads():
    config = load_server_config()

    assert config.engine.poll_timeout_s == 25.0
    assert config.engine.tick_interval_s == 0.5
    assert config.server_worker.enabled is True
    assert not hasattr(config.server_worker, "max_parallel")
    assert not hasattr(config.server_worker, "last" + "_choice")


def test_engine_int_becomes_float(tmp_path):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text("[engine]\ntick_interval_s = 1\n", encoding="utf-8")

    value = load_server_config(config_file).engine.tick_interval_s

    assert value == 1.0
    assert isinstance(value, float)


def test_unknown_server_worker_setting_is_ignored(tmp_path):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text('[server_worker]\n' + 'last' + '_choice = ["transcode"]\n', encoding="utf-8")

    config = load_server_config(config_file)

    assert not hasattr(config.server_worker, "max_parallel")
    assert not hasattr(config.server_worker, "last" + "_choice")


@pytest.mark.parametrize(
    ("contents", "message_prefix"),
    [
        ("[engine]\npoll_timeout_s = 0\n", "[engine] poll_timeout_s"),
        ("[engine]\ntick_interval_s = true\n", "[engine] tick_interval_s"),
        ("[engine]\nmax_attempts = 2.5\n", "[engine] max_attempts"),
        ('[server_worker]\nenabled = "yes"\n', "[server_worker] enabled"),
        ("engine = 5\n", "[engine] in"),
    ],
)
def test_invalid_values(tmp_path, contents, message_prefix):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text(contents, encoding="utf-8")

    with pytest.raises(ConfigError) as error:
        load_server_config(config_file)

    assert str(error.value).startswith(message_prefix)


def test_invalid_folder_dialog_server_address(tmp_path):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text("[folder_dialog]\nserver_address = 'a\\b'\n", encoding="utf-8")

    with pytest.raises(ConfigError) as error:
        load_server_config(config_file)

    assert str(error.value) == (
        f"[folder_dialog] server_address in {config_file} muss ein Rechnername oder eine IP-Adresse sein"
    )


def test_valid_folder_dialog_server_address(tmp_path):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text('[folder_dialog]\nserver_address = "10.0.77.11"\n', encoding="utf-8")

    assert load_server_config(config_file).folder_dialog.server_address == "10.0.77.11"


@pytest.mark.parametrize(
    "contents",
    [
        '[server_worker]\nskills = ["copy_file", "copy_file"]\n',
        '[server_worker]\nskills = "copy_file"\n',
    ],
)
def test_invalid_server_worker_skills(tmp_path, contents):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text(contents, encoding="utf-8")

    with pytest.raises(ConfigError) as error:
        load_server_config(config_file)

    assert str(error.value) == (
        f"[server_worker] skills in {config_file} muss eine Liste von Modultyp-IDs ohne Doppelte sein"
    )


@pytest.mark.parametrize("legacy_value", ["0", "64", '"ignored"'])
def test_legacy_server_worker_max_parallel_is_ignored(tmp_path, legacy_value):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text(f"[server_worker]\nmax_parallel = {legacy_value}\n", encoding="utf-8")

    config = load_server_config(config_file).server_worker
    assert config == ServerWorkerConfig()
    assert not hasattr(config, "max_parallel")
