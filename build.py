"""Set up everything Marathon needs inside the project folder and build the web UI."""

# IMPORTS
import argparse
import hashlib
import json
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
    caddy_dir,
    caddy_exe,
    caddy_root_cert,
    client_package_dir,
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
caddy_version = "2.11.9"
caddy_release_url = f"https://github.com/caddyserver/caddy/releases/download/v{caddy_version}"
caddy_packages = {
    ("linux", "x64"): ("linux_amd64.tar.gz", "5b8cb7faf8726a2028ad96a66e54e6e81e8f361bc6175e5d22bbd997cc5177fb"),
    ("win", "x64"): ("windows_amd64.zip", "ad93112d335ee687100c4c9f11bced16ca590377976b7fe7883882b9e797e472"),
}
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
server_config_file = root_dir / "config" / "marathon.toml"
ui_dir = root_dir / "ui"
ui_package_file = ui_dir / "package.json"
ui_lock_file = ui_dir / "package-lock.json"
ui_modules_dir = ui_dir / "node_modules"
client_venv_dir = build_dir / "client-venv"
client_build_dir = build_dir / "client"
client_requirements_file = root_dir / "requirements" / "client.txt"
client_build_requirements_file = root_dir / "requirements" / "client-build.txt"
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
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            command, cwd=cwd, env=env, shell=False, timeout=timeout,
            capture_output=capture_output, text=capture_output,
        )
    except subprocess.TimeoutExpired as error:
        raise BuildError(f"Befehl überschritt das Zeitlimit von {timeout} Sekunden: {command[0]}") from error
    except OSError as error:
        raise BuildError(f"Befehl konnte nicht gestartet werden: {command[0]} ({error})") from error
    if result.returncode:
        raise BuildError(f"Befehl fehlgeschlagen (Exit-Code {result.returncode}): {' '.join(command)}")
    return result


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


def install_caddy() -> None:
    if caddy_exe.is_file() and stamp_matches("caddy", caddy_version):
        print(f"  Caddy {caddy_version} vorhanden, übersprungen")
        return
    write_stamp("caddy", "")
    try:
        key = platform_key()
    except BuildError as error:
        raise BuildError(
            f"Keine Caddy-Version für dieses System hinterlegt: {sys.platform}/{platform.machine().lower()}"
        ) from error
    package = caddy_packages.get(key)
    if package is None:
        raise BuildError(
            f"Keine Caddy-Version für dieses System hinterlegt: {sys.platform}/{platform.machine().lower()}"
        )
    suffix, expected_hash = package
    archive_name = f"caddy_{caddy_version}_{suffix}"
    archive = download_dir / archive_name
    try:
        if not archive.is_file() or file_hash(archive) != expected_hash:
            print(f"  lade {archive_name}")
            download(f"{caddy_release_url}/{archive_name}", archive)
        if file_hash(archive) != expected_hash:
            archive.unlink()
            raise BuildError(f"Prüfsumme von {archive_name} stimmt nicht, Download verworfen")

        caddy_dir.mkdir(parents=True, exist_ok=True)
        if suffix.endswith(".zip"):
            with zipfile.ZipFile(archive) as package_file:
                member = next(
                    (item for item in package_file.infolist() if item.filename == caddy_exe.name and not item.is_dir()),
                    None,
                )
                if member is None:
                    raise BuildError(f"Caddy-Archiv ist unvollständig: {caddy_exe.name} fehlt.")
                with package_file.open(member) as source, caddy_exe.open("wb") as binary:
                    shutil.copyfileobj(source, binary)
        else:
            with tarfile.open(archive, "r:gz") as package_file:
                member = next(
                    (item for item in package_file.getmembers() if item.name == caddy_exe.name and item.isfile()),
                    None,
                )
                source = package_file.extractfile(member) if member is not None else None
                if source is None:
                    raise BuildError(f"Caddy-Archiv ist unvollständig: {caddy_exe.name} fehlt.")
                with source, caddy_exe.open("wb") as binary:
                    shutil.copyfileobj(source, binary)
        if not is_windows:
            caddy_exe.chmod(0o755)
        write_stamp("caddy", caddy_version)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise BuildError(
            "Caddy konnte nicht eingerichtet werden. Beende laufende Marathon-Prozesse und starte "
            f"python build.py erneut. Ursprünglicher Fehler: {error}"
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


# FUNC: worker client
def client_venv_python() -> Path:
    return client_venv_dir / ("Scripts/python.exe" if is_windows else "bin/python")


def client_requirements_hash() -> str:
    digest = hashlib.sha256()
    for path in (client_requirements_file, client_build_requirements_file):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def setup_client_venv() -> None:
    new_venv = not client_venv_python().is_file()
    if new_venv:
        print("  lege .build/client-venv an")
        run([sys.executable, "-m", "venv", str(client_venv_dir)])
    requirements_hash = client_requirements_hash()
    if not new_venv and stamp_matches("client-requirements", requirements_hash):
        print("  Client-Pakete aktuell, übersprungen")
        return
    run([
        str(client_venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "-q",
        "-r", str(client_requirements_file), "-r", str(client_build_requirements_file),
    ])
    write_stamp("client-requirements", requirements_hash)


def client_proxy_settings(config) -> tuple[str, bool]:
    try:
        result = run([str(venv_python()), "-m", "marathon.server.db", "proxy-info"], capture_output=True)
        values = json.loads(result.stdout)
        public_url = values["public_url"]
        builtin = values["builtin"]
        if not isinstance(public_url, str) or type(builtin) is not bool:
            raise ValueError("Ungültige Proxy-Einstellungen")
        return public_url, builtin
    except (BuildError, AttributeError, KeyError, TypeError, ValueError) as error:
        print("  Server-Adresse aus config/marathon.toml (Datenbank nicht erreichbar)")
        return config.proxy.public_url, config.proxy.builtin


def write_client_version_info(path: Path) -> None:
    version = tuple(int(part) for part in app_version.split("."))
    version_tuple = (*version, 0)
    content = f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={version_tuple}, prodvers={version_tuple}, mask=0x3f, flags=0x0, OS=0x4, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Marathon'),
      StringStruct('ProductName', 'Marathon-Worker'),
      StringStruct('FileDescription', 'Marathon-Worker'),
      StringStruct('FileVersion', '{app_version}'),
      StringStruct('ProductVersion', '{app_version}'),
      StringStruct('OriginalFilename', 'MarathonWorker.exe'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
'''
    path.write_text(content, encoding="utf-8", newline="\n")


def write_client_bootstrap(path: Path, server_url: str, root_ca_pem: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(
            {"server_url": server_url, "root_ca_pem": root_ca_pem, "client_version": app_version},
            output, ensure_ascii=False, indent=2,
        )
        output.write("\n")


def publish_client(exe_path: Path, server_url: str) -> None:
    client_package_dir.mkdir(parents=True, exist_ok=True)
    target_exe = client_package_dir / "MarathonWorker.exe"
    temporary_exe = client_package_dir / ".MarathonWorker.exe.tmp"
    temporary_manifest = client_package_dir / ".client.json.tmp"
    try:
        shutil.copyfile(exe_path, temporary_exe)
        os.replace(temporary_exe, target_exe)
        manifest = {
            "version": app_version,
            "sha256": file_hash(target_exe),
            "server_url": server_url,
        }
        with temporary_manifest.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary_manifest, client_package_dir / "client.json")
    finally:
        temporary_exe.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)


def build_client(*, strict: bool = False) -> None:
    if not is_windows:
        if strict:
            raise BuildError("Der Worker-Client lässt sich nur unter Windows bauen.")
        print("  nur unter Windows, übersprungen")
        return

    required = (client_requirements_file, client_build_requirements_file)
    missing = [str(path.relative_to(root_dir)) for path in required if not path.is_file()]
    if missing:
        raise BuildError(f"Dateien fehlen im Repo: {', '.join(missing)}")
    try:
        config = load_server_config()
    except ConfigError as error:
        raise BuildError(str(error)) from error

    server_url, builtin = client_proxy_settings(config)
    if builtin:
        if not caddy_root_cert.is_file():
            message = f"Stammzertifikat fehlt ({caddy_root_cert}). Zuerst python run.py einmal starten."
            if strict:
                raise BuildError(message)
            print(message)
            return
        root_ca_pem = caddy_root_cert.read_text(encoding="utf-8")
    else:
        root_ca_pem = ""

    setup_client_venv()
    client_build_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_path = client_build_dir / "client_bootstrap.json"
    version_info_path = client_build_dir / "version_info.txt"
    write_client_bootstrap(bootstrap_path, server_url, root_ca_pem)
    write_client_version_info(version_info_path)

    command = [
        str(client_venv_python()), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "MarathonWorker", "--distpath", str(client_build_dir / "dist"),
        "--workpath", str(client_build_dir / "work"), "--specpath", str(client_build_dir),
        "--add-data", f"{bootstrap_path}{os.pathsep}.", "--paths", str(root_dir),
        "--collect-submodules", "marathon.modules", "--hidden-import", "pystray._win32",
        "--version-file", str(version_info_path), str(root_dir / "marathon" / "client" / "__main__.py"),
    ]
    run(command)
    exe_path = client_build_dir / "dist" / "MarathonWorker.exe"
    if not exe_path.is_file():
        raise BuildError(f"PyInstaller hat keine Exe in {exe_path} erzeugt")

    if config.client_build.sign_command:
        sign_command = [part.replace("{file}", str(exe_path)) for part in config.client_build.sign_command]
        try:
            run(sign_command)
        except BuildError as error:
            raise BuildError(f"Signieren fehlgeschlagen: {error}") from error
    else:
        print("  Hinweis: Worker-Client ist nicht signiert ([client_build] sign_command leer); Windows zeigt beim Start eine Warnung.")

    publish_client(exe_path, server_url)
    print("  Worker-Client bereit: .data/client/MarathonWorker.exe")


# MAIN
steps = (
    ("Python-Umgebung (.venv)", setup_venv),
    (f"Node.js {node_version} (.tools/node)", install_node),
    (f"PostgreSQL {pg_version} (.tools/postgres)", install_postgres),
    (f"Caddy {caddy_version} (.tools/caddy)", install_caddy),
    ("Datenordner (.data/postgres)", init_database),
    ("UI-Bibliotheken (ui/node_modules)", install_ui_dependencies),
    ("Oberfläche bauen (marathon/server/static)", build_ui),
    ("Worker-Client (.data/client)", build_client),
)


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(
        description="Installiert alles lokal im Projektordner, baut die Oberfläche und den Worker-Client.",
    )
    parser.add_argument("--client", action="store_true", help="Nur den Worker-Client bauen (nur Windows)")
    arguments = parser.parse_args()
    try:
        check_python()
        if arguments.client:
            print("[1/1] Worker-Client (.data/client)")
            build_client(strict=True)
        else:
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
