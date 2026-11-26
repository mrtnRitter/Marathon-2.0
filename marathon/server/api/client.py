"""Public worker-client downloads and authenticated worker tool files."""

# IMPORTS
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from marathon.server import client_tools, config
from marathon.server.worker_auth import require_worker


# CONFIG
client_router = APIRouter(prefix="/client", tags=["client"])
tools_router = APIRouter(
    prefix="/worker/tools", tags=["worker"], dependencies=[Depends(require_worker)]
)
_client_file_name = "MarathonWorker-Setup.exe"
_client_executable = "MarathonWorker.exe"
_client_build_message = "Worker-Client noch nicht gebaut. Auf dem Server python build.py --client ausführen."
_client_address_message = "Worker-Client passt nicht zur Server-Adresse. Auf dem Server python build.py --client ausführen."
_tool_missing_message = "Zusatzprogramm nicht gefunden"
_client_media_type = "application/vnd.microsoft.portable-executable"


# FUNC
def read_client_info(directory: Path) -> dict[str, str] | None:
    """Read client metadata when both the installer and metadata are available."""
    if not (directory / _client_executable).is_file():
        return None

    try:
        payload: Any = json.loads((directory / "client.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    fields = ("version", "sha256", "server_url")
    if any(not isinstance(payload.get(field), str) for field in fields):
        return None
    return {field: payload[field] for field in fields}


def _server_address_matches(request: Request, info: dict[str, str]) -> bool:
    proxy_settings = getattr(request.app.state, "proxy_settings", None)
    if proxy_settings is None:
        return True
    return info["server_url"].rstrip("/").casefold() == proxy_settings.public_url.rstrip("/").casefold()


@client_router.get("")
def get_client_info(request: Request) -> dict[str, bool | str | None]:
    """Report whether a client installer matching this server is available."""
    info = read_client_info(config.client_package_dir)
    if info is None:
        return {
            "available": False,
            "version": None,
            "file_name": _client_file_name,
            "detail": _client_build_message,
        }
    if not _server_address_matches(request, info):
        return {
            "available": False,
            "version": info["version"],
            "file_name": _client_file_name,
            "detail": _client_address_message,
        }
    return {
        "available": True,
        "version": info["version"],
        "file_name": _client_file_name,
        "detail": None,
    }


@client_router.get("/download")
def download_client(request: Request) -> FileResponse:
    """Return the installer if it matches the configured public server address."""
    directory = config.client_package_dir
    info = read_client_info(directory)
    if info is None:
        raise HTTPException(status_code=404, detail=_client_build_message)
    if not _server_address_matches(request, info):
        raise HTTPException(status_code=409, detail=_client_address_message)
    return FileResponse(
        directory / _client_executable,
        filename=_client_file_name,
        media_type=_client_media_type,
    )


@tools_router.get("")
def get_worker_tools() -> dict[str, list[dict[str, Any]]]:
    """Return the installed tool manifest for an authenticated worker."""
    return {"tools": client_tools.tool_manifest(client_tools.tools_root)}


@tools_router.get("/{name}/files/{path:path}")
def download_worker_tool(name: str, path: str) -> FileResponse:
    """Return a validated tool file to an authenticated worker."""
    file_path = client_tools.tool_file(name, path, client_tools.tools_root)
    if file_path is None:
        raise HTTPException(status_code=404, detail=_tool_missing_message)
    return FileResponse(file_path, media_type="application/octet-stream")
