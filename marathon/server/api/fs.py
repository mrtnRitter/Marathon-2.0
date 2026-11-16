"""Server-side directory browser API."""

# IMPORTS
from collections.abc import Iterable
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


def _visible_shares(entries: Iterable[tuple[str, int]], host: str) -> list[dict[str, str]]:
    """Return sorted disk shares, excluding special shares."""
    shares = [
        {"name": name, "path": f"\\\\{host}\\{name}"}
        for name, share_type in entries
        if share_type & 0xFF == 0 and not share_type & 0x80000000
    ]
    shares.sort(key=lambda item: item["name"].casefold())
    return shares


def _list_shares(host: str) -> tuple[list[dict[str, str]], bool]:
    """Return remote disk shares and whether the result was truncated."""
    if os_name != "nt":
        raise OSError("NetShareEnum requires Windows")

    import ctypes
    from ctypes import wintypes

    class SHARE_INFO_1(ctypes.Structure):
        _fields_ = [
            ("shi1_netname", wintypes.LPWSTR),
            ("shi1_type", wintypes.DWORD),
            ("shi1_remark", wintypes.LPWSTR),
        ]

    netapi32 = ctypes.WinDLL("Netapi32", use_last_error=True)
    net_share_enum = netapi32.NetShareEnum
    net_share_enum.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    net_share_enum.restype = wintypes.DWORD
    net_api_buffer_free = netapi32.NetApiBufferFree
    net_api_buffer_free.argtypes = [ctypes.c_void_p]
    net_api_buffer_free.restype = wintypes.DWORD

    entries: list[tuple[str, int]] = []
    resume_handle = wintypes.DWORD(0)
    while True:
        buffer = ctypes.c_void_p()
        entries_read = wintypes.DWORD(0)
        total_entries = wintypes.DWORD(0)
        previous_resume_handle = resume_handle.value
        status = net_share_enum(
            f"\\\\{host}",
            1,
            ctypes.byref(buffer),
            0xFFFFFFFF,
            ctypes.byref(entries_read),
            ctypes.byref(total_entries),
            ctypes.byref(resume_handle),
        )
        try:
            if status not in (0, 234):
                if status == 5:
                    raise PermissionError(status, f"Access denied: \\\\{host}")
                if status in (53, 67, 2250):
                    raise FileNotFoundError(status, f"Computer not found: \\\\{host}")
                raise OSError(status, f"NetShareEnum failed: \\\\{host}")
            if buffer.value:
                shares = ctypes.cast(buffer, ctypes.POINTER(SHARE_INFO_1))
                entries.extend(
                    (shares[index].shi1_netname, shares[index].shi1_type)
                    for index in range(entries_read.value)
                )
        finally:
            if buffer.value:
                net_api_buffer_free(buffer)
        if status == 234 and resume_handle.value == previous_resume_handle:
            raise OSError(status, f"NetShareEnum did not advance: \\\\{host}")
        if status != 234:
            break

    shares = _visible_shares(entries, host)
    return shares[:max_entries], len(shares) > max_entries


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
    host_match = re.fullmatch(r"\\\\([^\\/]+)[\\/]?", p)
    if host_match:
        host = host_match.group(1)
        if os_name != "nt":
            raise HTTPException(
                status_code=422,
                detail="Freigaben eines Rechners lassen sich nur auf einem Windows-Server anzeigen",
            )
        host_path = f"\\\\{host}"
        share_future: Future[tuple[list[dict[str, str]], bool]] | None = None
        try:
            share_future = _executor.submit(_list_shares, host)
            dirs, truncated = share_future.result(timeout=list_timeout_s)
        except FutureTimeout:
            if share_future is not None:
                share_future.cancel()
            raise HTTPException(status_code=504, detail=f"Rechner antwortet nicht: {host_path}") from None
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=f"Kein Zugriff auf {host_path}") from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=f"Rechner nicht gefunden: {host_path}") from error
        except Exception as error:
            raise HTTPException(status_code=502, detail=f"Freigaben nicht lesbar: {host_path}") from error
        return {"path": host_path, "parent": None, "dirs": dirs, "truncated": truncated}

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

    share_root = re.fullmatch(r"\\\\([^\\/]+)[\\/][^\\/]+", p.rstrip("\\/"))
    if os_name == "nt" and share_root:
        parent = f"\\\\{share_root.group(1)}"
    else:
        parent = None if _is_root_path(p) else os.path.dirname(p.rstrip("\\/"))
    return {"path": p, "parent": parent, "dirs": dirs, "truncated": truncated}
