"""Server-side directory browser API."""

# IMPORTS
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout, wait
from os import path as os_path
import os
import re
from string import ascii_uppercase
from time import monotonic
from typing import Any

from fastapi import APIRouter, HTTPException

from marathon.modules.watchfolder.rules import validate_path

# CONFIG
router = APIRouter(tags=["fs"])
list_timeout_s = 10.0
max_entries = 1000
os_name = os.name
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="marathon-fs")

# FUNC
def _list_dirs(p: str) -> tuple[list[dict[str, str]], bool]:
    """Return sorted child directories and whether the result was truncated."""
    dirs = []
    with os.scandir(p) as entries:
        for entry in entries:
            try:
                if entry.is_dir():
                    dirs.append({"name": entry.name, "path": os.path.join(p, entry.name)})
            except OSError:
                continue
    dirs.sort(key=lambda item: item["name"].casefold())
    return dirs[:max_entries], len(dirs) > max_entries


def _drive_exists(drive: str) -> bool:
    """Return whether a Windows drive root is available."""
    return os_path.isdir(f"{drive}:\\")


def _is_root_path(p: str) -> bool:
    """Return whether p denotes a filesystem, drive, or UNC share root."""
    if p == "/":
        return True
    if len(p) >= 3 and p[0].isalpha() and p[1] == ":":
        return not p[2:].strip("\\/")
    return re.fullmatch(r"\\\\[^\\/]+[\\/][^\\/]+", p.rstrip("\\/")) is not None


def _root_dirs() -> list[dict[str, str]]:
    """Return available roots, omitting Windows drives that miss the deadline."""
    if os_name != "nt":
        future = _executor.submit(os_path.isdir, "/")
        try:
            future.result(timeout=list_timeout_s)
        except FutureTimeout:
            future.cancel()
            raise HTTPException(status_code=504, detail="Ordner antwortet nicht: /") from None
        except Exception as error:
            raise HTTPException(status_code=502, detail="Ordner nicht lesbar: /") from error
        return [{"name": "/", "path": "/"}]

    try:
        deadline = monotonic() + list_timeout_s
        futures: dict[Future[bool], str] = {
            _executor.submit(_drive_exists, drive): drive for drive in ascii_uppercase
        }
        done, pending = wait(futures, timeout=max(0.0, deadline - monotonic()))
        for future in pending:
            future.cancel()
        drives = []
        for future in done:
            try:
                if future.result():
                    drives.append(futures[future])
            except Exception:
                continue
    except Exception as error:
        raise HTTPException(status_code=502, detail="Ordner nicht lesbar: /") from error
    return [
        {"name": f"{drive}:", "path": f"{drive}:\\"}
        for drive in sorted(drives, key=str.casefold)
    ]


# ROUTES
@router.get("/fs/dirs")
def get_dirs(path: str | None = None) -> dict[str, Any]:
    """List child directories for a server-side absolute path or filesystem roots."""
    if path is None or not path.strip():
        return {"path": None, "parent": None, "dirs": _root_dirs(), "truncated": False}

    p = path.strip()
    validation_error = validate_path(p)
    if validation_error:
        raise HTTPException(status_code=422, detail=validation_error)

    future: Future[tuple[list[dict[str, str]], bool]] | None = None
    try:
        future = _executor.submit(_list_dirs, p)
        dirs, truncated = future.result(timeout=list_timeout_s)
    except FutureTimeout:
        if future is not None:
            future.cancel()
        raise HTTPException(status_code=504, detail=f"Ordner antwortet nicht: {p}") from None
    except (FileNotFoundError, NotADirectoryError) as error:
        raise HTTPException(status_code=404, detail=f"Ordner nicht gefunden: {p}") from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=f"Kein Zugriff auf {p}") from error
    except OSError as error:
        raise HTTPException(status_code=502, detail=f"Ordner nicht lesbar: {p}") from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Ordner nicht lesbar: {p}") from error

    parent = None if _is_root_path(p) else os.path.dirname(p.rstrip("\\/"))
    return {"path": p, "parent": parent, "dirs": dirs, "truncated": truncated}
