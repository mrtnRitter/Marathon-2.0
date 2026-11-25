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
from typing import Callable, TextIO

from build import (
    BuildError,
    is_windows,
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
    caddy_root_cert,
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
restart_command = "restart"
restart_exit_code = 75
console_admin_code_timeout_s = 30.0
console_admin_code_poll_interval_s = 0.1
console_admin_code_stop_timeout_s = 5.0


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


def trust_certificate() -> int:
    if not caddy_root_cert.is_file():
        print(
            f"Fehler: Stammzertifikat fehlt ({caddy_root_cert}). Zuerst python run.py einmal starten.",
            file=sys.stderr,
        )
        return 1
    if not is_windows:
        print(f"Stammzertifikat: {caddy_root_cert}")
        print(
            "Unter Linux in den Zertifikatsspeicher des Systems übernehmen, z. B. nach "
            "/usr/local/share/ca-certificates/marathon.crt kopieren und update-ca-certificates ausführen."
        )
        return 0
    try:
        result = _run_command(
            ["certutil", "-user", "-addstore", "Root", str(caddy_root_cert)], timeout=120
        )
    except BuildError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(f"Fehler: Import des Stammzertifikats fehlgeschlagen (Exit-Code {result.returncode})", file=sys.stderr)
        return 1
    print("Stammzertifikat importiert. Browser neu starten.")
    return 0


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


class _DatabaseStartInterrupted(KeyboardInterrupt):
    """Signal that a one-shot database start may have launched PostgreSQL."""


def start_database(config: ServerConfig) -> bool:
    """Start PostgreSQL when needed and report whether this call started it."""
    pg_ctl = str(pg_tool("pg_ctl"))
    data_path = str(pg_data_dir)
    status = _run_command([pg_ctl, "status", "-D", data_path], timeout=15)
    if status.returncode == 0:
        print("Datenbank läuft bereits")
        return False

    database_config = config.database
    command = [
        pg_ctl, "start", "-D", data_path, "-l", str(pg_log_file), "-w", "-t", "30",
        "-o", f"-p {database_config.port} -h {database_config.host}",
    ]
    try:
        result = _run_command(command, timeout=45)
        if result.returncode:
            raise BuildError("Datenbank startet nicht, Details in .data/postgres.log")
        print(f"Datenbank läuft auf {database_config.host}:{database_config.port}")
    except KeyboardInterrupt as error:
        raise _DatabaseStartInterrupted from error
    except BuildError as error:
        if not isinstance(error.__cause__, subprocess.TimeoutExpired):
            raise
        try:
            status = _run_command([pg_ctl, "status", "-D", data_path], timeout=15)
        except KeyboardInterrupt as interrupt:
            raise _DatabaseStartInterrupted from interrupt
        except BuildError:
            raise error
        if status.returncode == 0:
            try:
                stop_database()
            except BuildError as stop_error:
                raise BuildError(f"{error}; Bereinigung fehlgeschlagen: {stop_error}") from error
        raise
    return True


def _run_database_command(venv: Path, subcommand: str) -> None:
    command = [str(venv), "-m", "marathon.server.db", subcommand]
    result = _run_command(command, timeout=120)
    if result.returncode:
        raise BuildError(f"Datenbank-Unterbefehl fehlgeschlagen: {subcommand}")


def _mail_test(address: str) -> int:
    """Run the SMTP diagnostic command."""
    command = [str(venv_python()), "-m", "marathon.server.mail", "test", address]
    try:
        return _run_command(command, timeout=60).returncode
    except BuildError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1


def _admin_code() -> int:
    """Run the emergency admin-code command."""
    command = [str(venv_python()), "-m", "marathon.server.auth", "admin-code"]
    try:
        return _run_command(command, timeout=60).returncode
    except BuildError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1


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


class _ConsoleAdminCodeJobs:
    """Run console admin-code subprocesses without blocking console control."""

    def __init__(self) -> None:
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        thread = threading.Thread(target=self._run, daemon=True)
        with self._lock:
            self._threads.append(thread)
        thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()
        with self._lock:
            processes = tuple(self._processes)
        for process in processes:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass

    def _stop_process(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            if process.poll() is not None:
                return
            raise
        try:
            process.wait(timeout=console_admin_code_stop_timeout_s)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            process.kill()
        except OSError:
            if process.poll() is not None:
                return
            raise
        try:
            process.wait(timeout=console_admin_code_stop_timeout_s)
        except subprocess.TimeoutExpired:
            return

    def _run(self) -> None:
        if self._cancel_event.is_set():
            return
        command = [str(venv_python()), "-m", "marathon.server.auth", "admin-code"]
        try:
            process = subprocess.Popen(command, cwd=root_dir, stdin=subprocess.DEVNULL)
        except OSError as error:
            print(f"Fehler: admin-code konnte nicht gestartet werden ({error})", file=sys.stderr)
            return

        with self._lock:
            self._processes.add(process)
        try:
            deadline = time.monotonic() + console_admin_code_timeout_s
            while True:
                if self._cancel_event.is_set():
                    self._stop_process(process)
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    print("Fehler: admin-code hat nicht rechtzeitig geantwortet")
                    self._stop_process(process)
                    return
                try:
                    process.wait(timeout=min(remaining, console_admin_code_poll_interval_s))
                    return
                except subprocess.TimeoutExpired:
                    continue
        finally:
            with self._lock:
                self._processes.discard(process)


def _watch_console(
    stop_event: threading.Event,
    stream: TextIO = sys.stdin,
    restart_event: threading.Event | None = None,
) -> threading.Thread:
    """Watch console lines for stop, restart, and asynchronous admin-code commands."""
    admin_code_jobs = getattr(stop_event, "_admin_code_jobs", None)
    if admin_code_jobs is None:
        admin_code_jobs = _ConsoleAdminCodeJobs()
        stop_event._admin_code_jobs = admin_code_jobs

    def watch() -> None:
        try:
            while line := stream.readline():
                normalized = line.strip().casefold()
                if normalized == stop_command:
                    stop_event.set()
                    admin_code_jobs.cancel()
                    return
                if normalized == restart_command and restart_event is not None and not stop_event.is_set():
                    restart_event.set()
                    continue
                parts = line.strip().split(maxsplit=1)
                if parts and parts[0].casefold() == "admin-code":
                    if len(parts) == 1:
                        admin_code_jobs.start()
                    else:
                        print("Fehler: admin-code erwartet keine Adresse")
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


def _wait_child(
    process: subprocess.Popen,
    stop_event: threading.Event,
    grace_s: float = 10.0,
    restart_event: threading.Event | None = None,
) -> int:
    """Wait for a child, then stop it gracefully and bound forced cleanup."""
    while not stop_event.is_set() and not (restart_event and restart_event.is_set()):
        try:
            return process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            continue
        except KeyboardInterrupt:
            stop_event.set()
            break

    if process.poll() is not None:
        return process.wait(timeout=0)

    _request_stop(process)
    try:
        return process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        pass
    except KeyboardInterrupt:
        stop_event.set()

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


def _wait_for_dev_end(
    server: subprocess.Popen,
    vite: subprocess.Popen,
    stop_event: threading.Event,
    restart_event: threading.Event,
) -> tuple[int | None, str]:
    while True:
        if stop_event.is_set():
            return _wait_child(server, stop_event, restart_event=restart_event), "stop"
        server_code = server.poll()
        if server_code is not None:
            return server_code, "server"
        vite_code = vite.poll()
        if vite_code is not None:
            return vite_code, "vite"
        if restart_event.is_set():
            code = _wait_child(server, stop_event, restart_event=restart_event)
            return code, "stop" if stop_event.is_set() else "restart"
        time.sleep(0.2)


def start_dev(config: ServerConfig, venv: Path) -> int:
    """Run the server in the background while Vite serves the live preview."""
    server_command = [str(venv), "-m", "marathon.server"]
    server = None
    vite = None
    admin_code_jobs = None

    def start_server_child() -> subprocess.Popen:
        try:
            return _start_child(server_command, cwd=root_dir)
        except OSError as error:
            raise BuildError(f"Server konnte nicht gestartet werden: {error}") from error

    try:
        server = start_server_child()
        api_url = _api_url(config.host, config.port)
        env = {**node_env(), "MARATHON_API_URL": api_url}
        try:
            vite = _start_child(ui_command("vite"), cwd=ui_dir, env=env)
        except OSError as error:
            raise BuildError(f"Live-Vorschau konnte nicht gestartet werden: {error}") from error

        print("Beenden mit „stop“, Neustart mit „restart“ und Enter")
        stop_event = threading.Event()
        admin_code_jobs = _ConsoleAdminCodeJobs()
        stop_event._admin_code_jobs = admin_code_jobs
        restart_event = threading.Event()
        _watch_console(stop_event, restart_event=restart_event)
        try:
            while True:
                exit_code, reason = _wait_for_dev_end(server, vite, stop_event, restart_event)
                if stop_event.is_set() or reason == "stop":
                    return 0
                if reason == "vite":
                    return exit_code if exit_code is not None else 0
                if restart_event.is_set() or exit_code == restart_exit_code:
                    restart_event.clear()
                    print("Server wird neu gestartet …")
                    server = start_server_child()
                    continue
                return exit_code if exit_code is not None else 0
        except KeyboardInterrupt:
            stop_event.set()
            return 0
    finally:
        if admin_code_jobs is not None:
            admin_code_jobs.cancel()
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
    stop_event = threading.Event()
    admin_code_jobs = _ConsoleAdminCodeJobs()
    stop_event._admin_code_jobs = admin_code_jobs
    restart_event = threading.Event()
    print("Beenden mit „stop“, Neustart mit „restart“ und Enter")
    _watch_console(stop_event, restart_event=restart_event)

    try:
        while True:
            try:
                process = _start_child(command, cwd=root_dir)
            except OSError as error:
                raise BuildError(f"Server konnte nicht gestartet werden: {error}") from error
            try:
                exit_code = _wait_child(process, stop_event, restart_event=restart_event)
            except KeyboardInterrupt:
                stop_event.set()
                _finish_server(process, Path(command[0]).name)
                return 0
            if stop_event.is_set():
                return exit_code
            if restart_event.is_set() or exit_code == restart_exit_code:
                restart_event.clear()
                print("Server wird neu gestartet …")
                continue
            return exit_code
    finally:
        admin_code_jobs.cancel()


def _run_single_database_command(command_runner: Callable[[], int]) -> int:
    """Run a one-shot command, owning database cleanup only when needed."""
    database_started = False
    try:
        try:
            config = load_server_config()
        except ConfigError as error:
            raise BuildError(str(error)) from error
        try:
            database_started = start_database(config)
        except _DatabaseStartInterrupted:
            database_started = True
            raise
        if database_started:
            _run_database_command(venv_python(), "upgrade")
        return command_runner()
    finally:
        if database_started:
            stop_database()


# MAIN
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Startet die Marathon-Datenbank und den Server oder die Live-Vorschau."
    )
    parser.add_argument("--dev", action="store_true", help="Live-Vorschau der Oberfläche starten")
    parser.add_argument("--trust", action="store_true", help="Stammzertifikat des mitgelieferten Proxys für diesen Benutzer importieren")
    parser.add_argument("command", nargs="?", choices=["mail-test", "admin-code"])
    parser.add_argument("address", nargs="?")
    args = parser.parse_args(argv)
    if args.command in ("mail-test", "admin-code"):
        if args.command == "mail-test":
            if not args.address:
                parser.error("mail-test braucht eine E-Mail-Adresse")
            command_runner = lambda: _mail_test(args.address)
        else:
            if args.address is not None:
                parser.error("admin-code erwartet keine Adresse")
            command_runner = _admin_code
        try:
            return _run_single_database_command(command_runner)
        except KeyboardInterrupt:
            return 0
        except BuildError as error:
            print(f"Fehler: {error}", file=sys.stderr)
            return 1
    if args.address is not None:
        parser.error("unrecognized arguments: " + args.address)
    if args.trust:
        return trust_certificate()

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
