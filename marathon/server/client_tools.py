# IMPORTS
import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Any

from .config import data_dir

# CONFIG
tools_root: Path = data_dir / "client-tools"
_tool_name_pattern = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_hash_cache: dict[str, tuple[int, int, str]] = {}
_cache_lock = threading.Lock()

# FUNC
def _file_sha256(path: Path) -> str:
    absolute_path = str(path.resolve())
    stat = path.stat()
    cache_key = (stat.st_size, stat.st_mtime_ns)
    with _cache_lock:
        cached = _hash_cache.get(absolute_path)
        if cached is not None and cached[:2] == cache_key:
            return cached[2]
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
        result = digest.hexdigest()
        _hash_cache[absolute_path] = (*cache_key, result)
        return result


def tool_manifest(root: Path = tools_root) -> list[dict[str, Any]]:
    """Return sorted metadata and content hashes for installed client tools."""
    if root.is_symlink() or not root.is_dir():
        return []

    manifest = []
    for tool_dir in root.iterdir():
        if (
            not _tool_name_pattern.fullmatch(tool_dir.name)
            or tool_dir.is_symlink()
            or not tool_dir.is_dir()
        ):
            continue

        files = []
        for current_dir, dirnames, filenames in os.walk(tool_dir, followlinks=False):
            current_path = Path(current_dir)
            dirnames[:] = [name for name in dirnames if not (current_path / name).is_symlink()]
            for filename in filenames:
                file_path = current_path / filename
                if file_path.is_symlink() or file_path.suffix == ".part" or not file_path.is_file():
                    continue
                files.append({
                    "path": file_path.relative_to(tool_dir).as_posix(),
                    "size": file_path.stat().st_size,
                    "sha256": _file_sha256(file_path),
                })

        if not files:
            continue
        files.sort(key=lambda item: item["path"])
        version_input = "\n".join(f"{item['path']}\0{item['sha256']}" for item in files)
        manifest.append({
            "name": tool_dir.name,
            "version": hashlib.sha256(version_input.encode("utf-8")).hexdigest()[:16],
            "files": files,
        })

    manifest.sort(key=lambda item: item["name"])
    return manifest


def tool_file(name: str, relative_path: str, root: Path = tools_root) -> Path | None:
    """Resolve an installed tool file, rejecting invalid or linked paths."""
    if (
        root.is_symlink()
        or not _tool_name_pattern.fullmatch(name)
        or not relative_path
        or "\\" in relative_path
    ):
        return None

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        return None

    tool_dir = root / name
    if tool_dir.is_symlink() or not tool_dir.is_dir():
        return None
    candidate = tool_dir
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            return None

    try:
        resolved_tool_dir = tool_dir.resolve(strict=True)
        resolved_path = candidate.resolve(strict=True)
        resolved_path.relative_to(resolved_tool_dir)
    except (OSError, RuntimeError, ValueError):
        return None

    if resolved_path.suffix == ".part" or not resolved_path.is_file():
        return None
    return resolved_path
