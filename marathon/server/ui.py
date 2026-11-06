"""Delivery of the built web UI with single-page-app fallback."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

default_static_dir = Path(__file__).resolve().parent / "static"


def create_ui_router(static_dir: Path) -> APIRouter:
    """Serve files from static_dir; every other path returns index.html."""
    static_dir = static_dir.resolve()
    index_file = static_dir / "index.html"
    router = APIRouter()

    @router.get("/{path:path}", include_in_schema=False)
    def serve_ui(path: str) -> FileResponse:
        file = (static_dir / path).resolve()
        if path and file.is_file() and file.is_relative_to(static_dir):
            return FileResponse(file)
        if not index_file.is_file():
            raise HTTPException(503, "Oberfläche ist nicht gebaut. Zuerst python build.py ausführen.")
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})

    return router
