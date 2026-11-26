"""Tests for public client downloads and authenticated worker tools."""

import json

import pytest
from fastapi.testclient import TestClient

from marathon.server import client_tools, config
from marathon.server.app import create_app
from marathon.server.proxy import proxy_settings
from marathon.server.config import EngineConfig


@pytest.fixture
def api_client(db_engine, tmp_path, monkeypatch):
    """Provide an app and isolated client package and tool directories."""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    package_dir = tmp_path / "client"
    package_dir.mkdir()
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    monkeypatch.setattr(config, "client_package_dir", package_dir, raising=False)
    monkeypatch.setattr(client_tools, "tools_root", tools_dir)
    app = create_app(
        static_dir=static_dir,
        engine=db_engine,
        worker_token="server-secret",
        engine_config=EngineConfig(poll_timeout_s=1),
    )
    with TestClient(app) as client:
        yield client, package_dir, tools_dir


def _install_client(package_dir, server_url="https://worker.example"):
    (package_dir / "MarathonWorker.exe").write_bytes(b"worker installer")
    (package_dir / "client.json").write_text(
        json.dumps({"version": "1.2.3", "sha256": "abc123", "server_url": server_url}),
        encoding="utf-8",
    )


def _install_tool(tools_dir):
    tool_dir = tools_dir / "render-tool"
    tool_dir.mkdir()
    (tool_dir / "setup.bin").write_bytes(b"tool payload")


def test_client_is_public_and_reports_missing_package(api_client):
    client, _, _ = api_client

    response = client.get("/api/v1/client")
    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "version": None,
        "file_name": "MarathonWorker-Setup.exe",
        "detail": "Worker-Client noch nicht gebaut. Auf dem Server python build.py --client ausführen.",
    }
    assert client.get("/api/v1/client/download").status_code == 404


def test_client_download_is_public_and_has_installer_filename(api_client):
    client, package_dir, _ = api_client
    _install_client(package_dir)

    info = client.get("/api/v1/client")
    assert info.status_code == 200
    assert info.json() == {
        "available": True,
        "version": "1.2.3",
        "file_name": "MarathonWorker-Setup.exe",
        "detail": None,
    }
    download = client.get("/api/v1/client/download")
    assert download.status_code == 200
    assert download.content == b"worker installer"
    assert 'filename="MarathonWorker-Setup.exe"' in download.headers["content-disposition"]


def test_client_download_rejects_a_different_proxy_address(api_client):
    client, package_dir, _ = api_client
    _install_client(package_dir, server_url="https://other.example")
    client.app.state.proxy_settings = proxy_settings("https://worker.example/", False, [])

    info = client.get("https://worker.example/api/v1/client")
    assert info.status_code == 200
    assert info.json() == {
        "available": False,
        "version": "1.2.3",
        "file_name": "MarathonWorker-Setup.exe",
        "detail": "Worker-Client passt nicht zur Server-Adresse. Auf dem Server python build.py --client ausführen.",
    }
    assert client.get("https://worker.example/api/v1/client/download").status_code == 409


def test_worker_tools_accept_server_and_enrolled_tokens(api_client):
    client, _, tools_dir = api_client
    _install_tool(tools_dir)

    unauthorized = client.get("/api/v1/worker/tools")
    assert unauthorized.status_code == 401

    server_response = client.get(
        "/api/v1/worker/tools", headers={"Authorization": "Bearer server-secret"}
    )
    assert server_response.status_code == 200
    manifest = server_response.json()["tools"]
    assert len(manifest) == 1
    assert manifest[0]["name"] == "render-tool"
    assert manifest[0]["files"][0]["path"] == "setup.bin"

    enrolled = client.post(
        "/api/v1/worker/enroll",
        json={"hostname": "node", "platform": "windows-x86_64", "client_version": "1.2.3"},
    )
    assert enrolled.status_code == 200
    worker_token = enrolled.json()["worker_token"]
    worker_response = client.get(
        "/api/v1/worker/tools", headers={"Authorization": f"Bearer {worker_token}"}
    )
    assert worker_response.status_code == 200
    assert worker_response.json()["tools"] == manifest


def test_worker_tool_file_download_and_invalid_paths(api_client):
    client, _, tools_dir = api_client
    _install_tool(tools_dir)
    headers = {"Authorization": "Bearer server-secret"}

    response = client.get("/api/v1/worker/tools/render-tool/files/setup.bin", headers=headers)
    assert response.status_code == 200
    assert response.content == b"tool payload"
    assert response.headers["content-type"] == "application/octet-stream"

    traversal = client.get(
        "/api/v1/worker/tools/render-tool/files/%2E%2E%2Fsecret.txt", headers=headers
    )
    assert traversal.status_code == 404
    unknown = client.get(
        "/api/v1/worker/tools/unknown/files/setup.bin", headers=headers
    )
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "Zusatzprogramm nicht gefunden"}
