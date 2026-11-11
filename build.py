"""Set up everything Marathon needs inside the project folder and build the web UI."""

# IMPORTS
import argparse
import hashlib
import os
import platform
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from marathon.server.config import (
    ConfigError,
    data_dir,
    load_server_config,
    pg_bin_dir,
    pg_data_dir,
    pg_dir,
    pg_password_file,
)

# CONFIG
app_name = "marathon-build"
app_version = "2.0.0"

min_python = (3, 11, 4)
node_version = "24.22.2"
node_dist_url = f"https://nodejs.org/dist/v{node_version}"
pg_version = "18.6.0"
command_timeout = 1800
pg_maven_url = "https://repo1.maven.org/maven2/io/zonky/test/postgres"
pg_packages = {
    ("linux", "x64"): ("linux-amd64", "008ee189eaa7ca2b58bd01dd950291e638b998392cf982297acd4779ec898ac1"),
    ("win", "x64"): ("windows-amd64", "b7ef2d03588e0439f0d0a2250f43905f7464f1e96ace47703ceaf79094198150"),
}

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

ui_cli_paths = {
    "typescript": Path("typescript/bin/tsc"),
    "vite": Path("vite/bin/vite.js"),
}


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


def ui_command(tool: str, *args: str) -> list[str]:
    """Run a UI tool directly with Node, without npm's Windows .cmd wrappers."""
    if tool not in ui_cli_paths:
        raise BuildError(f"Unbekanntes UI-Werkzeug: {tool}")
    cli_file = ui_modules_dir / ui_cli_paths[tool]
    if not cli_file.is_file():
        raise BuildError(f"UI-Werkzeug fehlt: {cli_file}. Zuerst python build.py ausführen.")
    return [str(node_exe()), str(cli_file), *args]


def run(
    command: list[str],
    cwd: Path = root_dir,
    env: dict[str, str] | None = None,
    timeout: int = command_timeout,
) -> None:
    try:
        result = subprocess.run(command, cwd=cwd, env=env, shell=False, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise BuildError(f"Befehl überschritt das Zeitlimit von {timeout} Sekunden: {command[0]}") from error
    except OSError as error:
        raise BuildError(f"Befehl konnte nicht gestartet werden: {command[0]} ({error})") from error
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
    if sys.version_info[:3] < min_python:
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
def platform_key() -> tuple[str, str]:
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    system = {"win32": "win", "linux": "linux", "darwin": "darwin"}.get(sys.platform)
    if not system or not arch:
        raise BuildError(f"Unbekanntes System: {sys.platform}/{machine}")
    return system, arch


def node_archive_name() -> str:
    system, arch = platform_key()
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
    except BaseException:
        target.unlink(missing_ok=True)
        raise


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
        # Python 3.11.4 is the minimum supported version with the data filter.
        file.extractall(target, filter="data")


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


def pg_tool(name: str) -> Path:
    return pg_bin_dir / f"{name}{'.exe' if is_windows else ''}"


def install_postgres() -> None:
    required_tools = ("initdb", "pg_ctl", "postgres")
    if all(pg_tool(name).is_file() for name in required_tools) and stamp_matches("postgres", pg_version):
        print(f"  PostgreSQL {pg_version} vorhanden, übersprungen")
        return
    write_stamp("postgres", "")
    key = platform_key()
    package = pg_packages.get(key)
    if package is None:
        machine = platform.machine().lower()
        raise BuildError(f"Keine PostgreSQL-Version für dieses System hinterlegt: {sys.platform}/{machine}")
    package_name, expected_hash = package
    artifact = f"embedded-postgres-binaries-{package_name}"
    archive_name = f"{artifact}-{pg_version}.jar"
    archive = download_dir / archive_name
    try:
        if not archive.is_file() or file_hash(archive) != expected_hash:
            print(f"  lade {archive_name}")
            download(f"{pg_maven_url}/{artifact}/{pg_version}/{archive_name}", archive)
        if file_hash(archive) != expected_hash:
            archive.unlink(missing_ok=True)
            raise BuildError(f"Prüfsumme von {archive_name} stimmt nicht, Download verworfen")
    except OSError as error:
        raise BuildError(f"PostgreSQL-Archiv konnte nicht geprüft werden: {error}") from error
    try:
        tools_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="postgres-unpack-", dir=tools_dir, ignore_cleanup_errors=True) as temporary_dir:
            unpack_dir = Path(temporary_dir)
            with zipfile.ZipFile(archive) as jar_file:
                txz_entries = [name for name in jar_file.namelist() if name.lower().endswith(".txz")]
                if len(txz_entries) != 1:
                    raise BuildError("PostgreSQL-Archiv muss genau einen .txz-Eintrag enthalten.")
                txz_file = unpack_dir / "postgres.txz"
                with jar_file.open(txz_entries[0]) as source, txz_file.open("wb") as target:
                    shutil.copyfileobj(source, target)
            with tarfile.open(txz_file, "r:xz") as tar_file:
                tar_file.extractall(unpack_dir, filter="data")
            txz_file.unlink()
            bin_names = tuple(f"{name}{'.exe' if is_windows else ''}" for name in required_tools)
            candidates = [
                path.parent.parent
                for path in unpack_dir.rglob(bin_names[0])
                if path.is_file() and path.parent.name == "bin"
                and all((path.parent / name).is_file() for name in bin_names)
            ]
            if not candidates:
                raise BuildError("PostgreSQL-Archiv ist unvollständig: initdb, pg_ctl oder postgres fehlt.")
            source_dir = candidates[0]
            write_stamp("postgres", "")
            shutil.copytree(source_dir, pg_dir, dirs_exist_ok=True)
        write_stamp("postgres", pg_version)
    except (OSError, shutil.Error, tarfile.TarError, zipfile.BadZipFile) as error:
        raise BuildError(
            "PostgreSQL-Dateien konnten nicht eingerichtet werden. "
            "Beende laufende PostgreSQL-Prozesse und starte python build.py erneut. "
            f"Ursprünglicher Fehler: {error}"
        ) from error


def init_database() -> None:
    try:
        config = load_server_config()
    except ConfigError as error:
        raise BuildError(str(error)) from error
    pg_version_file = pg_data_dir / "PG_VERSION"
    if pg_version_file.is_file():
        if not pg_password_file.is_file():
            raise BuildError(
                "Passwortdatei für die vorhandene Datenbank fehlt. "
                "Lösche .data/postgres (löscht alle Daten) und führe python build.py erneut aus."
            )
        print("  Datenordner (.data/postgres) vorhanden, übersprungen")
        return
    if pg_data_dir.exists() and any(pg_data_dir.iterdir()):
        raise BuildError(".data/postgres ist nicht leer und enthält keine PG_VERSION. Lösche den Ordner und führe python build.py erneut aus.")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        pg_password_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        if os.name != "nt":
            pg_password_file.chmod(0o600)
        run([
            str(pg_tool("initdb")),
            "-D", str(pg_data_dir),
            "-U", config.database.user,
            "-E", "UTF8",
            "--locale=C",
            "--auth=scram-sha-256",
            f"--pwfile={pg_password_file}",
            "--no-instructions",
        ])
    except BaseException as error:
        try:
            if pg_data_dir.exists():
                shutil.rmtree(pg_data_dir)
            pg_password_file.unlink(missing_ok=True)
        except OSError as cleanup_error:
            raise BuildError(f"PostgreSQL-Initialisierung fehlgeschlagen; Bereinigung nicht vollständig: {cleanup_error}") from error
        if isinstance(error, (BuildError, KeyboardInterrupt, SystemExit)):
            raise
        raise BuildError(f"PostgreSQL-Datenordner konnte nicht initialisiert werden: {error}") from error


# FUNC: web ui
def install_ui_dependencies() -> None:
    lock_hash = file_hash(ui_lock_file)
    if ui_modules_dir.is_dir() and stamp_matches("ui-dependencies", lock_hash):
        print("  UI-Bibliotheken aktuell, übersprungen")
        return
    run(npm_command("ci", "--no-audit", "--no-fund"), cwd=ui_dir, env=node_env())
    write_stamp("ui-dependencies", lock_hash)


def build_ui() -> None:
    # Arguments stay separate: spaces and & in the repo path must never reach cmd.exe.
    for command in (ui_command("typescript", "-p", "tsconfig.json"), ui_command("vite", "build")):
        run(command, cwd=ui_dir, env=node_env())
    if not (static_dir / "index.html").is_file():
        raise BuildError(f"UI-Build hat keine index.html in {static_dir} erzeugt")


# MAIN
steps = (
    ("Python-Umgebung (.venv)", setup_venv),
    (f"Node.js {node_version} (.tools/node)", install_node),
    (f"PostgreSQL {pg_version} (.tools/postgres)", install_postgres),
    ("Datenordner (.data/postgres)", init_database),
    ("UI-Bibliotheken (ui/node_modules)", install_ui_dependencies),
    ("Oberfläche bauen (marathon/server/static)", build_ui),
)


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    argparse.ArgumentParser(
        description="Installiert alles lokal im Projektordner (.venv, .tools/node, .tools/postgres, .data, ui/node_modules) und baut die Oberfläche.",
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
    except Exception as error:
        build_error = BuildError(f"Build fehlgeschlagen: {error}")
        print(f"Fehler: {build_error}", file=sys.stderr)
        return 1
    print("Fertig. Starten mit: python run.py")
    return 0


# EXEC
if __name__ == "__main__":
    sys.exit(main())
