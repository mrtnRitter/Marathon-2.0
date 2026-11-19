"""Start the Marathon database, server, and optional live UI preview."""

# IMPORTS
import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from subprocess import CompletedProcess
from typing import TextIO

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
stop_command = "stop"


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


def _start_child(command: list[str], **kwargs) -> subprocess.Popen:
    """Start a child with isolated console input and, on Windows, a signalable process group."""
    kwargs["stdin"] = subprocess.DEVNULL
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _request_stop(process: subprocess.Popen) -> None:
    """Send the platform's graceful console-stop signal to a running child."""
    stop_signal = signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT
    try:
        process.send_signal(stop_signal)
    except OSError:
        if process.poll() is None:
            raise


def _watch_console(stop_event: threading.Event, stream: TextIO = sys.stdin) -> threading.Thread:
    """Watch console lines for the stop command without blocking process shutdown."""
    def watch() -> None:
        try:
            while line := stream.readline():
                if line.strip().casefold() == stop_command:
                    stop_event.set()
                    return
        except Exception:
            return

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def _child_name(process: subprocess.Popen) -> str:
    args = getattr(process, "args", None)
    command = args[0] if isinstance(args, (list, tuple)) and args else str(args or "Server")
    return Path(command).name


def _wait_after_kill(process: subprocess.Popen, command_name: str) -> int:
    deadline = time.monotonic() + 5.0
    timeout = 5.0
    while True:
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise BuildError(f"Zeitlimit beim Beenden des Servers überschritten: {command_name}") from error
        except KeyboardInterrupt:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                raise BuildError(f"Zeitlimit beim Beenden des Servers überschritten: {command_name}")


def _wait_child(process: subprocess.Popen, stop_event: threading.Event, grace_s: float = 10.0) -> int:
    """Wait for a child, then stop it gracefully and bound forced cleanup."""
    while not stop_event.is_set():
        try:
            return process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            continue
        except KeyboardInterrupt:
            break

    if process.poll() is not None:
        return process.wait(timeout=0)

    _request_stop(process)
    try:
        return process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        pass
    except KeyboardInterrupt:
        pass

    if process.poll() is not None:
        return process.wait(timeout=0)
    try:
        process.kill()
    except OSError:
        if process.poll() is None:
            raise
    return _wait_after_kill(process, _child_name(process))


def _finish_server(process: subprocess.Popen, command_name: str, request_stop: bool = True) -> None:
    """Gracefully stop a child and bound both graceful and forced waits."""
    if process.poll() is not None:
        return
    try:
        if request_stop:
            _request_stop(process)
        else:
            process.terminate()
    except OSError:
        if process.poll() is None:
            raise
        return

    try:
        process.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    except KeyboardInterrupt:
        pass

    if process.poll() is not None:
        return
    try:
        process.kill()
    except OSError:
        if process.poll() is None:
            raise
        return
    _wait_after_kill(process, command_name)


def _wait_for_dev_end(server: subprocess.Popen, vite: subprocess.Popen, stop_event: threading.Event) -> int | None:
    while True:
        if stop_event.is_set():
            return None
        server_code = server.poll()
        if server_code is not None:
            return server_code
        vite_code = vite.poll()
        if vite_code is not None:
            return vite_code
        time.sleep(0.2)


def start_dev(config: ServerConfig, venv: Path) -> int:
    """Run the server in the background while Vite serves the live preview."""
    server_command = [str(venv), "-m", "marathon.server"]
    server = None
    vite = None
    try:
        try:
            server = _start_child(server_command, cwd=root_dir)
        except OSError as error:
            raise BuildError(f"Server konnte nicht gestartet werden: {error}") from error

        api_url = _api_url(config.host, config.port)
        env = {**node_env(), "MARATHON_API_URL": api_url}
        try:
            vite = _start_child(ui_command("vite"), cwd=ui_dir, env=env)
        except OSError as error:
            raise BuildError(f"Live-Vorschau konnte nicht gestartet werden: {error}") from error

        print("Beenden mit „stop“ und Enter")
        stop_event = threading.Event()
        _watch_console(stop_event)
        try:
            exit_code = _wait_for_dev_end(server, vite, stop_event)
        except KeyboardInterrupt:
            exit_code = None
        return exit_code if exit_code is not None else 0
    finally:
        cleanup_error = None
        for process, name, request_stop in (
            (server, Path(server_command[0]).name, True),
            (vite, "vite", False),
        ):
            if process is None:
                continue
            try:
                _finish_server(process, name, request_stop=request_stop)
            except BuildError as error:
                cleanup_error = cleanup_error or error
            except OSError as error:
                cleanup_error = cleanup_error or BuildError(f"Server konnte nicht beendet werden: {error}")
        if cleanup_error is not None:
            raise cleanup_error


def start_server(venv: Path) -> int:
    """Run the configured Marathon server in the foreground."""
    command = [str(venv), "-m", "marathon.server"]
    try:
        process = _start_child(command, cwd=root_dir)
    except OSError as error:
        raise BuildError(f"Server konnte nicht gestartet werden: {error}") from error
    print("Beenden mit „stop“ und Enter")
    stop_event = threading.Event()
    _watch_console(stop_event)
    return _wait_child(process, stop_event)


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
