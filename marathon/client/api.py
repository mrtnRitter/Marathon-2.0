# IMPORTS
import http.client
import json
import ssl
import threading
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from marathon.worker.tls import create_ssl_context



# CONFIG
API_PREFIX = "/api/v1/"
DOWNLOAD_BLOCK_SIZE = 1024 * 1024


# FUNC
class ApiError(Exception):
    """Represent a failed server request without exposing response contents."""

    def __init__(self, status: int | None, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


def _check_path(path: str) -> None:
    if not path.startswith(API_PREFIX):
        raise ValueError("API-Pfad muss mit /api/v1/ beginnen")


def _error_detail(status: int, body: bytes) -> str:
    try:
        response = json.loads(body) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        response = {}
    if isinstance(response, dict) and isinstance(response.get("detail"), str):
        return response["detail"]
    return f"Serverfehler {status}"


def _raise_http_error(error: HTTPError) -> None:
    try:
        body = error.read()
    except (URLError, http.client.HTTPException, TimeoutError, OSError):
        body = b""
    raise ApiError(error.code, _error_detail(error.code, body)) from error


def _raise_unreachable(error: BaseException) -> None:
    raise ApiError(None, "Server nicht erreichbar") from error


class ClientApi:
    """Make authenticated JSON and file requests to a Marathon server."""

    def __init__(
        self,
        server_url: str,
        ssl_context: ssl.SSLContext | None,
        token: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.server_url = server_url
        self.ssl_context = ssl_context
        self.token = token
        self.timeout_s = timeout_s

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None,
                 timeout_s: float | None = None) -> tuple[int, bytes]:
        _check_path(path)
        headers = {"Content-Type": "application/json"}
        if self.token is not None:
            headers["Authorization"] = f"Bearer {self.token}"
        data = json.dumps(dict(payload or {})).encode("utf-8") if method == "POST" else None
        request = Request(self.server_url.rstrip("/") + path, data=data, headers=headers, method=method)
        try:
            with urlopen(
                request,
                timeout=self.timeout_s if timeout_s is None else timeout_s,
                context=self.ssl_context,
            ) as response:
                return response.status, response.read()
        except HTTPError as error:
            _raise_http_error(error)
        except (URLError, http.client.HTTPException, TimeoutError, OSError) as error:
            _raise_unreachable(error)
        raise ApiError(None, "Server nicht erreichbar")

    @staticmethod
    def _decode_json(status: int, body: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(body) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ApiError(status, "Ungültige Antwort vom Server") from error
        if not isinstance(decoded, dict):
            raise ApiError(status, "Ungültige Antwort vom Server")
        return decoded

    def post_json(self, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """POST a JSON object and return the JSON object response."""
        status, body = self._request("POST", path, payload)
        return self._decode_json(status, body)

    def get_json(self, path: str) -> dict[str, Any]:
        """GET a JSON object and return the JSON object response."""
        status, body = self._request("GET", path)
        return self._decode_json(status, body)

    def download(
        self,
        path: str,
        target: Path,
        stop: threading.Event,
        read_timeout_s: float = 30.0,
    ) -> None:
        """Stream a response to target, removing partial files after any failure."""
        try:
            _check_path(path)
            if stop.is_set():
                raise ApiError(None, "Abgebrochen")
            headers = {"Content-Type": "application/json"}
            if self.token is not None:
                headers["Authorization"] = f"Bearer {self.token}"
            request = Request(self.server_url.rstrip("/") + path, headers=headers, method="GET")
            try:
                response = urlopen(request, timeout=read_timeout_s, context=self.ssl_context)
            except HTTPError as error:
                _raise_http_error(error)
            except (URLError, http.client.HTTPException, TimeoutError, OSError) as error:
                _raise_unreachable(error)
            with response, target.open("wb") as output:
                while True:
                    if stop.is_set():
                        raise ApiError(None, "Abgebrochen")
                    try:
                        block = response.read(DOWNLOAD_BLOCK_SIZE)
                    except (URLError, http.client.HTTPException, TimeoutError, OSError) as error:
                        _raise_unreachable(error)
                    if not block:
                        break
                    output.write(block)
        except BaseException:
            target.unlink(missing_ok=True)
            raise


def client_ssl_context(root_ca_pem: str) -> ssl.SSLContext:
    """Build a verified client TLS context from a bundled root or system roots."""
    if not root_ca_pem:
        context = create_ssl_context(None)
    else:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try:
            context.load_verify_locations(cadata=root_ca_pem)
        except (ssl.SSLError, ValueError) as error:
            raise ValueError("Stammzertifikat im Client-Paket ist ungültig") from error
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    return context


def enroll(api: ClientApi, hostname: str, platform_name: str, client_version: str) -> tuple[str, str, bool]:
    """Register this client and return its assigned identity and approval state."""
    response = api.post_json(
        "/api/v1/worker/enroll",
        {"hostname": hostname, "platform": platform_name, "client_version": client_version},
    )
    return response["worker_id"], response["worker_token"], response["approved"]


def request_control_link(api: ClientApi) -> str:
    """Request a one-time control link for the enrolled worker."""
    return api.post_json("/api/v1/worker/control-link")["url"]


def tool_fetchers(
    api: ClientApi,
) -> tuple[Callable[[], list[dict[str, Any]]], Callable[[str, str, Path, threading.Event], None]]:
    """Return callbacks for the tool manifest and encoded tool-file downloads."""
    def list_tools() -> list[dict[str, Any]]:
        return api.get_json("/api/v1/worker/tools")["tools"]

    def fetch_file(name: str, relative_path: str, target: Path, stop: threading.Event) -> None:
        quoted_name = quote(name, safe="")
        quoted_path = "/".join(quote(segment, safe="") for segment in relative_path.split("/"))
        path = f"/api/v1/worker/tools/{quoted_name}/files/{quoted_path}"
        api.download(path, target, stop)

    return list_tools, fetch_file
