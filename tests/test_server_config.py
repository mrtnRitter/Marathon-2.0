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


def test_repo_config_file_loads():
    config = load_server_config()

    assert config.engine.poll_timeout_s == 25.0
    assert config.engine.tick_interval_s == 0.5
    assert config.server_worker.enabled is True
    assert config.server_worker.max_parallel == 1
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

    assert config.server_worker.max_parallel == 1
    assert not hasattr(config.server_worker, "last" + "_choice")


@pytest.mark.parametrize(
    ("contents", "message_prefix"),
    [
        ("[engine]\npoll_timeout_s = 0\n", "[engine] poll_timeout_s"),
        ("[engine]\ntick_interval_s = true\n", "[engine] tick_interval_s"),
        ("[engine]\nmax_attempts = 2.5\n", "[engine] max_attempts"),
        ("[server_worker]\nmax_parallel = 0\n", "[server_worker] max_parallel"),
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
