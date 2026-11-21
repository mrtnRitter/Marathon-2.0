# IMPORTS
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import http.client
import json
import logging
import ssl
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from marathon.common.version import app_version
from marathon.modules import catalog_module, load_catalog
from marathon.modules._job import JobStopped, ModuleJob, ModuleResult, SpawnedAsset


# CONFIG
api_prefix = "/api/v1"
logger = logging.getLogger("marathon.worker")


# FUNC
@dataclass
class _RunningJob:
    job_id: str
    stop: threading.Event = field(default_factory=threading.Event)
    threads: list[threading.Thread] = field(default_factory=list)
    remaining_assets: int = 0


def worker_capabilities() -> list[str]:
    """Return catalog IDs explicitly marked for worker execution."""
    return [spec["id"] for spec in load_catalog() if spec.get("execution") == "worker"]


class WorkerRuntime:
    """Register with a Marathon server, execute offered jobs, and report results."""

    def __init__(
        self,
        server_url: str,
        token: str,
        worker_id: str,
        name: str,
        capabilities: Sequence[str],
        resolve_module: Callable[[str], Any | None] = catalog_module,
        request_timeout_s: float = 10.0,
        retry_delays_s: Sequence[float] = (1, 2, 5, 10),
        result_retry_s: float = 2.0,
        result_deadline_s: float = 120.0,
        shutdown_timeout_s: float = 10.0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if request_timeout_s <= 0 or result_retry_s < 0 or result_deadline_s < 0 or shutdown_timeout_s < 0:
            raise ValueError("Zeitlimits müssen gültig und nicht negativ sein")
        if any(delay < 0 for delay in retry_delays_s):
            raise ValueError("retry_delays_s darf keine negativen Werte enthalten")
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.worker_id = worker_id
        self.name = name
        self.capabilities = list(capabilities)
        self.resolve_module = resolve_module
        self.request_timeout_s = request_timeout_s
        self.retry_delays_s = tuple(retry_delays_s)
        self.result_retry_s = result_retry_s
        self.result_deadline_s = result_deadline_s
        self.shutdown_timeout_s = shutdown_timeout_s
        self.ssl_context = ssl_context
        self._jobs: dict[str, _RunningJob] = {}
        self._jobs_lock = threading.Lock()
        self._stop = threading.Event()

    def _post(self, path: str, payload: Mapping[str, Any], timeout_s: float) -> tuple[int | None, dict[str, Any]]:
        request = Request(
            f"{self.server_url}{api_prefix}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_s, context=self.ssl_context) as response:
                status = response.status
                body = response.read()
        except HTTPError as error:
            status = error.code
            body = error.read()
        except (URLError, http.client.HTTPException, TimeoutError, OSError, ValueError):
            return None, {}
        try:
            decoded = json.loads(body) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            decoded = {}
        return status, decoded if isinstance(decoded, dict) else {}

    def _post_interruptible(
        self,
        path: str,
        payload: Mapping[str, Any],
        timeout_s: float,
        stop: threading.Event,
    ) -> tuple[int | None, dict[str, Any]]:
        completed = threading.Event()
        result: list[tuple[int | None, dict[str, Any]]] = []

        def request() -> None:
            try:
                result.append(self._post(path, payload, timeout_s))
            finally:
                completed.set()

        threading.Thread(target=request, name="marathon-worker-request", daemon=True).start()
        while not completed.wait(0.05):
            if stop.is_set():
                return None, {}
        return result[0] if result else (None, {})

    def _retry_delay(self, attempt: int) -> float:
        if not self.retry_delays_s:
            return 1.0
        return self.retry_delays_s[min(attempt, len(self.retry_delays_s) - 1)]

    def _register(self, stop: threading.Event) -> tuple[int | None, dict[str, Any]]:
        payload = {
            "worker_id": self.worker_id,
            "name": self.name,
            "client_version": app_version,
            "capabilities": self.capabilities,
            "protocol": 2,
        }
        attempt = 0
        while not stop.is_set():
            status, response = self._post_interruptible("/worker/register", payload, self.request_timeout_s, stop)
            if status is not None and 200 <= status < 300:
                return status, response
            if status == 401:
                return status, response
            logger.warning("Worker-Anmeldung fehlgeschlagen; neuer Versuch")
            if stop.wait(self._retry_delay(attempt)):
                break
            attempt += 1
        return None, {}

    def _running_ids(self) -> list[str]:
        with self._jobs_lock:
            return list(self._jobs)

    def _poll(self, timeout_s: float, stop: threading.Event) -> tuple[int | None, dict[str, Any]]:
        completed = threading.Event()
        result: list[tuple[int | None, dict[str, Any]]] = []
        def request_loop() -> None:
            attempt = 0
            try:
                while not stop.is_set():
                    payload = {"worker_id": self.worker_id, "running": self._running_ids()}
                    status, response = self._post("/worker/poll", payload, timeout_s)
                    if status == 401 or (status == 404 and response.get("detail") == "Worker unbekannt"):
                        result.append((status, response))
                        return
                    if status is not None and 200 <= status < 300:
                        result.append((status, response))
                        return
                    if stop.wait(self._retry_delay(attempt)):
                        return
                    attempt += 1
            finally:
                completed.set()

        threading.Thread(target=request_loop, name="marathon-worker-poll", daemon=True).start()
        while not completed.wait(0.05):
            if stop.is_set():
                return None, {}
        return result[0] if result else (None, {})

    def _start_job(self, offered: Mapping[str, Any], stop: threading.Event) -> tuple[int | None, bool]:
        job_id = str(offered.get("job_id", ""))
        if not job_id:
            return None, False
        offered_assets = offered.get("assets")
        if not isinstance(offered_assets, list):
            return None, False
        assets = [
            asset for asset in offered_assets
            if isinstance(asset, Mapping) and asset.get("id") not in (None, "")
        ]
        if not assets:
            return None, False
        with self._jobs_lock:
            if job_id in self._jobs or self._jobs:
                return None, False
        status, _ = self._post_interruptible(
            f"/worker/jobs/{job_id}/start", {"worker_id": self.worker_id}, self.request_timeout_s, stop
        )
        if status != 200:
            return (3 if status == 401 else None), False
        state = _RunningJob(job_id, remaining_assets=len(assets))
        with self._jobs_lock:
            if self._jobs:
                return None, False
            self._jobs[job_id] = state
        type_id = str(offered.get("type_id", ""))
        settings = offered.get("settings") or {}
        state.threads = [
            threading.Thread(
                target=self._run_asset,
                args=(state, type_id, str(asset["id"]), str(asset.get("path", "")), settings),
                name=f"marathon-asset-{job_id}-{asset['id']}",
                daemon=True,
            )
            for asset in assets
        ]
        for thread in state.threads:
            thread.start()
        return None, True

    def _job_result(
        self, type_id: str, asset_path: str, settings: Mapping[str, Any], stop: threading.Event
    ) -> dict[str, Any]:
        base = {"worker_id": self.worker_id}
        try:
            module = self.resolve_module(type_id)
            if module is None:
                return {**base, "outcome": "error", "error": f"Modultyp unbekannt: {type_id}"}
            result = module.run(ModuleJob(asset_path, settings, stop))
        except JobStopped:
            return {**base, "outcome": "cancelled"}
        except Exception as error:
            return {**base, "outcome": "error", "error": str(error) or type(error).__name__}
        if not isinstance(result, ModuleResult):
            return {**base, "outcome": "error", "error": "Modul lieferte kein ModuleResult"}
        spawned = [
            {"path": asset.path, "output": asset.output, "log": list(asset.log)}
            for asset in result.spawned
        ]
        return {
            "worker_id": self.worker_id,
            "outcome": "result",
            "output": result.output,
            "log": list(result.log),
            "asset_path": result.asset_path,
            "spawned": spawned,
        }

    def _send_result(self, state: _RunningJob, asset_id: str, payload: Mapping[str, Any]) -> None:
        deadline = time.monotonic() + self.result_deadline_s
        shutdown_attempted = False
        while True:
            shutting_down = self._stop.is_set()
            if shutting_down and shutdown_attempted:
                return
            if shutting_down:
                shutdown_attempted = True
            status, _ = self._post(
                f"/worker/jobs/{state.job_id}/assets/{asset_id}/result", payload, self.request_timeout_s
            )
            if status is not None and 200 <= status < 300 or status in (403, 404):
                return
            if self._stop.is_set():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if self._stop.wait(min(self.result_retry_s, remaining)):
                continue

    def _run_asset(
        self,
        state: _RunningJob,
        type_id: str,
        asset_id: str,
        asset_path: str,
        settings: Mapping[str, Any],
    ) -> None:
        try:
            self._send_result(state, asset_id, self._job_result(type_id, asset_path, settings, state.stop))
        finally:
            with self._jobs_lock:
                state.remaining_assets -= 1
                if state.remaining_assets == 0 and self._jobs.get(state.job_id) is state:
                    # Keep the job running until results are sent; otherwise the server may mark it stale.
                    del self._jobs[state.job_id]

    def _handle_poll(self, response: Mapping[str, Any], stop: threading.Event) -> tuple[int | None, bool]:
        activity = False
        cancellations = response.get("cancel", [])
        for job_id in cancellations if isinstance(cancellations, list) else []:
            with self._jobs_lock:
                state = self._jobs.get(str(job_id))
                if state is not None and not state.stop.is_set():
                    state.stop.set()
                    activity = True
        jobs = response.get("jobs", [])
        for offered in jobs if isinstance(jobs, list) else []:
            if stop.is_set():
                break
            if isinstance(offered, Mapping):
                exit_code, started = self._start_job(offered, stop)
                activity = activity or started
                if exit_code == 3:
                    return exit_code, activity
        return None, activity

    def _shutdown(self, stop: threading.Event) -> None:
        stop.set()
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        threads: list[threading.Thread] = []
        for state in jobs:
            state.stop.set()
            threads.extend(state.threads)
        deadline = time.monotonic() + self.shutdown_timeout_s
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            thread.join(remaining)

    def run(self, stop: threading.Event) -> int:
        self._stop = stop
        if stop.is_set():
            return 0
        exit_code = 0
        try:
            status, registration = self._register(stop)
            if stop.is_set():
                return 0
            if status == 401:
                logger.error("Worker-Token ungültig")
                return 3
            if status is None:
                return 0
            poll_timeout_s = max(0.0, float(registration.get("poll_timeout_s", 0))) + 10.0
            while not stop.is_set():
                status, response = self._poll(poll_timeout_s, stop)
                if stop.is_set():
                    break
                if status == 401:
                    logger.error("Worker-Token ungültig")
                    exit_code = 3
                    break
                if status == 404 and response.get("detail") == "Worker unbekannt":
                    status, registration = self._register(stop)
                    if stop.is_set():
                        break
                    if status == 401:
                        logger.error("Worker-Token ungültig")
                        exit_code = 3
                        break
                    if status is not None:
                        poll_timeout_s = max(0.0, float(registration.get("poll_timeout_s", 0))) + 10.0
                    continue
                if status is not None and 200 <= status < 300:
                    poll_exit_code, activity = self._handle_poll(response, stop)
                    exit_code = poll_exit_code or exit_code
                    if exit_code == 3:
                        logger.error("Worker-Token ungültig")
                        break
                    if not activity and stop.wait(0.5):
                        break
        finally:
            self._shutdown(stop)
        return exit_code
