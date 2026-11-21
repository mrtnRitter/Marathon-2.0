"""Tests for HTTPS proxy handling."""

# IMPORTS
import asyncio
import inspect
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from marathon.server.proxy import ProxyMiddleware, ProxySettings, caddyfile, proxy_settings, upstream_address


# STATIC
PUBLIC_URL = "https://laptop-martin-r"
TRUSTED_SETTINGS = proxy_settings(PUBLIC_URL, True, ["10.0.77.20"])
USE_TEST_CLIENT = "client" in inspect.signature(TestClient).parameters


# FUNC
def _make_app(settings: ProxySettings | None):
    app = FastAPI()

    @app.get("/{path:path}")
    async def echo(request: Request):
        return {
            "scheme": request.url.scheme,
            "host": request.headers.get("host"),
            "client": request.client.host if request.client else None,
        }

    return ProxyMiddleware(app, lambda: settings)


def _request(
    settings: ProxySettings | None,
    peer: str,
    path: str = "/echo",
    headers: dict[str, str] | None = None,
    scheme: str = "http",
):
    app = _make_app(settings)
    request_headers = {key.lower().encode("latin-1"): value.encode("latin-1") for key, value in (headers or {}).items()}
    if USE_TEST_CLIENT:
        with TestClient(app, client=(peer, 123), follow_redirects=False) as client:
            return client.get(path, headers=headers, follow_redirects=False)

    sent = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": scheme,
        "path": urlsplit(path).path,
        "raw_path": urlsplit(path).path.encode("ascii"),
        "query_string": urlsplit(path).query.encode("ascii"),
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
    response_headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in sent[0]["headers"]}
    body = b"".join(message.get("body", b"") for message in sent[1:])
    return SimpleNamespace(
        status_code=sent[0]["status"],
        headers=response_headers,
        json=lambda: json.loads(body),
    )


def test_trusted_proxy_headers_applied():
    response = _request(
        TRUSTED_SETTINGS,
        "127.0.0.1",
        headers={
            "X-Forwarded-Proto": "https",
            "Host": "laptop-martin-r",
            "X-Forwarded-For": "10.1.2.3, 127.0.0.1",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"scheme": "https", "host": "laptop-martin-r", "client": "10.1.2.3"}


def test_forwarded_host_used():
    response = _request(
        TRUSTED_SETTINGS,
        "10.0.77.20",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "Laptop-Martin-R:443",
            "Host": "backend:8080",
        },
    )

    assert response.status_code == 200
    assert response.json()["host"] == "Laptop-Martin-R:443"


def test_wrong_host_redirects():
    response = _request(
        TRUSTED_SETTINGS,
        "127.0.0.1",
        "/a/b?x=1",
        {"X-Forwarded-Proto": "https", "Host": "10.0.0.5"},
    )

    assert response.status_code == 308
    assert response.headers["location"] == "https://laptop-martin-r/a/b?x=1"


def test_http_scheme_redirects():
    response = _request(
        TRUSTED_SETTINGS,
        "10.0.77.20",
        headers={"X-Forwarded-Proto": "http", "Host": "laptop-martin-r"},
    )

    assert response.status_code == 308


def test_untrusted_headers_ignored():
    response = _request(
        TRUSTED_SETTINGS,
        "10.9.9.9",
        headers={"X-Forwarded-Proto": "https", "Host": "laptop-martin-r"},
        scheme="http",
    )

    assert response.status_code == 308


def test_loopback_direct_passes():
    response = _request(
        TRUSTED_SETTINGS,
        "127.0.0.1",
        headers={"Host": "127.0.0.1:8080"},
    )

    assert response.status_code == 200


def test_builtin_off_loopback_not_trusted():
    settings = proxy_settings(PUBLIC_URL, False, ["10.0.77.20"])
    response = _request(
        settings,
        "127.0.0.1",
        headers={"X-Forwarded-Proto": "https", "Host": "laptop-martin-r"},
    )

    assert response.status_code == 308


def test_no_settings_passes():
    response = _request(None, "10.9.9.9", headers={"Host": "x"})

    assert response.status_code == 200


def test_port_in_public_url():
    settings = proxy_settings("https://host:8443", True, [])
    accepted = _request(settings, "127.0.0.1", headers={"X-Forwarded-Proto": "https", "Host": "host:8443"})
    rejected = _request(settings, "127.0.0.1", headers={"X-Forwarded-Proto": "https", "Host": "host"})

    assert accepted.status_code == 200
    assert rejected.status_code == 308
    assert rejected.headers["location"] == "https://host:8443/echo"


def test_proxy_settings_trusted():
    assert proxy_settings("https://h", True, ["10.0.0.1"]).trusted_proxies == frozenset(
        {"10.0.0.1", "127.0.0.1", "::1"}
    )
    assert proxy_settings("https://h", False, ["10.0.0.1"]).trusted_proxies == frozenset({"10.0.0.1"})


def test_upstream_address():
    values = [
        (("0.0.0.0", 8080), "127.0.0.1:8080"),
        (("", 1), "127.0.0.1:1"),
        (("::", 2), "127.0.0.1:2"),
        (("10.0.0.5", 9), "10.0.0.5:9"),
        (("fe80::1", 9), "[fe80::1]:9"),
    ]
    for (host, port), expected in values:
        assert upstream_address(host, port) == expected


def test_caddyfile_exact(tmp_path: Path):
    storage_dir = tmp_path / "caddy"
    root = storage_dir.resolve().as_posix()
    expected = (
        "{\n"
        "\tadmin off\n"
        "\tskip_install_trust\n"
        "\tauto_https disable_redirects\n"
        "\tstorage file_system {\n"
        f'\t\troot "{root}"\n'
        "\t}\n"
        "\thttps_port 443\n"
        "\tservers {\n"
        "\t\tprotocols h1 h2\n"
        "\t}\n"
        "}\n"
        "\n"
        "https://laptop-martin-r {\n"
        "\ttls internal\n"
        "\treverse_proxy 127.0.0.1:8080 {\n"
        "\t\tflush_interval -1\n"
        "\t}\n"
        "}\n"
    )

    assert caddyfile(PUBLIC_URL, "127.0.0.1:8080", storage_dir) == expected


def test_caddyfile_port(tmp_path: Path):
    result = caddyfile("https://host:8443", "127.0.0.1:8080", tmp_path)

    assert "\thttps_port 8443\n" in result
    assert "https://host:8443 {\n" in result


def test_caddyfile_adapts(tmp_path: Path):
    caddy = shutil.which("caddy")
    if caddy is None:
        pytest.skip("Caddy is not installed")

    config_path = tmp_path / "Caddyfile"
    config_path.write_text(caddyfile(PUBLIC_URL, "127.0.0.1:8080", tmp_path / "storage"), encoding="utf-8")
    result = subprocess.run(
        [caddy, "adapt", "--config", str(config_path), "--adapter", "caddyfile"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    adapted = json.loads(result.stdout)
    server = adapted["apps"]["http"]["servers"]["srv0"]
    assert server["listen"] == [":443"]
    assert adapted["admin"]["disabled"] is True
    assert server["protocols"] == ["h1", "h2"]


def test_forwarded_for_uses_first_valid_ip():
    response = _request(
        TRUSTED_SETTINGS,
        "127.0.0.1",
        headers={
            "X-Forwarded-Proto": "https",
            "Host": "laptop-martin-r",
            "X-Forwarded-For": "invalid, 10.1.2.3, 10.1.2.4",
        },
    )

    assert response.status_code == 200
    assert response.json()["client"] == "10.1.2.3"
