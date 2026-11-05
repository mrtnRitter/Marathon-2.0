"""Worker registration, job execution, and reporting."""

import argparse
import concurrent.futures
import json
import os
import socket
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from marathon import __version__

UPDATE_EXIT_CODE = 75
CAPABILITIES = ("warten", "wait")


def load_config(path):
    with Path(path).open("rb") as file:
        data = tomllib.load(file)
    server_url = data.get("server_url", "").strip().rstrip("/")
    if not server_url:
        raise ValueError("client.toml must define server_url")
    return {
        "server_url": server_url,
        "name": data.get("name", socket.gethostname()),
        "max_parallel": max(1, int(data.get("max_parallel", 1))),
        "poll_interval": max(0.05, float(data.get("poll_interval", 1))),
    }


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


class WorkerRuntime:
    def __init__(self, config, state_path, update_path=None):
        self.config = config
        self.state_path = Path(state_path)
        self.update_path = Path(update_path or self.state_path.with_suffix(".update.json"))
        self.worker_id = _read_json(self.state_path).get("worker_id")
        self.pending_update = None

    def _url(self, endpoint):
        base = self.config["server_url"].rstrip("/") + "/api/"
        return urllib.parse.urljoin(base, endpoint.lstrip("/"))

    def _request(self, method, endpoint, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._url(endpoint),
            data=body,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _register(self):
        result = self._request(
            "POST",
            "worker/register",
            {
                "name": self.config["name"],
                "capabilities": list(CAPABILITIES),
                "max_parallel": self.config["max_parallel"],
                "client_version": __version__,
            },
        )
        self.worker_id = result["id"]
        state = _read_json(self.state_path)
        state["worker_id"] = self.worker_id
        _write_json(self.state_path, state)
        self.pending_update = result.get("update")

    def _heartbeat(self):
        if not self.worker_id:
            self._register()
        result = self._request(
            "POST",
            f"worker/{self.worker_id}/heartbeat",
            {"capabilities": list(CAPABILITIES), "client_version": __version__},
        )
        self.pending_update = result.get("update") or self.pending_update

    def _log(self, job_id, message):
        self._request(
            "POST", f"worker/{self.worker_id}/jobs/{job_id}/log", {"message": message}
        )

    def _execute(self, job):
        job_id = job["id"]
        module_type = job.get("module_type")
        settings = job.get("settings") or {}
        log_lines = []
        try:
            if module_type not in ("wait", "warten"):
                raise ValueError(f"Unsupported module type: {module_type}")
            seconds = max(0.0, float(settings.get("seconds", 1)))
            fail = bool(settings.get("fail", False))
            started = time.monotonic()
            log_lines.append(f"Started {module_type}")
            self._log(job_id, log_lines[-1])
            last_progress = -1
            while time.monotonic() - started < seconds:
                elapsed = time.monotonic() - started
                remaining = seconds - elapsed
                progress = min(99, int(elapsed * 100 / seconds)) if seconds else 100
                if progress > last_progress:
                    line = f"Progress: {progress}%"
                    log_lines.append(line)
                    self._log(job_id, line)
                    last_progress = progress
                time.sleep(min(0.25, remaining))
            log_lines.append("Completed wait" if not fail else "Configured failure")
            self._log(job_id, log_lines[-1])
            result = {
                "status": "failed" if fail else "success",
                "output": {"completed": not fail},
                "message": "Configured failure" if fail else None,
                "log": "\n".join(log_lines),
            }
        except Exception as error:
            log_lines.append(str(error))
            self._log(job_id, log_lines[-1])
            result = {
                "status": "failed",
                "message": str(error),
                "log": "\n".join(log_lines),
            }
        self._request(
            "POST", f"worker/{self.worker_id}/jobs/{job_id}/result", result
        )

    def run(self, stop_event=None):
        stop_event = stop_event or threading.Event()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.config["max_parallel"]
        ) as executor:
            active = set()
            while not stop_event.is_set():
                done = {future for future in active if future.done()}
                for future in done:
                    future.result()
                active.difference_update(done)
                self._heartbeat()
                if self.pending_update and not active:
                    _write_json(self.update_path, self.pending_update)
                    return UPDATE_EXIT_CODE
                if not self.pending_update:
                    available = self.config["max_parallel"] - len(active)
                    if available > 0:
                        response = self._request(
                            "POST", f"worker/{self.worker_id}/jobs/claim", {}
                        )
                        for job in response.get("items", [])[:available]:
                            active.add(executor.submit(self._execute, job))
                stop_event.wait(self.config["poll_interval"])
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("client.toml"))
    parser.add_argument("--state", type=Path, default=Path(".client-state.json"))
    parser.add_argument("--update", type=Path)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        return WorkerRuntime(config, args.state, args.update).run()
    except (OSError, ValueError, urllib.error.URLError, KeyError) as error:
        print(f"Worker runtime failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
