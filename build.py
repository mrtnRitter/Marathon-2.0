"""Build project-local tools, the UI, and the worker release archive."""

import ast
from contextlib import contextmanager
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path


PINS = {
    "python": {
        "version": "3.12.10",
        "url": "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip",
        "archive": "python-3.12.10-embed-amd64.zip",
    },
    "postgresql": {
        "version": "17.4",
        "url": "https://get.enterprisedb.com/postgresql/postgresql-17.4-1-windows-x64-binaries.zip",
        "archive": "postgresql-17.4-1-windows-x64-binaries.zip",
    },
    "node": {
        "version": "22.14.0",
        "archives": {
            ("Windows", "AMD64"): ("https://nodejs.org/dist/v22.14.0/node-v22.14.0-win-x64.zip", "node-v22.14.0-win-x64.zip", "zip"),
            ("Linux", "x86_64"): ("https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-x64.tar.xz", "node-v22.14.0-linux-x64.tar.xz", "tar"),
            ("Linux", "aarch64"): ("https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-arm64.tar.xz", "node-v22.14.0-linux-arm64.tar.xz", "tar"),
            ("Darwin", "x86_64"): ("https://nodejs.org/dist/v22.14.0/node-v22.14.0-darwin-x64.tar.gz", "node-v22.14.0-darwin-x64.tar.gz", "tar"),
            ("Darwin", "arm64"): ("https://nodejs.org/dist/v22.14.0/node-v22.14.0-darwin-arm64.tar.gz", "node-v22.14.0-darwin-arm64.tar.gz", "tar"),
        },
    },
    "jetbrains_mono": {
        "version": "2.304",
        "url": "https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip",
        "archive": "JetBrainsMono-2.304.zip",
        "font": "JetBrainsMono-Regular.ttf",
    },
}

ROOT = Path(__file__).resolve().parent
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._+-]+$")


def _layout(project_root):
    root = Path(project_root).resolve()
    build_dir = root / ".build"
    return {
        "root": root,
        "build": build_dir,
        "downloads": build_dir / "downloads",
        "venv": root / ".venv",
        "postgres": root / ".tools" / "postgresql",
        "postgres_data": root / ".data" / "postgresql",
        "node": root / ".tools" / "node",
        "ui": root / "ui",
        "ui_static": root / "marathon" / "server" / "static",
        "fonts": root / "ui" / "public" / "fonts",
        "client_dist": root / "dist" / "client",
        "pip_cache": build_dir / "pip-cache",
        "npm_cache": build_dir / "npm-cache",
    }


def _download(url, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _cached_download(root, filename, url, download=None):
    download = download or _download
    destination = root / ".build" / "downloads" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        download(url, destination)
    return destination


def _unpack(archive, destination, kind):
    destination = Path(destination)
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp_name:
        temp = Path(temp_name)
        if kind == "zip":
            with zipfile.ZipFile(archive) as package:
                package.extractall(temp)
        else:
            with tarfile.open(archive, "r:*") as package:
                package.extractall(temp)
        children = list(temp.iterdir())
        source = children[0] if len(children) == 1 and children[0].is_dir() else temp
        shutil.copytree(source, destination)


def _version_from_init(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    accepted = {"__version__", "VERSION", "version"}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if accepted.intersection(names):
                return str(ast.literal_eval(node.value))
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in accepted:
            return str(ast.literal_eval(node.value))
    raise ValueError("marathon/__init__.py must define a literal version")


def _check_python(version_info=None):
    version_info = version_info or sys.version_info
    if tuple(version_info[:2]) < (3, 12):
        raise RuntimeError("Python 3.12 or newer is required to build")


def _run(args, cwd, env=None):
    subprocess.run([str(part) for part in args], cwd=cwd, env=env, check=True)


def _digest_files(paths, extra=""):
    digest = hashlib.sha256(extra.encode("utf-8"))
    for path in sorted((Path(item) for item in paths), key=lambda item: str(item)):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_stamp(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _ensure_venv(paths, run=None):
    run = run or _run
    requirements = paths["root"] / "requirements.txt"
    if not requirements.is_file():
        raise FileNotFoundError(requirements)
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    stamp_value = _digest_files([requirements], python_version)
    stamp = paths["build"] / "venv-requirements.sha256"
    venv_python = paths["venv"] / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if venv_python.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8") == stamp_value:
        return
    if not venv_python.is_file():
        run([sys.executable, "-m", "venv", paths["venv"]], paths["root"])
    env = os.environ.copy()
    env["PIP_CACHE_DIR"] = str(paths["pip_cache"])
    run([venv_python, "-m", "pip", "install", "-r", requirements], paths["root"], env)
    _write_stamp(stamp, stamp_value)


def _ensure_postgresql(paths, download=None):
    download = download or _download
    pin = PINS["postgresql"]
    archive = _cached_download(paths["root"], pin["archive"], pin["url"], download)
    _unpack(archive, paths["postgres"], "zip")
    paths["postgres_data"].mkdir(parents=True, exist_ok=True)


def _node_pin(system, machine):
    machine = {"x86_64": "x86_64", "AMD64": "AMD64", "aarch64": "aarch64", "arm64": "arm64"}.get(machine, machine)
    try:
        return PINS["node"]["archives"][(system, machine)]
    except KeyError as error:
        raise RuntimeError(f"No pinned Node.js build for {system} {machine}") from error


def _ensure_node(paths, system=None, machine=None, download=None):
    download = download or _download
    system = system or platform.system()
    machine = machine or platform.machine()
    url, archive_name, kind = _node_pin(system, machine)
    archive = _cached_download(paths["root"], archive_name, url, download)
    _unpack(archive, paths["node"], kind)
    executable = paths["node"] / ("node.exe" if system == "Windows" else "bin/node")
    npm_cli = paths["node"] / "node_modules" / "npm" / "bin" / "npm-cli.js"
    if not executable.is_file() or not npm_cli.is_file():
        raise FileNotFoundError("Pinned Node.js archive is missing its executable or npm")
    return executable, npm_cli


def _ensure_font(paths, download=None):
    download = download or _download
    target = paths["fonts"] / PINS["jetbrains_mono"]["font"]
    if target.is_file():
        return target
    pin = PINS["jetbrains_mono"]
    archive = _cached_download(paths["root"], pin["archive"], pin["url"], download)
    with zipfile.ZipFile(archive) as package:
        member = next((name for name in package.namelist() if name.endswith("/ttf/" + pin["font"])), None)
        if member is None:
            raise FileNotFoundError(pin["font"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(package.read(member))
    return target


def _tree_files(directory):
    excluded = {"node_modules", "dist", ".git"}
    return [
        path for path in directory.rglob("*")
        if path.is_file() and not excluded.intersection(path.relative_to(directory).parts)
    ]


def _build_ui(paths, node, npm_cli, run=None):
    run = run or _run
    ui = paths["ui"]
    package = ui / "package.json"
    lock = ui / "package-lock.json"
    if not package.is_file():
        raise FileNotFoundError("ui/package.json is required")
    env = os.environ.copy()
    env["PATH"] = str(node.parent if node.name == "node" else paths["node"]) + os.pathsep + env.get("PATH", "")
    env["npm_config_cache"] = str(paths["npm_cache"])
    dependencies = paths["build"] / "ui-dependencies.sha256"
    dependency_files = [package, lock] if lock.is_file() else [package]
    deps_hash = _digest_files(dependency_files, PINS["node"]["version"])
    if not (ui / "node_modules").is_dir() or not dependencies.is_file() or dependencies.read_text(encoding="utf-8") != deps_hash:
        run([node, npm_cli, "ci" if lock.is_file() else "install"], ui, env)
        dependency_files = [package, lock] if lock.is_file() else [package]
        _write_stamp(dependencies, _digest_files(dependency_files, PINS["node"]["version"]))
    source_hash = _digest_files(_tree_files(ui), PINS["node"]["version"])
    build_stamp = paths["build"] / "ui-build.sha256"
    static = paths["ui_static"]
    if build_stamp.is_file() and build_stamp.read_text(encoding="utf-8") == source_hash and static.is_dir():
        return
    run([node, npm_cli, "run", "build"], ui, env)
    if not static.is_dir():
        raise FileNotFoundError("UI build did not create marathon/server/static")
    _write_stamp(build_stamp, source_hash)


@contextmanager
def _prepare_embedded_python(paths, download=None):
    download = download or _download
    pin = PINS["python"]
    archive = _cached_download(paths["root"], pin["archive"], pin["url"], download)
    with tempfile.TemporaryDirectory(dir=paths["build"]) as temp_name:
        extracted = Path(temp_name) / "python"
        extracted.mkdir()
        with zipfile.ZipFile(archive) as package:
            package.extractall(extracted)
        config = next(extracted.glob("python*._pth"), None)
        if config is None:
            raise FileNotFoundError("Embedded Python archive has no module search-path file")
        lines = config.read_text(encoding="utf-8").splitlines()
        if "." not in lines:
            lines.insert(1, ".")
        config.write_text("\n".join(lines) + "\n", encoding="utf-8")
        yield_root = extracted
        yield yield_root


def _build_client_package(paths, version, download=None):
    download = download or _download
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("Version contains characters not allowed by the package format")
    destination = paths["client_dist"] / f"{version}.zip"
    if destination.is_file():
        return destination
    source_root = paths["root"]
    source_files = [
        source_root / "marathon" / "__init__.py",
        source_root / "marathon" / "client" / "__init__.py",
        source_root / "marathon" / "client" / "starter.py",
        source_root / "marathon" / "client" / "runtime.py",
    ]
    missing = [path for path in source_files if not path.is_file()]
    if missing:
        raise FileNotFoundError(", ".join(str(path) for path in missing))
    paths["build"].mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _prepare_embedded_python(paths, download) as embedded:
        with tempfile.TemporaryDirectory(dir=paths["build"]) as temp_name:
            stage = Path(temp_name) / "bundle"
            shutil.copytree(embedded, stage)
            for source in source_files:
                relative = source.relative_to(source_root)
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            (stage / "manifest.json").write_text(json.dumps({"version": version}, ensure_ascii=False) + "\n", encoding="utf-8")
            temporary = destination.with_suffix(".zip.tmp")
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as package:
                for path in sorted(stage.rglob("*")):
                    if path.is_file():
                        package.write(path, path.relative_to(stage).as_posix())
            temporary.replace(destination)
    return destination


def build_project(project_root=ROOT, *, system=None, machine=None, download=None, run=None, python_version=None):
    download = download or _download
    run = run or _run
    _check_python(python_version)
    paths = _layout(project_root)
    version = _version_from_init(paths["root"] / "marathon" / "__init__.py")
    _ensure_venv(paths, run)
    _ensure_postgresql(paths, download)
    node, npm_cli = _ensure_node(paths, system, machine, download)
    _ensure_font(paths, download)
    _build_ui(paths, node, npm_cli, run)
    return _build_client_package(paths, version, download)


def main():
    build_project()


if __name__ == "__main__":
    main()
