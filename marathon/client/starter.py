"""Start the worker runtime and supervise client updates."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import io
import time
import tomllib
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from .runtime import UPDATE_EXIT_CODE, _read_json, _write_json

VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._+-]+$")


def _python_executable(package_root):
    candidates = (
        package_root / "python.exe",
        package_root / "python" / "python.exe",
        package_root / "python" / "bin" / "python3",
        package_root / ".venv" / "Scripts" / "python.exe",
        package_root / ".venv" / "bin" / "python",
    )
    return next((path for path in candidates if path.is_file()), Path(sys.executable))


def _launch(package_root, config_path, state_path, update_path, popen=None):
    executable = _python_executable(package_root)
    script = package_root / "marathon" / "client" / "runtime.py"
    if not script.is_file():
        raise FileNotFoundError(script)
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(package_root) + (os.pathsep + existing if existing else "")
    command = [
        str(executable), str(script), "--config", str(config_path),
        "--state", str(state_path), "--update", str(update_path),
    ]
    return (popen or subprocess.Popen)(command, cwd=package_root, env=env)


def _wait_started(process, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        time.sleep(0.05)
    return process.poll() is None


def _install_update(server_url, update, versions_dir):
    version = str(update.get("version", ""))
    download_url = update.get("download_url")
    checksum = update.get("sha256") or update.get("checksum")
    if not VERSION_PATTERN.fullmatch(version) or not download_url or not checksum:
        raise ValueError("Update requires version, download_url, and SHA-256 checksum")
    checksum = str(checksum).lower().removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Invalid update checksum")
    url = urllib.parse.urljoin(server_url.rstrip("/") + "/", str(download_url))
    versions_dir = Path(versions_dir)
    versions_dir.mkdir(parents=True, exist_ok=True)
    destination = versions_dir / version

    with tempfile.TemporaryDirectory(dir=versions_dir, prefix="stage-") as temporary:
        temporary = Path(temporary)
        archive_path = temporary / "package.zip"
        digest = hashlib.sha256()
        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=60) as response, archive_path.open("wb") as file:
            while chunk := response.read(1024 * 1024):
                file.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != checksum:
            raise ValueError("Update checksum mismatch")

        staging = temporary / "package"
        staging.mkdir()
        with zipfile.ZipFile(archive_path) as package:
            names = package.namelist()
            for name in names:
                path = PurePosixPath(name)
                if (
                    not path.parts
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in name
                    or ":" in path.parts[0]
                ):
                    raise ValueError("Unsafe path in client package")
                info = package.getinfo(name)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Symbolic links are not allowed in client packages")
            if "manifest.json" not in names:
                raise ValueError("Client package has no root manifest.json")
            manifest = json.loads(package.read("manifest.json").decode("utf-8"))
            if manifest.get("version") != version:
                raise ValueError("Client package version does not match update")
            package.extractall(staging)
        if destination.exists():
            existing = destination / "manifest.json"
            if existing.is_file() and json.loads(existing.read_text(encoding="utf-8")).get("version") == version:
                return destination
            raise ValueError("A different package already uses this version")
        os.replace(staging, destination)
    return destination


def supervise(package_root, config_path=None, state_path=None, startup_timeout=1.0, popen=None, installer=None):
    package_root = Path(package_root).resolve()
    config_path = Path(config_path or package_root / "client.toml").resolve()
    state_path = Path(state_path or package_root / ".client-state.json").resolve()
    update_path = state_path.with_suffix(".update.json")
    state = _read_json(state_path)
    active = state.get("active_version") or "."
    previous = state.get("previous_version")
    fallback_used = False
    installer = installer or _install_update

    while True:
        runtime_root = (package_root / active).resolve()
        try:
            process = _launch(runtime_root, config_path, state_path, update_path, popen)
            if not _wait_started(process, startup_timeout):
                code = process.poll() or 1
            else:
                code = process.wait()
        except (OSError, ValueError):
            code = 1

        if code == UPDATE_EXIT_CODE:
            update = _read_json(update_path)
            try:
                config = tomllib.loads(config_path.read_text(encoding="utf-8"))
                new_root = installer(config["server_url"], update, package_root / ".client" / "versions")
                relative = str(new_root.resolve().relative_to(package_root))
                state["previous_version"] = active
                state["active_version"] = relative
                _write_json(state_path, state)
                update_path.unlink(missing_ok=True)
                active = relative
                previous = state["previous_version"]
                fallback_used = False
                continue
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                code = 1

        if code != 0 and previous and not fallback_used:
            active, previous = previous, None
            state["active_version"] = active
            state["previous_version"] = None
            _write_json(state_path, state)
            fallback_used = True
            continue
        return code


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("client.toml"))
    args = parser.parse_args(argv)
    package_root = Path(__file__).resolve().parents[2]
    try:
        return supervise(package_root, args.config)
    except (OSError, ValueError) as error:
        print(f"Worker starter failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
