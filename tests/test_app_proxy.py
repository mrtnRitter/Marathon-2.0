"""Application integration tests for HTTPS proxy startup."""

import asyncio
import inspect
import logging
import os
import socket
import ssl
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.server import app as server_app
from marathon.server.config import ServerWorkerConfig
from marathon.server.db.settings_writes import save_settings
from marathon.server.proxy import caddyfile
from marathon.server.worker_supervisor import WorkerSupervisor


PUBLIC_URL = "https://laptop-martin-r"
USE_TEST_CLIENT_PEER = "client" in inspect.signature(TestClient).parameters


def _save_proxy(db_engine, *, builtin: bool, public_url: str = PUBLIC_URL) -> None:
    with db_engine.begin() as connection:
        save_settings(
            connection,
            "",
            public_url=public_url,
            builtin=builtin,
            trusted_proxies=["10.0.77.20"],
        )


def _request_from(app, peer: str, headers: dict[str, str] | None = None):
    path = "/api/v1/settings"
    request_headers = {
        key.lower().encode("latin-1"): value.encode("latin-1")
        for key, value in (headers or {}).items()
    }
    if b"host" not in request_headers:
        request_headers[b"host"] = b"testserver"
    sent = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": list(request_headers.items()),
        "client": (peer, 123),
        "server": ("testserver", 80),
    }
    messages = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return SimpleNamespace(
        status_code=sent[0]["status"],
        headers={key.decode("latin-1").lower(): value.decode("latin-1") for key, value in sent[0]["headers"]},
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_accepts_connections(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def test_redirects_after_proxy_loaded(db_engine, tmp_path):
    _save_proxy(db_engine, builtin=False)
    app = server_app.create_app(static_dir=tmp_path, engine=db_engine, load_proxy=True)

    with TestClient(app, **({"client": ("203.0.113.8", 123)} if USE_TEST_CLIENT_PEER else {})):
        redirected = _request_from(app, "203.0.113.8")
        forwarded = _request_from(
            app,
            "10.0.77.20",
            {"X-Forwarded-Proto": "https", "Host": "laptop-martin-r"},
        )

    assert redirected.status_code == 308
    assert redirected.headers["location"] == "https://laptop-martin-r/api/v1/settings"
    assert forwarded.status_code == 200


def test_no_redirect_without_load_proxy(db_engine, tmp_path):
    _save_proxy(db_engine, builtin=False)
    app = server_app.create_app(static_dir=tmp_path, engine=db_engine)

    with TestClient(app):
        response = _request_from(
            app,
            "203.0.113.8",
            {"X-Forwarded-Proto": "http", "Host": "laptop-martin-r"},
        )

    assert response.status_code == 200


def test_missing_caddy_aborts_startup(monkeypatch, db_engine, tmp_path, caplog):
    _save_proxy(db_engine, builtin=True)
    monkeypatch.setattr(server_app, "caddy_exe", tmp_path / "missing-caddy", raising=False)
    app = server_app.create_app(static_dir=tmp_path, engine=db_engine, load_proxy=True, listen=("127.0.0.1", 8080))

    with caplog.at_level(logging.ERROR, logger="marathon.server"), pytest.raises(RuntimeError), TestClient(app):
        pass

    assert "Mitgelieferter Proxy fehlt" in caplog.text



def test_caddyfile_write_failure_aborts_startup(monkeypatch, db_engine, tmp_path, caplog):
    _save_proxy(db_engine, builtin=True)
    caddy_exe = tmp_path / "caddy"
    caddy_exe.write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(server_app, "caddy_exe", caddy_exe, raising=False)
    monkeypatch.setattr(server_app, "cert_dir", tmp_path / "cert")
    monkeypatch.setattr(server_app, "caddyfile_path", tmp_path / "missing-parent" / "Caddyfile")
    app = server_app.create_app(
        static_dir=tmp_path,
        engine=db_engine,
        load_proxy=True,
        listen=("127.0.0.1", 8080),
    )

    with caplog.at_level(logging.ERROR, logger="marathon.server"), pytest.raises(RuntimeError), TestClient(app):
        pass

    assert "Caddy-Konfiguration konnte nicht geschrieben werden" in caplog.text


def test_invalid_stored_proxy_aborts_startup(db_engine, tmp_path, caplog):
    _save_proxy(db_engine, builtin=True)
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE settings SET value = CAST(:value AS jsonb) WHERE key = :key"),
            {"value": '"yes"', "key": "proxy.builtin"},
        )
    app = server_app.create_app(static_dir=tmp_path, engine=db_engine, load_proxy=True)

    with caplog.at_level(logging.ERROR, logger="marathon.server"), pytest.raises(RuntimeError), TestClient(app):
        pass

    assert "Proxy-Einstellungen ungültig" in caplog.text
    assert "Einstellung proxy.builtin muss true oder false sein" in caplog.text


def test_builtin_starts_caddy_and_server_worker(monkeypatch, db_engine, tmp_path, capsys):
    _save_proxy(db_engine, builtin=True)
    caddy_exe = tmp_path / "caddy"
    caddy_exe.write_text("dummy", encoding="utf-8")
    caddyfile_path = tmp_path / "cert" / "Caddyfile"
    storage_dir = tmp_path / "caddy-storage"
    root_cert = storage_dir / "pki" / "authorities" / "local" / "root.crt"
    caddy_log_file = tmp_path / "data" / "caddy.log"
    for name, value in (
        ("caddy_exe", caddy_exe),
        ("cert_dir", caddyfile_path.parent),
        ("caddyfile_path", caddyfile_path),
        ("caddy_storage_dir", storage_dir),
        ("caddy_root_cert", root_cert),
        ("caddy_log_file", caddy_log_file),
    ):
        monkeypatch.setattr(server_app, name, value, raising=False)

    events = []
    instances = []

    class FakeSupervisor:
        def __init__(self, command, env, cwd, log_file, **kwargs):
            self.command = command
            self.env = env
            self.cwd = cwd
            self.log_file = log_file
            self.graceful_timeout_s = kwargs.get("graceful_timeout_s", 10.0)
            self.name = kwargs.get("name", "Server worker")
            instances.append(self)

        def start(self):
            events.append(f"{self.name}.start")

        def stop(self):
            events.append(f"{self.name}.stop")
            return True

    monkeypatch.setattr(server_app, "WorkerSupervisor", FakeSupervisor)
    app = server_app.create_app(
        static_dir=tmp_path,
        engine=db_engine,
        worker_token="t",
        server_worker=ServerWorkerConfig(),
        load_proxy=True,
        listen=("0.0.0.0", 8080),
    )

    with TestClient(app):
        pass

    caddy_instance = next(instance for instance in instances if instance.name == "Proxy")
    worker_instance = next(instance for instance in instances if instance.name != "Proxy")
    assert caddyfile_path.read_text(encoding="utf-8") == caddyfile(PUBLIC_URL, "127.0.0.1:8080", storage_dir)
    assert "\r" not in caddyfile_path.read_text(encoding="utf-8")
    assert caddy_instance.command == [
        str(caddy_exe), "run", "--config", str(caddyfile_path), "--adapter", "caddyfile"
    ]
    assert caddy_instance.log_file == caddy_log_file
    assert caddy_instance.graceful_timeout_s == 0.0
    assert "--server" in worker_instance.command
    assert worker_instance.command[worker_instance.command.index("--server") + 1] == PUBLIC_URL
    assert "--ca-file" in worker_instance.command
    assert worker_instance.command[worker_instance.command.index("--ca-file") + 1] == str(root_cert)
    assert events == ["Proxy.start", "Server worker.start", "Server worker.stop", "Proxy.stop"]
    assert "läuft auf https://laptop-martin-r" in capsys.readouterr().out


def test_caddy_end_to_end(monkeypatch, db_engine, tmp_path):
    caddy_exe = server_app.caddy_exe
    if not Path(caddy_exe).is_file():
        pytest.skip(".tools/caddy fehlt")

    server_port = _free_port()
    caddy_port = _free_port()
    while caddy_port == server_port:
        caddy_port = _free_port()
    public_url = f"https://localhost:{caddy_port}"
    _save_proxy(db_engine, builtin=True, public_url=public_url)
    storage_dir = tmp_path / "caddy-storage"
    cert_dir = tmp_path / "cert"
    root_cert = storage_dir / "pki" / "authorities" / "local" / "root.crt"
    caddyfile_path = cert_dir / "Caddyfile"
    caddy_log_file = tmp_path / "caddy.log"
    monkeypatch.setattr(server_app, "cert_dir", cert_dir)
    monkeypatch.setattr(server_app, "caddyfile_path", caddyfile_path)
    monkeypatch.setattr(server_app, "caddy_storage_dir", storage_dir)
    monkeypatch.setattr(server_app, "caddy_root_cert", root_cert)
    monkeypatch.setattr(server_app, "caddy_log_file", caddy_log_file)

    supervisors = []
    real_supervisor = WorkerSupervisor

    def track_supervisor(*args, **kwargs):
        supervisor = real_supervisor(*args, **kwargs)
        supervisors.append(supervisor)
        return supervisor

    monkeypatch.setattr(server_app, "WorkerSupervisor", track_supervisor)
    app = server_app.create_app(
        engine=db_engine,
        load_proxy=True,
        listen=("127.0.0.1", server_port),
    )

    import uvicorn
    from threading import Thread

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=server_port, log_level="critical", timeout_graceful_shutdown=5)
    )
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not server.should_exit:
            if root_cert.is_file() and _port_accepts_connections(caddy_port):
                break
            if not thread.is_alive():
                break
            time.sleep(0.05)
        assert root_cert.is_file(), "Caddys Stammzertifikat wurde nicht rechtzeitig erstellt"
        assert _port_accepts_connections(caddy_port), "Caddy-Port antwortet nicht rechtzeitig"

        ssl_context = ssl.create_default_context(cafile=str(root_cert))
        response = None
        last_error = None
        request_deadline = time.monotonic() + 5
        with httpx.Client(verify=ssl_context, trust_env=False, timeout=1) as client:
            while time.monotonic() < request_deadline:
                try:
                    response = client.get(f"{public_url}/api/v1/settings")
                    break
                except httpx.HTTPError as error:
                    last_error = error
                    time.sleep(0.05)
        assert response is not None, f"HTTPS-Anfrage an Caddy schlug fehl: {last_error}"
        assert response.status_code == 200
    finally:
        server.should_exit = True
        thread.join(10)
        for supervisor in supervisors:
            supervisor.stop()
        thread.join(1)

    assert not thread.is_alive(), "Uvicorn wurde nach dem Test nicht beendet"
    close_deadline = time.monotonic() + 10
    while time.monotonic() < close_deadline and _port_accepts_connections(caddy_port):
        time.sleep(0.05)
    assert not _port_accepts_connections(caddy_port), "Caddy lauscht nach dem Server-Stopp weiterhin"
