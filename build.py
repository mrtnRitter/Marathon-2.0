"""Set up everything Marathon needs inside the project folder and build the web UI."""

# IMPORTS
import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# CONFIG
app_name = "marathon-build"
app_version = "2.0.0"

min_python = (3, 11)
node_version = "24.22.2"
node_dist_url = f"https://nodejs.org/dist/v{node_version}"

root_dir = Path(__file__).resolve().parent
venv_dir = root_dir / ".venv"
tools_dir = root_dir / ".tools"
node_dir = tools_dir / "node"
build_dir = root_dir / ".build"
download_dir = build_dir / "downloads"
requirements_file = root_dir / "requirements" / "server.txt"
server_config_file = root_dir / "config" / "server.toml"
ui_dir = root_dir / "ui"
ui_package_file = ui_dir / "package.json"
ui_lock_file = ui_dir / "package-lock.json"
ui_modules_dir = ui_dir / "node_modules"
static_dir = root_dir / "marathon" / "server" / "static"

required_files = (requirements_file, server_config_file, ui_package_file, ui_lock_file)
is_windows = os.name == "nt"


# FUNC: paths and environment (shared with run.py)
class BuildError(Exception):
    pass


def venv_python() -> Path:
    return venv_dir / ("Scripts/python.exe" if is_windows else "bin/python")


def node_exe() -> Path:
    return node_dir / ("node.exe" if is_windows else "bin/node")


def npm_cli() -> Path:
    return node_dir / ("node_modules" if is_windows else "lib/node_modules") / "npm" / "bin" / "npm-cli.js"


def node_env() -> dict[str, str]:
    # npm scripts call "node" via PATH, so the local node must come first
    return {**os.environ, "PATH": os.pathsep.join((str(node_exe().parent), os.environ.get("PATH", "")))}


def npm_command(*args: str) -> list[str]:
    return [str(node_exe()), str(npm_cli()), *args]


def run(command: list[str], cwd: Path = root_dir, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, env=env)
    if result.returncode:
        raise BuildError(f"Befehl fehlgeschlagen (Exit-Code {result.returncode}): {' '.join(command)}")


# FUNC: change detection
def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp_matches(name: str, value: str) -> bool:
    stamp = build_dir / f"{name}.stamp"
    return stamp.is_file() and stamp.read_text(encoding="utf-8") == value


def write_stamp(name: str, value: str) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / f"{name}.stamp").write_text(value, encoding="utf-8")


# FUNC: checks
def check_python() -> None:
    if sys.version_info < min_python:
        raise BuildError(f"Python {'.'.join(map(str, min_python))} oder neuer nötig, gefunden: {platform.python_version()}")


def check_required_files() -> None:
    missing = [str(path.relative_to(root_dir)) for path in required_files if not path.is_file()]
    if missing:
        raise BuildError(f"Dateien fehlen im Repo: {', '.join(missing)}")


# FUNC: python environment
def setup_venv() -> None:
    if not venv_python().is_file():
        print("  lege .venv an")
        run([sys.executable, "-m", "venv", str(venv_dir)])
    requirements_hash = file_hash(requirements_file)
    if stamp_matches("requirements", requirements_hash):
        print("  Python-Pakete aktuell, übersprungen")
        return
    run([str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(requirements_file)])
    write_stamp("requirements", requirements_hash)


# FUNC: node.js
def node_archive_name() -> str:
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    system = {"win32": "win", "linux": "linux", "darwin": "darwin"}.get(sys.platform)
    if not arch or not system:
        raise BuildError(f"Keine Node.js-Version für dieses System hinterlegt: {sys.platform}/{machine}")
    extension = "zip" if system == "win" else "tar.xz" if system == "linux" else "tar.gz"
    return f"node-v{node_version}-{system}-{arch}.{extension}"


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as response, target.open("wb") as file:
            shutil.copyfileobj(response, file)
    except OSError as error:
        target.unlink(missing_ok=True)
        raise BuildError(f"Download fehlgeschlagen: {url} ({error})") from error


def expected_node_hash(archive_name: str) -> str:
    sums_file = download_dir / f"node-v{node_version}-SHASUMS256.txt"
    download(f"{node_dist_url}/SHASUMS256.txt", sums_file)
    sums = dict(line.split()[::-1] for line in sums_file.read_text(encoding="utf-8").splitlines() if line.strip())
    if archive_name not in sums:
        raise BuildError(f"{archive_name} fehlt in SHASUMS256.txt von Node.js {node_version}")
    return sums[archive_name]


def extract(archive: Path, target: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as file:
            file.extractall(target)
        return
    with tarfile.open(archive) as file:
        # "data" filter keeps extraction inside target (available from Python 3.11.4)
        file.extractall(target, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))


def install_node() -> None:
    if node_exe().is_file() and npm_cli().is_file() and stamp_matches("node", node_version):
        print(f"  Node.js {node_version} vorhanden, übersprungen")
        return
    archive_name = node_archive_name()
    archive = download_dir / archive_name
    expected_hash = expected_node_hash(archive_name)
    if not archive.is_file() or file_hash(archive) != expected_hash:
        print(f"  lade {archive_name}")
        download(f"{node_dist_url}/{archive_name}", archive)
    if file_hash(archive) != expected_hash:
        archive.unlink()
        raise BuildError(f"Prüfsumme von {archive_name} stimmt nicht, Download verworfen")
    try:
        tools_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="node-unpack-", dir=tools_dir, ignore_cleanup_errors=True) as temporary_dir:
            unpack_dir = Path(temporary_dir)
            extract(archive, unpack_dir)
            roots = list(unpack_dir.iterdir())
            if len(roots) != 1 or not roots[0].is_dir():
                raise BuildError("Node.js-Archiv hat eine unerwartete Ordnerstruktur.")
            source_dir = roots[0]
            for required_file in (node_exe(), npm_cli()):
                if not (source_dir / required_file.relative_to(node_dir)).is_file():
                    raise BuildError(f"Node.js-Archiv ist unvollständig: {required_file.relative_to(node_dir)} fehlt.")
            # Windows may lock whole directories; copy files instead of renaming the extracted root.
            # A failed copy must never leave a valid installation stamp.
            write_stamp("node", "")
            shutil.copytree(source_dir, node_dir, dirs_exist_ok=True)
    except (OSError, shutil.Error, tarfile.TarError, zipfile.BadZipFile) as error:
        raise BuildError(
            "Node.js-Dateien konnten nicht eingerichtet werden. "
            "Beende laufende Marathon- und Node-Prozesse. "
            "Falls das Projekt in OneDrive liegt, pausiere dessen Synchronisierung vorübergehend "
            "und starte python build.py erneut. "
            f"Ursprünglicher Fehler: {error}"
        ) from error
    write_stamp("node", node_version)


# FUNC: web ui
def install_ui_dependencies() -> None:
    lock_hash = file_hash(ui_lock_file)
    if ui_modules_dir.is_dir() and stamp_matches("ui-dependencies", lock_hash):
        print("  UI-Bibliotheken aktuell, übersprungen")
        return
    run(npm_command("ci", "--no-audit", "--no-fund"), cwd=ui_dir, env=node_env())
    write_stamp("ui-dependencies", lock_hash)


def build_ui() -> None:
    run(npm_command("run", "build"), cwd=ui_dir, env=node_env())
    if not (static_dir / "index.html").is_file():
        raise BuildError(f"UI-Build hat keine index.html in {static_dir} erzeugt")


# MAIN
steps = (
    ("Python-Umgebung (.venv)", setup_venv),
    (f"Node.js {node_version} (.tools/node)", install_node),
    ("UI-Bibliotheken (ui/node_modules)", install_ui_dependencies),
    ("Oberfläche bauen (marathon/server/static)", build_ui),
)


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    argparse.ArgumentParser(
        description="Installiert alles lokal im Projektordner (.venv, .tools/node, ui/node_modules) und baut die Oberfläche.",
    ).parse_args()
    try:
        check_python()
        check_required_files()
        for number, (title, step) in enumerate(steps, 1):
            print(f"[{number}/{len(steps)}] {title}")
            step()
    except BuildError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    print("Fertig. Starten mit: python run.py")
    return 0


# EXEC
if __name__ == "__main__":
    sys.exit(main())
