import argparse
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

from .client import ApiClient
from .context import ModuleContext


class TaskContext(ModuleContext):
    def __init__(self, client, task_id, storages, default_storage=None, heartbeat_interval=10.0):
        super().__init__(storages, default_storage)
        self.client = client
        self.task_id = task_id
        self.heartbeat_interval = heartbeat_interval
        self._progress = 0
        self._logs = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.heartbeat_error = None

    def progress(self, pct):
        try:
            value = max(0, min(100, int(pct)))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("Progress must be a number from 0 to 100") from None
        with self._lock:
            self._progress = value

    def log(self, line):
        with self._lock:
            self._logs.append(str(line))

    def flush(self):
        with self._lock:
            payload = {"progress": self._progress, "log": self._logs}
            self._logs = []
        status, _ = self.client.request(
            "POST", f"/tasks/{quote(str(self.task_id), safe='')}/heartbeat", payload
        )
        if not 200 <= status < 300:
            raise RuntimeError(f"Task heartbeat returned HTTP {status}")

    def _heartbeat_loop(self):
        while not self._stop.wait(self.heartbeat_interval):
            try:
                self.flush()
            except Exception as error:
                self.heartbeat_error = error

    def start(self):
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self.flush()


def load_config(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as source:
        config = json.load(source)
    if not isinstance(config, dict) or not isinstance(config.get("storages", {}), dict):
        raise ValueError("Agent config must contain a storages object")
    return config


class Agent:
    def __init__(
        self,
        server: str,
        name: str,
        capabilities: list[str],
        config: dict | None = None,
        client=None,
        modules_dir: str | Path | None = None,
        poll_interval: float = 1.0,
        heartbeat_interval: float = 10.0,
    ):
        self.name = name
        self.capabilities = capabilities
        self.config = config or {}
        self.client = client or ApiClient(server)
        self.modules_dir = Path(modules_dir) if modules_dir else Path(__file__).resolve().parents[1] / "modules"
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.worker_id = None

    def _manifests(self):
        manifests = []
        for folder in sorted(self.modules_dir.iterdir()):
            manifest_path = folder / "manifest.json"
            module_path = folder / "module.py"
            if not folder.is_dir() or not manifest_path.is_file():
                continue
            with manifest_path.open(encoding="utf-8") as source:
                manifest = json.load(source)
            if manifest.get("kind") == "source":
                continue
            if module_path.is_file():
                manifests.append(manifest)
        return manifests

    def register(self):
        payload = {
            "name": self.name,
            "capabilities": self.capabilities,
            "tags": self.config.get("tags", []),
            "modules": [manifest["name"] for manifest in self._manifests()],
        }
        status, data = self.client.request("POST", "/agents/register", payload)
        if not 200 <= status < 300 or not isinstance(data, dict) or not data.get("worker_id"):
            raise RuntimeError(f"Agent registration failed (HTTP {status})")
        self.worker_id = data["worker_id"]
        return self.worker_id

    def _check_response(self, status, action):
        if not 200 <= status < 300:
            raise RuntimeError(f"{action} returned HTTP {status}")

    def _load_module(self, name, version):
        folder = self.modules_dir / name
        manifest_path = folder / "manifest.json"
        module_path = folder / "module.py"
        if not manifest_path.is_file() or not module_path.is_file():
            raise ValueError(f"Agent module is unavailable: {name}")
        with manifest_path.open(encoding="utf-8") as source:
            manifest = json.load(source)
        if manifest.get("kind") == "source":
            raise ValueError(f"Source module cannot run on an agent: {name}")
        if version and manifest.get("version") != version:
            raise ValueError(f"Module version mismatch for {name}")
        spec = importlib.util.spec_from_file_location(f"m2_agent_module_{name}", module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load agent module: {name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not callable(getattr(module, "run", None)):
            raise TypeError(f"Module {name} must define run(ctx, clips, params)")
        return module

    @staticmethod
    def _error_results(clips, error):
        message = f"{type(error).__name__}: {error}"
        return [
            {
                "clip_id": clip["id"],
                "status": "error",
                "output_port": "error",
                "message": message,
                "metadata": {},
                "files_add": [],
            }
            for clip in clips
        ]

    def run_task(self, task):
        task_id = task["task_id"]
        clips = task.get("clips", [])
        ctx = TaskContext(
            self.client,
            task_id,
            self.config.get("storages", {}),
            self.config.get("default_storage"),
            self.heartbeat_interval,
        )
        error = None
        ctx.start()
        try:
            module = self._load_module(task["module"], task.get("module_version"))
            results = module.run(ctx, clips, task.get("params", {}))
            if not isinstance(results, list) or any(not isinstance(result, dict) for result in results):
                raise TypeError("Module run() must return a list of result objects")
        except Exception as exc:
            error = exc
            results = self._error_results(clips, exc)
        try:
            ctx.stop()
        except Exception as exc:
            if error is None:
                error = exc
                results = self._error_results(clips, exc)
        if ctx.heartbeat_error is not None and error is None:
            results = self._error_results(clips, ctx.heartbeat_error)
        status, _ = self.client.request(
            "POST", f"/tasks/{quote(str(task_id), safe='')}/result", {"results": results}
        )
        self._check_response(status, "Task result")
        return results

    def run_once(self):
        if self.worker_id is None:
            self.register()
        worker = quote(str(self.worker_id), safe="")
        status, _ = self.client.request("POST", f"/agents/{worker}/heartbeat")
        self._check_response(status, "Agent heartbeat")
        status, task = self.client.request("POST", f"/agents/{worker}/lease")
        if status == 204 or task is None:
            if 200 <= status < 300:
                return False
        self._check_response(status, "Task lease")
        if not isinstance(task, dict) or not task.get("task_id"):
            raise ValueError("Task lease response is invalid")
        self.run_task(task)
        return True

    def run_forever(self):
        while True:
            try:
                self.run_once()
            except Exception as error:
                print(f"Agent error: {error}", file=sys.stderr)
            time.sleep(self.poll_interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run an m2 worker agent")
    parser.add_argument("--server", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--capabilities", default="")
    parser.add_argument("--config", required=True)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    capabilities = [value.strip() for value in args.capabilities.split(",") if value.strip()]
    Agent(args.server, args.name, capabilities, config, poll_interval=args.poll_interval).run_forever()


if __name__ == "__main__":
    main()
