# IMPORTS
import logging
import os
import platform
import socket
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from marathon.client.api import (
    ApiError,
    ClientApi,
    client_ssl_context,
    enroll,
    request_control_link,
    tool_fetchers,
)
from marathon.client.availability import AvailabilityEvaluator, Sample
from marathon.client.local_state import (
    Bootstrap,
    LocalStateError,
    WorkerCredentials,
    client_dir,
    delete_credentials,
    load_credentials,
    save_credentials,
)
from marathon.client.sensors import SystemSensors
from marathon.client.tools import ToolStore
from marathon.client.tray import TrayController
from marathon.client.update import UpdateManager, block_reason
from marathon.worker.runtime import WorkerRuntime, worker_capabilities


# CONFIG
main_log = logging.getLogger(__name__)
_mutex_name = r"Local\MarathonWorkerClient"
_error_already_exists = 183
_sync_access = 0x00100000
_registration_backoff_s = (1, 2, 5, 10, 30, 60)
_notice_timeout_s = 30.0


# FUNC
def _platform_name() -> str:
    return f"{platform.system()}-{platform.machine()}"


def _default_wait(stop: threading.Event, seconds: float) -> bool:
    return stop.wait(seconds)


@dataclass
class ClientDeps:
    """Inject client components and environment functions for controlled execution."""

    api_factory: Callable[..., ClientApi] = ClientApi
    enroll_fn: Callable[[ClientApi, str, str, str], tuple[str, str, bool]] = enroll
    ssl_context_factory: Callable[[str], Any] = client_ssl_context
    control_link_fn: Callable[[ClientApi], str] = request_control_link
    runtime_factory: Callable[..., WorkerRuntime] = WorkerRuntime
    tray_factory: Callable[..., TrayController] = TrayController
    sensor_factory: Callable[[], SystemSensors] = SystemSensors
    tool_store_factory: Callable[..., ToolStore] = ToolStore
    update_manager_factory: Callable[..., UpdateManager] = UpdateManager
    open_browser: Callable[[str], Any] = webbrowser.open
    hostname: Callable[[], str] = socket.gethostname
    platform_name: Callable[[], str] = _platform_name
    now: Callable[[], datetime] = datetime.now
    monotonic: Callable[[], float] = time.monotonic
    wait: Callable[[threading.Event, float], bool] = _default_wait


class SingleInstance:
    """Hold a per-user named mutex or a non-blocking file lock."""

    def __init__(self, base: Path) -> None:
        self.base = Path(base)
        self._handle: Any | None = None
        self._lock_file: Any | None = None
        self._acquired = False

    def acquire(self) -> bool:
        """Acquire the client lock, returning false when another copy owns it."""
        if self._acquired:
            return True
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            ctypes.set_last_error(0)
            handle = kernel32.CreateMutexW(None, False, _mutex_name)
            if not handle:
                raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
            if ctypes.get_last_error() == _error_already_exists:
                kernel32.CloseHandle(handle)
                return False
            self._handle = handle
            self._acquired = True
            return True

        import fcntl

        self.base.mkdir(parents=True, exist_ok=True)
        lock_file = (self.base / "client.lock").open("a+b")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return False
        except BaseException:
            lock_file.close()
            raise
        self._lock_file = lock_file
        self._acquired = True
        return True

    def release(self) -> None:
        """Release the acquired lock; repeated calls are safe."""
        if not self._acquired:
            return
        if os.name == "nt":
            import ctypes

            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._handle)
            self._handle = None
        else:
            import fcntl

            if self._lock_file is not None:
                try:
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                finally:
                    self._lock_file.close()
                    self._lock_file = None
        self._acquired = False


def is_client_running() -> bool:
    """Return whether another client currently holds the per-user instance lock."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.OpenMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenMutexW(_sync_access, False, _mutex_name)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True

    import fcntl

    path = client_dir() / "client.lock"
    try:
        lock_file = path.open("a+b")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        lock_file.close()


def run_client(base: Path, bootstrap: Bootstrap, deps: ClientDeps | None = None) -> int:
    """Run the tray client, registration flow, worker runtime, and bounded shutdown."""
    dependencies = deps if deps is not None else ClientDeps()
    base = Path(base)
    instance = SingleInstance(base)
    try:
        if not instance.acquire():
            return 0
    except OSError:
        main_log.exception("Client-Einzelinstanz konnte nicht gestartet werden")
        return 1

    worker_stop = threading.Event()
    shutdown_event = threading.Event()
    exit_event = threading.Event()
    exit_lock = threading.Lock()
    state_lock = threading.RLock()
    thread_lock = threading.Lock()
    notice_lock = threading.RLock()
    threads: list[threading.Thread] = []
    exit_code = 0
    tray: TrayController | None = None
    credentials: WorkerCredentials | None = None
    api: ClientApi | None = None
    latest_status: dict[str, Any] = {"status": None}
    notice_message: str | None = None
    notice_expires: float | None = None
    notice_generation = 0
    notice_timer: threading.Timer | None = None
    evaluator = AvailabilityEvaluator()
    availability_state = (False, "Verfügbarkeit wird geprüft")
    runtime_holder: list[WorkerRuntime | None] = [None]
    update_holder: list[UpdateManager | None] = [None]
    healthy_confirmed = False

    def _safe_set_status(state: dict[str, Any]) -> None:
        if tray is None:
            return
        try:
            tray.set_status(state)
        except Exception:
            main_log.exception("Tray-Status konnte nicht aktualisiert werden")

    def _expire_notice(generation: int) -> None:
        nonlocal notice_message, notice_expires, notice_timer
        with notice_lock:
            if shutdown_event.is_set():
                notice_timer = None
                return
            if generation != notice_generation or notice_message is None or notice_expires is None:
                return
            remaining = notice_expires - dependencies.monotonic()
            if remaining > 0:
                notice_timer = threading.Timer(remaining, _expire_notice, args=(generation,))
                notice_timer.daemon = True
                notice_timer.start()
                return
            notice_message = None
            notice_expires = None
            notice_timer = None
            current = dict(latest_status)
        _safe_set_status(current)

    def _publish_status(state: dict[str, Any] | None = None) -> None:
        nonlocal latest_status, notice_message, notice_expires, notice_timer
        with notice_lock:
            if state is not None:
                latest_status = dict(state)
                latest_status.pop("message", None)
            if notice_message is not None and notice_expires is not None:
                if dependencies.monotonic() >= notice_expires:
                    notice_message = None
                    notice_expires = None
                    if notice_timer is not None:
                        notice_timer.cancel()
                    notice_timer = None
            current = dict(latest_status)
            if notice_message:
                current["message"] = notice_message
        _safe_set_status(current)

    def _set_notice(message: str, *, permanent: bool = False) -> None:
        nonlocal notice_message, notice_expires, notice_generation, notice_timer
        with notice_lock:
            if shutdown_event.is_set():
                return
            if notice_timer is not None:
                notice_timer.cancel()
                notice_timer = None
            notice_generation += 1
            generation = notice_generation
            notice_message = message
            notice_expires = None if permanent else dependencies.monotonic() + _notice_timeout_s
            if not permanent:
                notice_timer = threading.Timer(
                    _notice_timeout_s,
                    _expire_notice,
                    args=(generation,),
                )
                notice_timer.daemon = True
                notice_timer.start()
        _publish_status()

    def _launch_thread(target: Callable[[], None], name: str) -> threading.Thread | None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        with thread_lock:
            if worker_stop.is_set():
                return None
            threads.append(thread)
            try:
                thread.start()
            except RuntimeError:
                threads.remove(thread)
                main_log.exception("Client-Hintergrundaufgabe konnte nicht gestartet werden: %s", name)
                return None
        return thread

    def _request_exit(code: int) -> None:
        nonlocal exit_code
        with exit_lock:
            if not exit_event.is_set():
                exit_code = code
                exit_event.set()
        worker_stop.set()
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                main_log.exception("Tray konnte nicht beendet werden")

    def _open_settings() -> None:
        with state_lock:
            current_credentials = credentials
            current_api = api
        if current_credentials is None or current_api is None:
            _set_notice("Noch nicht beim Server angemeldet")
            return
        try:
            url = dependencies.control_link_fn(current_api)
        except ApiError as error:
            _set_notice(f"Worker-Einstellungen konnten nicht geöffnet werden: {error.detail}")
            return
        except Exception:
            main_log.exception("Worker-Einstellungen konnten nicht angefordert werden")
            return
        try:
            dependencies.open_browser(url)
        except Exception:
            main_log.exception("Worker-Einstellungen konnten nicht im Browser geöffnet werden")

    def _on_status(state: dict[str, Any]) -> None:
        nonlocal healthy_confirmed
        _publish_status(state)
        if state.get("connected") and state.get("status") is not None and not healthy_confirmed:
            with state_lock:
                if healthy_confirmed:
                    return
                healthy_confirmed = True
                current_update = update_holder[0]
            if current_update is not None:
                try:
                    current_update.confirm_healthy()
                except Exception:
                    main_log.exception("Gesunder Update-Start konnte nicht bestätigt werden")

    def _on_availability_config(config: dict[str, Any], _revision: int) -> None:
        try:
            with evaluator_lock:
                evaluator.set_config(config)
        except Exception:
            main_log.exception("Server-Verfügbarkeitsregeln konnten nicht übernommen werden")

    evaluator_lock = threading.RLock()

    def _availability_loop(sensors: SystemSensors) -> None:
        nonlocal availability_state
        while not worker_stop.is_set():
            try:
                sample: Sample = sensors.sample(dependencies.now())
                with evaluator_lock:
                    result = evaluator.evaluate(sample, dependencies.monotonic())
                state = (bool(result.available), str(result.reason))
            except Exception:
                main_log.exception("Lokale Verfügbarkeit konnte nicht ermittelt werden")
                state = (False, "Messwerte nicht lesbar")
            with state_lock:
                availability_state = state
            if dependencies.wait(worker_stop, 5):
                return

    def _get_runtime_idle() -> bool:
        with state_lock:
            current_runtime = runtime_holder[0]
        return current_runtime is None or current_runtime.running_jobs() == 0

    def _get_availability() -> tuple[bool, str]:
        with state_lock:
            current_update = update_holder[0]
            current_state = availability_state
        if current_update is not None:
            try:
                if current_update.blocks_jobs():
                    return False, block_reason
            except Exception:
                main_log.exception("Update-Status konnte nicht gelesen werden")
                return False, block_reason
        return current_state

    def _tool_sync_loop(tools: ToolStore) -> None:
        while not worker_stop.is_set():
            try:
                result = tools.sync(worker_stop)
                main_log.info("Zusatzprogramme abgeglichen: aktualisiert=%s fehlgeschlagen=%s", result.updated, result.failed)
            except Exception:
                if not worker_stop.is_set():
                    main_log.exception("Zusatzprogramme konnten nicht abgeglichen werden")
            if dependencies.wait(worker_stop, 600):
                return

    def _register() -> tuple[WorkerCredentials, Any] | None:
        try:
            saved = load_credentials(base)
        except LocalStateError:
            main_log.exception("Gespeicherte Worker-Anmeldung ist ungültig; erneute Anmeldung wird versucht")
            saved = None
        ssl_context = dependencies.ssl_context_factory(bootstrap.root_ca_pem)
        if saved is not None and saved.server_url == bootstrap.server_url:
            return saved, ssl_context

        enrollment_api = dependencies.api_factory(bootstrap.server_url, ssl_context)
        backoff_index = 0
        while not worker_stop.is_set():
            try:
                worker_id, worker_token, _approved = dependencies.enroll_fn(
                    enrollment_api,
                    dependencies.hostname(),
                    dependencies.platform_name(),
                    bootstrap.client_version,
                )
                if worker_stop.is_set():
                    return None
                enrolled = WorkerCredentials(bootstrap.server_url, worker_id, worker_token)
                save_credentials(base, enrolled)
                return enrolled, ssl_context
            except ApiError as error:
                if error.status is not None and 400 <= error.status < 500:
                    _set_notice(f"Anmeldung beim Server fehlgeschlagen: {error.detail}")
                    delay = _registration_backoff_s[-1]
                    backoff_index = len(_registration_backoff_s) - 1
                else:
                    delay = _registration_backoff_s[backoff_index]
                    backoff_index = min(backoff_index + 1, len(_registration_backoff_s) - 1)
                main_log.warning("Worker-Anmeldung fehlgeschlagen: %s", error.detail)
            except Exception:
                delay = _registration_backoff_s[backoff_index]
                backoff_index = min(backoff_index + 1, len(_registration_backoff_s) - 1)
                main_log.exception("Worker-Anmeldung fehlgeschlagen")
            if dependencies.wait(worker_stop, delay):
                return None
        return None

    def _start_worker() -> None:
        nonlocal credentials, api
        try:
            registered = _register()
            if registered is None or worker_stop.is_set():
                return
            registered_credentials, ssl_context = registered
            active_api = dependencies.api_factory(
                bootstrap.server_url, ssl_context, registered_credentials.worker_token
            )
            with state_lock:
                credentials = registered_credentials
                api = active_api
                active_credentials = credentials
            tools = dependencies.tool_store_factory(base, *tool_fetchers(active_api))
            current_update = dependencies.update_manager_factory(
                base,
                bootstrap.client_version,
                download=lambda url, target, stop: active_api.download(url, target, stop),
                is_idle=_get_runtime_idle,
                request_restart=lambda: _request_exit(10),
            )
            with state_lock:
                update_holder[0] = current_update
            if worker_stop.is_set():
                return
            sensors = dependencies.sensor_factory()
            current_runtime = dependencies.runtime_factory(
                bootstrap.server_url,
                active_credentials.worker_token,
                active_credentials.worker_id,
                name=dependencies.hostname(),
                capabilities=worker_capabilities(),
                ssl_context=ssl_context,
                availability=_get_availability,
                on_status=_on_status,
                on_availability_config=_on_availability_config,
                on_update_offer=current_update.offer,
                tool_path=tools.tool_path,
            )
            with state_lock:
                runtime_holder[0] = current_runtime
            if worker_stop.is_set():
                return

            _launch_thread(lambda: _availability_loop(sensors), "marathon-client-availability")
            _launch_thread(lambda: _tool_sync_loop(tools), "marathon-client-tools")

            def _run_runtime() -> None:
                nonlocal credentials, api
                try:
                    result = current_runtime.run(worker_stop)
                except Exception:
                    main_log.exception("Worker-Laufzeit wurde unerwartet beendet")
                    return
                if result != 3:
                    return
                worker_stop.set()
                try:
                    delete_credentials(base)
                except OSError:
                    main_log.exception("Worker-Anmeldung konnte nach Token-Ablehnung nicht gelöscht werden")
                with state_lock:
                    credentials = None
                    api = None
                current_update = update_holder[0]
                if current_update is not None:
                    try:
                        current_update.stop(timeout_s=2.0)
                    except Exception:
                        main_log.exception("Update-Arbeit konnte nach Token-Ablehnung nicht beendet werden")
                _set_notice(
                    "Worker wurde entfernt. Beim nächsten Start meldet sich dieser Rechner neu an.",
                    permanent=True,
                )

            _launch_thread(_run_runtime, "marathon-client-runtime")
        except Exception:
            main_log.exception("Client konnte nicht gestartet werden")

    def _shutdown() -> None:
        with thread_lock:
            shutdown_event.set()
            worker_stop.set()
            current_threads = list(threads)
        current_update = update_holder[0]
        if current_update is not None:
            try:
                current_update.stop(timeout_s=2.0)
            except Exception:
                main_log.exception("Update-Arbeit konnte nicht beendet werden")
        runtime_threads = [thread for thread in current_threads if thread.name == "marathon-client-runtime"]
        other_threads = [thread for thread in current_threads if thread not in runtime_threads]
        for thread in runtime_threads:
            if thread is not threading.current_thread():
                thread.join(15.0)
        for thread in other_threads:
            if thread is not threading.current_thread():
                thread.join(2.0)
        with notice_lock:
            if notice_timer is not None:
                notice_timer.cancel()
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                main_log.exception("Tray konnte nicht beendet werden")
        instance.release()

    try:
        tray = dependencies.tray_factory(_open_settings, lambda: _request_exit(0))
        tray.set_status({"status": None})
        _launch_thread(_start_worker, "marathon-client-main")
        tray.run()
    except Exception:
        main_log.exception("Client-Tray wurde unerwartet beendet")
        _request_exit(0)
    finally:
        _shutdown()

    return exit_code
