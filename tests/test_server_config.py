import pytest

from marathon.server.config import (
    ConfigError,
    EngineConfig,
    ProxyConfig,
    ServerWorkerConfig,
    check_proxy,
    load_server_config,
    normalize_public_url,
    normalize_trusted_proxies,
    public_url_error,
    trusted_proxies_error,
    trusted_proxies_required_error,
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


def test_proxy_defaults_without_section(tmp_path):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text('host = "localhost"\n', encoding="utf-8")

    config = load_server_config(config_file)

    assert config.proxy == ProxyConfig()
    assert ProxyConfig().public_url == "https://localhost"


def test_repo_config_proxy():
    assert load_server_config().proxy == ProxyConfig("https://laptop-martin-r", True, ())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" HTTPS://Laptop-Martin-R/ ", "https://laptop-martin-r"),
        ("https://host:443", "https://host"),
        ("https://host:8443", "https://host:8443"),
        ("https://10.0.77.11", "https://10.0.77.11"),
    ],
)
def test_normalize_public_url(value, expected):
    assert normalize_public_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://host", "https://host/path", "https://host?x=1", "https://host?", "https://host#a", "https://host#",
        "https://user@host", "https://host:0", "https://host:70000", "https://[::1]",
        "https://", "", 5,
    ],
)
def test_invalid_public_url(value):
    with pytest.raises(ValueError) as error:
        normalize_public_url(value)

    assert str(error.value) == public_url_error


def test_normalize_trusted_proxies():
    assert normalize_trusted_proxies([" 10.0.77.20 ", "10.0.77.20", "::1", "0:0::1"]) == (
        "10.0.77.20", "::1"
    )


@pytest.mark.parametrize("values", [["10.0.0.0/8"], ["host"], [""], "10.0.0.1", [5]])
def test_invalid_trusted_proxies(values):
    with pytest.raises(ValueError) as error:
        normalize_trusted_proxies(values)

    assert str(error.value) == trusted_proxies_error


def test_check_proxy():
    check_proxy(True, ())
    check_proxy(False, ("10.0.0.1",))

    with pytest.raises(ValueError) as error:
        check_proxy(False, ())

    assert str(error.value) == trusted_proxies_required_error


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("proxy = 5\n", "table"),
        ('[proxy]\nbuiltin = "yes"\n', "builtin"),
        ('[proxy]\npublic_url = "http://x"\n', "public_url"),
        ('[proxy]\ntrusted_proxies = ["x"]\n', "trusted_proxies"),
        ("[proxy]\nbuiltin = false\n", "required"),
    ],
)
def test_invalid_proxy_section(tmp_path, contents, message):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text(contents, encoding="utf-8")
    expected = {
        "table": f"[proxy] in {config_file} muss eine Tabelle sein",
        "builtin": f"[proxy] builtin in {config_file} muss true oder false sein",
        "public_url": f"[proxy] public_url in {config_file}: {public_url_error}",
        "trusted_proxies": f"[proxy] trusted_proxies in {config_file}: {trusted_proxies_error}",
        "required": f"[proxy] trusted_proxies in {config_file}: {trusted_proxies_required_error}",
    }[message]

    with pytest.raises(ConfigError) as error:
        load_server_config(config_file)

    assert str(error.value) == expected


def test_proxy_section_normalized(tmp_path):
    config_file = tmp_path / "marathon.toml"
    config_file.write_text(
        '[proxy]\npublic_url = "https://Host:443/"\nbuiltin = false\n'
        'trusted_proxies = ["10.0.0.1", "10.0.0.1"]\n',
        encoding="utf-8",
    )

    assert load_server_config(config_file).proxy == ProxyConfig("https://host", False, ("10.0.0.1",))
