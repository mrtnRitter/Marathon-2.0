"""Server delivers the UI and the health endpoint."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from marathon.common.version import app_name, app_version
from marathon.server.app import create_app
from marathon.server.config import ConfigError, load_server_config


@pytest.fixture()
def static_dir(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>marathon</html>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def client(static_dir: Path) -> TestClient:
    return TestClient(create_app(static_dir))


def test_health(client: TestClient):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": app_name, "version": app_version}


def test_index_and_spa_fallback(client: TestClient):
    for path in ("/", "/dashboard", "/assets/a001", "/workflows/wf-ingest/modules"):
        response = client.get(path)
        assert response.status_code == 200 and "marathon" in response.text, path


def test_asset_file(client: TestClient):
    response = client.get("/assets/app.js")
    assert response.status_code == 200 and response.text == "console.log(1)"


def test_unknown_api_is_json_404(client: TestClient):
    response = client.get("/api/v1/nope")
    assert response.status_code == 404 and "detail" in response.json()


def test_no_path_traversal(client: TestClient, static_dir: Path):
    (static_dir.parent / "secret.txt").write_text("secret", encoding="utf-8")
    assert "secret" not in client.get("/../secret.txt").text


def test_missing_build_returns_503(tmp_path: Path):
    assert TestClient(create_app(tmp_path)).get("/").status_code == 503


def test_config_defaults_and_validation(tmp_path: Path):
    config_file = tmp_path / "server.toml"
    config_file.write_text("port = 9000\n", encoding="utf-8")
    assert load_server_config(config_file).port == 9000
    config_file.write_text("port = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_server_config(config_file)


def test_repo_config_is_valid():
    config = load_server_config()
    assert config.host and config.port
