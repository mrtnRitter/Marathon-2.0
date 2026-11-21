import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import ssl
import subprocess
import threading
from typing import Iterator

import pytest

from marathon.worker.__main__ import main
from marathon.worker.runtime import WorkerRuntime
from marathon.worker.tls import create_ssl_context


@pytest.fixture(scope="module")
def tls_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[int, Path]]:
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is unavailable")
    directory = tmp_path_factory.mktemp("worker-tls")
    ca_key = directory / "ca.key"
    ca_pem = directory / "ca.pem"
    server_key = directory / "server.key"
    server_csr = directory / "server.csr"
    server_pem = directory / "server.pem"
    extensions = directory / "server.ext"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:localhost\n",
        encoding="utf-8",
    )
    subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key),
         "-out", str(ca_pem), "-subj", "/CN=Marathon Test CA", "-days", "1",
         "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign"],
        check=True, capture_output=True,
    )
    subprocess.run(
        [openssl, "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(server_key),
         "-out", str(server_csr), "-subj", "/CN=localhost"],
        check=True, capture_output=True,
    )
    subprocess.run(
        [openssl, "x509", "-req", "-in", str(server_csr), "-CA", str(ca_pem), "-CAkey", str(ca_key),
         "-CAcreateserial", "-out", str(server_pem), "-days", "1", "-extfile", str(extensions)],
        check=True, capture_output=True,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(server_pem), str(server_key))
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], ca_pem
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_default_context_verifies() -> None:
    context = create_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_missing_ca_file(tmp_path: Path) -> None:
    ca_file = tmp_path / "missing.crt"
    with pytest.raises(ValueError) as error:
        create_ssl_context(ca_file)
    assert str(error.value) == f"CA-Datei nicht lesbar: {ca_file}"


def test_invalid_ca_file(tmp_path: Path) -> None:
    ca_file = tmp_path / "invalid.crt"
    ca_file.write_text("kein Zertifikat", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        create_ssl_context(ca_file)
    assert str(error.value) == f"CA-Datei nicht lesbar: {ca_file}"


def test_post_with_ca_file_succeeds(tls_server: tuple[int, Path]) -> None:
    port, ca_file = tls_server
    runtime = WorkerRuntime(f"https://localhost:{port}", "t", "w", "W", [], ssl_context=create_ssl_context(ca_file))
    assert runtime._post("/x", {}, 5) == (200, {"ok": True})


def test_post_untrusted_certificate_fails(tls_server: tuple[int, Path]) -> None:
    port, _ = tls_server
    runtime = WorkerRuntime(f"https://localhost:{port}", "t", "w", "W", [], ssl_context=create_ssl_context())
    assert runtime._post("/x", {}, 5) == (None, {})


def test_post_wrong_hostname_fails(tls_server: tuple[int, Path]) -> None:
    port, ca_file = tls_server
    runtime = WorkerRuntime(f"https://127.0.0.1:{port}", "t", "w", "W", [], ssl_context=create_ssl_context(ca_file))
    assert runtime._post("/x", {}, 5) == (None, {})


def test_main_rejects_http(caplog: pytest.LogCaptureFixture) -> None:
    assert main(["--server", "http://127.0.0.1:9", "--worker-id", "w", "--name", "W"]) == 2
    assert "Server-Adresse muss mit https:// beginnen" in caplog.text


def test_main_rejects_missing_ca_file(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    monkeypatch.setenv("MARATHON_WORKER_TOKEN", "t")
    ca_file = tmp_path / "x.crt"
    assert main([
        "--server", "https://127.0.0.1:9", "--worker-id", "w", "--name", "W", "--ca-file", str(ca_file)
    ]) == 2
    assert "CA-Datei nicht lesbar" in caplog.text
