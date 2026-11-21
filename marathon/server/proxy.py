"""HTTPS reverse-proxy settings and middleware."""

# IMPORTS
import ipaddress
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send


# STATIC
@dataclass(frozen=True)
class ProxySettings:
    """Normalized public URL and the addresses allowed to forward proxy headers."""

    public_url: str
    trusted_proxies: frozenset[str]


def proxy_settings(public_url: str, builtin: bool, trusted_proxies: Sequence[str]) -> ProxySettings:
    """Build proxy settings, adding loopback addresses for the built-in proxy."""
    trusted = set(trusted_proxies)
    if builtin:
        trusted.update(("127.0.0.1", "::1"))
    return ProxySettings(public_url, frozenset(trusted))


def upstream_address(host: str, port: int) -> str:
    """Format a backend address, replacing wildcard binds with IPv4 loopback."""
    if host in ("", "0.0.0.0", "::"):
        return f"127.0.0.1:{port}"
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def caddyfile(public_url: str, upstream: str, storage_dir: Path) -> str:
    """Render the bundled Caddy configuration for the public HTTPS endpoint."""
    https_port = urlsplit(public_url).port or 443
    root = storage_dir.resolve().as_posix()
    return (
        "{\n"
        "\tadmin off\n"
        "\tskip_install_trust\n"
        "\tauto_https disable_redirects\n"
        "\tstorage file_system {\n"
        f'\t\troot "{root}"\n'
        "\t}\n"
        f"\thttps_port {https_port}\n"
        "\tservers {\n"
        "\t\tprotocols h1 h2\n"
        "\t}\n"
        "}\n"
        "\n"
        f"{public_url} {{\n"
        "\ttls internal\n"
        f"\treverse_proxy {upstream} {{\n"
        "\t\tflush_interval -1\n"
        "\t}\n"
        "}\n"
    )


def _first_header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1").split(",", 1)[0].strip()
    return None


def _first_valid_ip(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() != name:
            continue
        for candidate in value.decode("latin-1").split(","):
            candidate = candidate.strip()
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            return candidate
    return None


def _is_loopback(peer: str) -> bool:
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return False


def _normalized_host(host: str) -> str:
    normalized = host.casefold()
    return normalized[:-4] if normalized.endswith(":443") else normalized


# MAIN
class ProxyMiddleware:
    """Apply trusted forwarding headers and redirect requests to the public HTTPS URL."""

    def __init__(self, app: ASGIApp, settings: Callable[[], ProxySettings | None]) -> None:
        """Wrap an ASGI application with per-request proxy settings."""
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Forward non-HTTP scopes and stream HTTP requests without buffering."""
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        settings = self.settings()
        if settings is None:
            await self.app(scope, receive, send)
            return

        headers = list(scope.get("headers", ()))
        client = scope.get("client")
        peer = client[0] if client else ""
        forwarded_proto = _first_header(headers, b"x-forwarded-proto")
        request_scope = scope

        if peer in settings.trusted_proxies and forwarded_proto is not None:
            forwarded_host = _first_header(headers, b"x-forwarded-host")
            host = forwarded_host if forwarded_host is not None else (_first_header(headers, b"host") or "")
            forwarded_for = _first_valid_ip(headers, b"x-forwarded-for")
            forwarded_client = (forwarded_for, 0) if forwarded_for is not None else client
            forwarded_headers = [(key, value) for key, value in headers if key.lower() != b"host"]
            forwarded_headers.append((b"host", host.encode("latin-1")))
            request_scope = {
                **scope,
                "scheme": forwarded_proto.casefold(),
                "headers": forwarded_headers,
                "client": forwarded_client,
            }
            scheme = forwarded_proto.casefold()
        elif _is_loopback(peer) and forwarded_proto is None:
            await self.app(scope, receive, send)
            return
        else:
            scheme = scope.get("scheme", "")
            host = _first_header(headers, b"host") or ""

        public_host = urlsplit(settings.public_url).netloc
        if scheme == "https" and _normalized_host(host) == _normalized_host(public_host):
            await self.app(request_scope, receive, send)
            return

        raw_path = scope.get("raw_path")
        path = raw_path.decode("latin-1") if raw_path is not None else scope.get("path", "")
        query = scope.get("query_string", b"").decode("latin-1")
        location = settings.public_url + path + (f"?{query}" if query else "")
        await send(
            {
                "type": "http.response.start",
                "status": 308,
                "headers": [(b"location", location.encode("latin-1")), (b"content-length", b"0")],
            }
        )
        await send({"type": "http.response.body", "body": b"", "more_body": False})
