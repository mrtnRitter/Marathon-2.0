import json
import shutil
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

import marathon.client.api as client_api_module
from marathon.client.api import (
    ApiError,
    ClientApi,
    client_ssl_context,
    enroll,
    request_control_link,
    tool_fetchers,
)


class ApiTestServer:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.routes: dict[tuple[str, str], Any] = {}
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b""
                try:
                    payload = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    payload = None
                request = {
                    "method": self.command,
                    "path": self.path,
                    "payload": payload,
                    "headers": dict(self.headers.items()),
                }
                with owner.lock:
                    owner.requests.append(request)
                handler = owner.routes.get((self.command, self.path))
                if handler is None:
                    status, body, headers = 200, {"ok": True}, {}
                else:
                    result = handler(request) if callable(handler) else handler
                    if isinstance(result, tuple):
                        status, body, *extra = result
                        headers = extra[0] if extra else {}
                    else:
                        status, body, headers = 200, result, {}
                if isinstance(body, bytes):
                    encoded = body
                elif body is None:
                    encoded = b""
                else:
                    encoded = json.dumps(body).encode("utf-8")
                    headers = {"Content-Type": "application/json", **headers}
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self) -> None:
                self._handle()

            def do_POST(self) -> None:
                self._handle()

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)


@pytest.fixture
def server() -> Any:
    instance = ApiTestServer()
    try:
        yield instance
    finally:
        instance.close()


def test_post_json_sends_json_and_optional_bearer_token(server: ApiTestServer) -> None:
    api = ClientApi(server.url, None, token="secret")
    assert api.post_json("/api/v1/example", {"key": "value"}) == {"ok": True}
    request = server.requests[-1]
    assert request["payload"] == {"key": "value"}
    assert request["headers"]["Content-Type"] == "application/json"
    assert request["headers"]["Authorization"] == "Bearer secret"

    ClientApi(server.url, None).post_json("/api/v1/example")
    request = server.requests[-1]
    assert request["payload"] == {}
    assert "Authorization" not in request["headers"]


def test_get_json_sends_optional_bearer_token(server: ApiTestServer) -> None:
    api = ClientApi(server.url, None, token="secret")
    assert api.get_json("/api/v1/example") == {"ok": True}
    assert server.requests[-1]["headers"]["Authorization"] == "Bearer secret"

    ClientApi(server.url, None).get_json("/api/v1/example")
    assert "Authorization" not in server.requests[-1]["headers"]


@pytest.mark.parametrize("status,body,expected", [
    (403, {"detail": "Nicht erlaubt"}, "Nicht erlaubt"),
    (500, b"nicht-json", "Serverfehler 500"),
])
def test_http_errors_include_status_and_detail(
    server: ApiTestServer, status: int, body: Any, expected: str
) -> None:
    server.routes[("GET", "/api/v1/fail")] = (status, body)
    with pytest.raises(ApiError) as caught:
        ClientApi(server.url, None).get_json("/api/v1/fail")
    assert caught.value.status == status
    assert str(caught.value) == expected
    assert caught.value.detail == expected


def test_invalid_json_is_reported_with_http_status(server: ApiTestServer) -> None:
    server.routes[("GET", "/api/v1/invalid")] = (200, b"{")
    with pytest.raises(ApiError, match="Ungültige Antwort vom Server") as caught:
        ClientApi(server.url, None).get_json("/api/v1/invalid")
    assert caught.value.status == 200


def test_unreachable_server_maps_to_api_error() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(ApiError, match="Server nicht erreichbar") as caught:
        ClientApi(f"http://127.0.0.1:{port}", None, timeout_s=0.2).get_json("/api/v1/example")
    assert caught.value.status is None


def test_json_request_timeout_maps_to_unreachable() -> None:
    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            time.sleep(0.3)
            try:
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: Any) -> None:
            return

    slow_server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    slow_thread = threading.Thread(target=slow_server.serve_forever, daemon=True)
    slow_thread.start()
    try:
        api = ClientApi(f"http://127.0.0.1:{slow_server.server_port}", None, timeout_s=0.05)
        with pytest.raises(ApiError, match="Server nicht erreichbar") as caught:
            api.get_json("/api/v1/slow")
        assert caught.value.status is None
    finally:
        slow_server.shutdown()
        slow_server.server_close()
        slow_thread.join(1)


def test_client_tls_uses_only_the_embedded_root(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, Any]] = []

    class FakeContext:
        def __init__(self, protocol: int) -> None:
            self.protocol = protocol
            self.verify_mode = None
            self.check_hostname = False
            self.loaded_root = None
            created.append({"context": self})

        def load_verify_locations(self, *, cadata: str) -> None:
            self.loaded_root = cadata

    monkeypatch.setattr(ssl, "SSLContext", FakeContext)
    monkeypatch.setattr(ssl, "create_default_context", lambda **_: pytest.fail("system roots loaded"))

    context = client_ssl_context("embedded root")

    assert created[0]["context"] is context
    assert context.protocol == ssl.PROTOCOL_TLS_CLIENT
    assert context.loaded_root == "embedded root"
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_client_tls_empty_root_uses_worker_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeContext:
        verify_mode = None
        check_hostname = False

    expected_context = FakeContext()
    calls: list[object] = []

    def create_context(ca_file: object) -> FakeContext:
        calls.append(ca_file)
        return expected_context

    monkeypatch.setattr(client_api_module, "create_ssl_context", create_context, raising=False)

    context = client_ssl_context("")

    assert calls == [None]
    assert context is expected_context
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_client_tls_rejects_invalid_embedded_root() -> None:
    with pytest.raises(ValueError, match="Stammzertifikat im Client-Paket ist ungültig"):
        client_ssl_context("not a certificate")


def test_path_must_be_under_api_prefix(server: ApiTestServer) -> None:
    with pytest.raises(ValueError):
        ClientApi(server.url, None).get_json("/example")


def test_enroll_posts_payload_and_returns_credentials(server: ApiTestServer) -> None:
    server.routes[("POST", "/api/v1/worker/enroll")] = {
        "worker_id": "worker-7", "worker_token": "token-7", "approved": True
    }
    result = enroll(ClientApi(server.url, None), "HOST", "Windows", "1.2.3")
    assert result == ("worker-7", "token-7", True)
    assert server.requests[-1]["payload"] == {
        "hostname": "HOST", "platform": "Windows", "client_version": "1.2.3"
    }


def test_request_control_link_returns_url(server: ApiTestServer) -> None:
    server.routes[("POST", "/api/v1/worker/control-link")] = {"url": "https://control.test/once"}
    assert request_control_link(ClientApi(server.url, None)) == "https://control.test/once"


def test_tool_fetchers_manifest_and_encoded_download_path(server: ApiTestServer, tmp_path: Path) -> None:
    manifest = [{"name": "Tool Name"}]
    server.routes[("GET", "/api/v1/worker/tools")] = {"tools": manifest}
    encoded_path = "/api/v1/worker/tools/Tool%20Name/files/folder%20name/file%20name.zip"
    server.routes[("GET", encoded_path)] = (200, b"downloaded")
    list_tools, fetch_file = tool_fetchers(ClientApi(server.url, None))
    assert list_tools() == manifest
    target = tmp_path / "tool.zip"
    fetch_file("Tool Name", "folder name/file name.zip", target, threading.Event())
    assert target.read_bytes() == b"downloaded"
    assert server.requests[-1]["path"] == encoded_path


def test_download_streams_complete_file(server: ApiTestServer, tmp_path: Path) -> None:
    expected = b"x" * (1024 * 1024 + 17)
    server.routes[("GET", "/api/v1/file")] = (200, expected)
    target = tmp_path / "download.bin"
    ClientApi(server.url, None).download("/api/v1/file", target, threading.Event())
    assert target.read_bytes() == expected


def test_download_cancellation_removes_partial_file(tmp_path: Path) -> None:
    first = b"a" * (1024 * 1024)
    second = b"b" * (1024 * 1024)
    first_chunk_sent = threading.Event()
    allow_second_chunk = threading.Event()

    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(first + second)))
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            first_chunk_sent.set()
            allow_second_chunk.wait(2)
            try:
                self.wfile.write(second)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: Any) -> None:
            return

    slow_server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    slow_thread = threading.Thread(target=slow_server.serve_forever, daemon=True)
    slow_thread.start()
    stop = threading.Event()
    target = tmp_path / "partial.bin"
    errors: list[BaseException] = []

    def download() -> None:
        try:
            ClientApi(f"http://127.0.0.1:{slow_server.server_port}", None).download(
                "/api/v1/file", target, stop
            )
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=download)
    worker.start()
    try:
        assert first_chunk_sent.wait(2)
        deadline = time.monotonic() + 2
        while (not target.exists() or target.stat().st_size < len(first)) and time.monotonic() < deadline:
            time.sleep(0.01)
        stop.set()
        allow_second_chunk.set()
        worker.join(2)
    finally:
        stop.set()
        allow_second_chunk.set()
        worker.join(2)
        slow_server.shutdown()
        slow_server.server_close()
        slow_thread.join(1)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ApiError)
    assert str(errors[0]) == "Abgebrochen"
    assert not target.exists()


def test_download_read_timeout_maps_to_unreachable_and_removes_file(tmp_path: Path) -> None:
    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "1")
            self.end_headers()
            self.wfile.flush()
            time.sleep(0.3)
            try:
                self.wfile.write(b"x")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: Any) -> None:
            return

    slow_server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    slow_thread = threading.Thread(target=slow_server.serve_forever, daemon=True)
    slow_thread.start()
    target = tmp_path / "timed-out.bin"
    try:
        with pytest.raises(ApiError, match="Server nicht erreichbar") as caught:
            ClientApi(f"http://127.0.0.1:{slow_server.server_port}", None).download(
                "/api/v1/file", target, threading.Event(), read_timeout_s=0.05
            )
        assert caught.value.status is None
        assert not target.exists()
    finally:
        slow_server.shutdown()
        slow_server.server_close()
        slow_thread.join(1)


def test_download_http_error_removes_target(server: ApiTestServer, tmp_path: Path) -> None:
    server.routes[("GET", "/api/v1/missing")] = (404, {"detail": "Nicht gefunden"})
    target = tmp_path / "missing.bin"
    with pytest.raises(ApiError) as caught:
        ClientApi(server.url, None).download("/api/v1/missing", target, threading.Event())
    assert caught.value.status == 404
    assert str(caught.value) == "Nicht gefunden"
    assert not target.exists()


def test_client_ssl_context_trusts_pem_and_rejects_invalid_pem(tmp_path: Path) -> None:
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is unavailable for generating a test certificate")
    key = tmp_path / "key.pem"
    cert = tmp_path / "cert.pem"
    subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
         "-out", str(cert), "-days", "1", "-nodes", "-subj", "/CN=localhost"],
        check=True, capture_output=True, timeout=10,
    )
    pem = cert.read_text(encoding="ascii")
    context = client_ssl_context(pem)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.cert_store_stats()["x509"] == 1
    with pytest.raises(ValueError, match="Stammzertifikat im Client-Paket ist ungültig"):
        client_ssl_context("not a certificate")
