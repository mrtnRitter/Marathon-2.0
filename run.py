"""Start the Marathon database, server, and optional live UI preview."""

# IMPORTS
import argparse
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

from build import (
    BuildError,
    node_env,
    node_exe,
    pg_tool,
    root_dir,
    static_dir,
    ui_command,
    ui_dir,
    ui_modules_dir,
    venv_python,
)
from marathon.server.config import (
    ConfigError,
    ServerConfig,
    load_server_config,
    pg_data_dir,
    pg_log_file,
    pg_password_file,
)

# CONFIG
app_name = "marathon-run"
app_version = "2.0.0"

setup_hint = "Zuerst python build.py ausführen."


# FUNC
def _run_command(command: list[str], timeout: int) -> CompletedProcess:
    """Run a finite local command with a bounded wait."""
    try:
        return subprocess.run(command, cwd=root_dir, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        command_name = Path(command[0]).name
        raise BuildError(f"Zeitlimit für Befehl überschritten: {command_name}") from error
    except OSError as error:
        command_name = Path(command[0]).name
        raise BuildError(f"Befehl konnte nicht gestartet werden: {command_name} ({error})") from error


def _validate_prerequisites(dev: bool) -> None:
    required = {
        venv_python(): venv_python().is_file(),
        pg_tool("pg_ctl"): pg_tool("pg_ctl").is_file(),
        pg_data_dir / "PG_VERSION": (pg_data_dir / "PG_VERSION").is_file(),
        pg_password_file: pg_password_file.is_file(),
        static_dir / "index.html": (static_dir / "index.html").is_file(),
    }
    if dev:
        required[node_exe()] = node_exe().is_file()
        required[ui_modules_dir] = ui_modules_dir.is_dir()
    missing = [str(path.relative_to(root_dir)) if path.is_relative_to(root_dir) else str(path)
               for path, exists in required.items() if not exists]
    if missing:
        raise BuildError(f"Einrichtung unvollständig ({', '.join(missing)}). {setup_hint}")


def _api_url(host: str, port: int) -> str:
    """Return a browser-reachable URL for the configured API listener."""
    api_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return f"http://{api_host}:{port}"


def start_database(config: ServerConfig) -> None:
    """Start PostgreSQL when needed; a successful return transfers cleanup to the caller."""
    pg_ctl = str(pg_tool("pg_ctl"))
    data_path = str(pg_data_dir)
    status = _run_command([pg_ctl, "status", "-D", data_path], timeout=15)
    if status.returncode == 0:
        print("Datenbank läuft bereits")
        return

    database_config = config.database
    command = [
        pg_ctl, "start", "-D", data_path, "-l", str(pg_log_file), "-w", "-t", "30",
        "-o", f"-p {database_config.port} -h {database_config.host}",
    ]
    try:
        result = _run_command(command, timeout=45)
    except BuildError as error:
        if not isinstance(error.__cause__, subprocess.TimeoutExpired):
            raise
        try:
            status = _run_command([pg_ctl, "status", "-D", data_path], timeout=15)
        except BuildError:
            raise error
        if status.returncode == 0:
            try:
                stop_database()
            except BuildError as stop_error:
                raise BuildError(f"{error}; Bereinigung fehlgeschlagen: {stop_error}") from error
        raise
    if result.returncode:
        raise BuildError("Datenbank startet nicht, Details in .data/postgres.log")
    print(f"Datenbank läuft auf {database_config.host}:{database_config.port}")


def _run_database_command(venv: Path, subcommand: str) -> None:
    command = [str(venv), "-m", "marathon.server.db", subcommand]
    result = _run_command(command, timeout=120)
    if result.returncode:
        raise BuildError(f"Datenbank-Unterbefehl fehlgeschlagen: {subcommand}")


def _force_stop_database() -> None:
    command = [str(pg_tool("pg_ctl")), "stop", "-D", str(pg_data_dir), "-m", "immediate", "-w", "-t", "30"]
    result = _run_command(command, timeout=20)
    if result.returncode:
        status = _run_command([str(pg_tool("pg_ctl")), "status", "-D", str(pg_data_dir)], timeout=15)
        if status.returncode == 0:
            raise BuildError("Datenbank lässt sich nicht stoppen.")


def stop_database() -> None:
    """Stop the PostgreSQL cluster, escalating to immediate shutdown if needed."""
    pg_ctl = str(pg_tool("pg_ctl"))
    stop_error = None
    interrupted = False
    try:
        fast_result = _run_command(
            [pg_ctl, "stop", "-D", str(pg_data_dir), "-m", "fast", "-w", "-t", "30"],
            timeout=40,
        )
    except KeyboardInterrupt:
        interrupted = True
        fast_result = None
    except BuildError as error:
        stop_error = error
        fast_result = None

    if fast_result is not None and fast_result.returncode == 0:
        print("Datenbank gestoppt")
        return

    try:
        status = _run_command([pg_ctl, "status", "-D", str(pg_data_dir)], timeout=15)
    except KeyboardInterrupt:
        status = None
    except BuildError as error:
        stop_error = stop_error or error
        status = None

    if status is not None and status.returncode != 0 and not interrupted:
        print("Datenbank gestoppt")
        if stop_error:
            raise stop_error
        return

    force_error = None
    try:
        _force_stop_database()
    except KeyboardInterrupt:
        # A second interrupt must not prevent the bounded emergency shutdown.
        try:
            _force_stop_database()
        except (BuildError, KeyboardInterrupt) as error:
            force_error = error if isinstance(error, BuildError) else BuildError(
                "Datenbankstopp nach wiederholter Unterbrechung nicht bestätigt."
            )
    except BuildError as error:
        force_error = error
    if force_error:
        raise force_error
    print("Datenbank gestoppt")
    if stop_error:
        raise stop_error


def _finish_server(process: subprocess.Popen, command_name: str) -> None:
    """Terminate a dev server and bound both graceful and forced waits."""
    try:
        process.terminate()
        process.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    except OSError:
        pass
    except KeyboardInterrupt:
        pass

    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as error:
        raise BuildError(f"Zeitlimit beim Beenden des Servers überschritten: {command_name}") from error
    except KeyboardInterrupt:
        # The child has already received kill; do not emit a shutdown traceback.
        return


def start_dev(config: ServerConfig, venv: Path) -> int:
    """Run the server in the background while Vite serves the live preview."""
    server_command = [str(venv), "-m", "marathon.server"]
    try:
        server = subprocess.Popen(server_command, cwd=root_dir)
    except OSError as error:
        raise BuildError(f"Server konnte nicht gestartet werden: {error}") from error

    try:
        api_url = _api_url(config.host, config.port)
        env = {**node_env(), "MARATHON_API_URL": api_url}
        return subprocess.call(ui_command("vite"), cwd=ui_dir, env=env, shell=False)
    except OSError as error:
        raise BuildError(f"Live-Vorschau konnte nicht gestartet werden: {error}") from error
    finally:
        _finish_server(server, Path(server_command[0]).name)


def start_server(venv: Path) -> int:
    """Run the configured Marathon server in the foreground."""
    try:
        return subprocess.call([str(venv), "-m", "marathon.server"], cwd=root_dir)
    except OSError as error:
        raise BuildError(f"Server konnte nicht gestartet werden: {error}") from error


# MAIN
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Startet die Marathon-Datenbank und den Server oder die Live-Vorschau."
    )
    parser.add_argument("--dev", action="store_true", help="Live-Vorschau der Oberfläche starten")
    args = parser.parse_args(argv)

    exit_code = 0
    database_started = False
    try:
        _validate_prerequisites(args.dev)
        try:
            config = load_server_config()
        except ConfigError as error:
            raise BuildError(str(error)) from error

        venv = venv_python()
        try:
            start_database(config)
        except KeyboardInterrupt:
            database_started = True
            raise
        database_started = True
        _run_database_command(venv, "upgrade")
        exit_code = start_dev(config, venv) if args.dev else start_server(venv)
    except KeyboardInterrupt:
        exit_code = 0
    except BuildError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        exit_code = 1
    finally:
        if database_started:
            try:
                stop_database()
            except KeyboardInterrupt:
                # A second interrupt during cleanup is handled as a forced stop.
                try:
                    _force_stop_database()
                except (BuildError, KeyboardInterrupt) as error:
                    message = str(error) if isinstance(error, BuildError) else (
                        "Datenbankstopp nach wiederholter Unterbrechung nicht bestätigt."
                    )
                    print(f"Fehler: {message}", file=sys.stderr)
                    exit_code = 1
            except BuildError as error:
                print(f"Fehler: {error}", file=sys.stderr)
                exit_code = 1
    return exit_code


# EXEC
if __name__ == "__main__":
    sys.exit(main())
