from __future__ import annotations

import csv
import io
import json
import mimetypes
import queue
import re
import ssl
import subprocess
import threading
import time
import uuid
import zipfile
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .. import __version__


class APIError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def module_types() -> list[dict[str, Any]]:
    return [
        {"type": "start", "execution": "server", "description": "Fester Workflow-Start; wird serverseitig ausgeführt und erzeugt keinen Job.", "settings": []},
        {"type": "wait", "execution": "worker", "description": "Wartet auf dem Worker und kann für Tests fehlschlagen.", "settings": [
            {"key": "seconds", "type": "number", "label": "Sekunden", "default": 1, "minimum": 0},
            {"key": "fail", "type": "boolean", "label": "Fehlschlagen", "default": False},
        ]},
    ]


def default_graph() -> dict[str, Any]:
    return {"nodes": [{"id": "start", "type": "start", "settings": {}}], "edges": []}


def normalize_graph(value: Any) -> dict[str, Any]:
    if value is None:
        return default_graph()
    if not isinstance(value, dict) or not isinstance(value.get("nodes", []), list) or not isinstance(value.get("edges", []), list):
        raise APIError(400, "graph must contain nodes and edges arrays")
    nodes = []
    seen: set[str] = set()
    for item in value.get("nodes", []):
        if not isinstance(item, dict):
            raise APIError(400, "each node must be an object")
        node_id = str(item.get("id", "")).strip()
        kind = item.get("type")
        if not node_id or node_id in seen or kind not in {"start", "wait"}:
            raise APIError(400, "node ids must be unique and types must be start or wait")
        settings = item.get("settings") or {}
        if not isinstance(settings, dict):
            raise APIError(400, "node settings must be an object")
        if kind == "start":
            settings = {}
        else:
            seconds = settings.get("seconds", 1)
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
                raise APIError(400, "wait seconds must be a non-negative number")
            if not isinstance(settings.get("fail", False), bool):
                raise APIError(400, "wait fail must be a boolean")
            settings = {"seconds": seconds, "fail": settings.get("fail", False)}
        nodes.append({"id": node_id, "type": kind, "settings": settings})
        seen.add(node_id)
    starts = [node for node in nodes if node["type"] == "start"]
    if not starts:
        if "start" in seen:
            raise APIError(400, "the start node id is reserved")
        nodes.insert(0, {"id": "start", "type": "start", "settings": {}})
        seen.add("start")
        starts = [nodes[0]]
    if len(starts) != 1 or starts[0]["id"] != "start":
        raise APIError(400, "a workflow must have one fixed start node with id start")
    edges = []
    adjacency = {node_id: [] for node_id in seen}
    edge_pairs: set[tuple[str, str]] = set()
    for edge in value.get("edges", []):
        if not isinstance(edge, dict):
            raise APIError(400, "each edge must be an object")
        source = str(edge.get("from", ""))
        target = str(edge.get("to", ""))
        if source not in seen or target not in seen or source == target or target == starts[0]["id"]:
            raise APIError(400, "edge endpoints must exist and cannot point to start")
        if (source, target) in edge_pairs:
            continue
        adjacency[source].append(target)
        edge_pairs.add((source, target))
        edges.append({"from": source, "to": target})
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise APIError(400, "workflow graph cannot contain cycles")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in adjacency[node_id]:
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in seen:
        visit(node_id)
    reachable = set()
    pending = [starts[0]["id"]]
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        pending.extend(adjacency[current])
    if reachable != seen:
        raise APIError(400, "every module must be connected to start")
    return {"nodes": nodes, "edges": edges}


class MemoryStore:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"workflows": {}, "workflow_versions": {}, "assets": {}, "jobs": {}, "history": [], "workers": {}, "client_versions": {}}

    def load(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def save(self, state: dict[str, Any]) -> None:
        self.state = deepcopy(state)


class PostgresStore:
    migrations = {
        1: [
            "CREATE TABLE workflows (id text PRIMARY KEY, name text NOT NULL, enabled boolean NOT NULL, version integer NOT NULL, graph jsonb NOT NULL, payload jsonb NOT NULL)",
            "CREATE TABLE workflow_versions (id text PRIMARY KEY, workflow_id text NOT NULL REFERENCES workflows(id), version integer NOT NULL, graph jsonb NOT NULL, payload jsonb NOT NULL, UNIQUE(workflow_id, version))",
            "CREATE TABLE assets (id text PRIMARY KEY, workflow_id text NOT NULL REFERENCES workflows(id), name text NOT NULL, status text NOT NULL, current_node text NOT NULL, payload jsonb NOT NULL)",
            "CREATE TABLE workers (id text PRIMARY KEY, approved boolean NOT NULL, last_seen timestamptz, client_version text NOT NULL, capabilities jsonb NOT NULL, payload jsonb NOT NULL)",
            "CREATE TABLE jobs (id text PRIMARY KEY, workflow_id text NOT NULL REFERENCES workflows(id), status text NOT NULL, module_type text NOT NULL, worker_id text REFERENCES workers(id), payload jsonb NOT NULL)",
            "CREATE TABLE job_assets (job_id text NOT NULL REFERENCES jobs(id), asset_id text NOT NULL REFERENCES assets(id), PRIMARY KEY(job_id, asset_id))",
            "CREATE TABLE history (id text PRIMARY KEY, asset_id text REFERENCES assets(id), job_id text REFERENCES jobs(id), event text NOT NULL, payload jsonb NOT NULL)",
            "CREATE TABLE client_versions (version text PRIMARY KEY, package_path text NOT NULL, payload jsonb NOT NULL)",
        ],
        2: [
            "CREATE INDEX assets_workflow_status_idx ON assets(workflow_id, status)",
            "CREATE INDEX jobs_status_type_idx ON jobs(status, module_type)",
            "CREATE INDEX history_asset_idx ON history(asset_id)",
        ],
    }

    def __init__(self, connect_args: dict[str, Any]):
        self.connect_args = connect_args
        import psycopg
        self.psycopg = psycopg
        self.jsonb = __import__("psycopg.types.json", fromlist=["Jsonb"]).Jsonb
        self.migrate()

    def connect(self):
        return self.psycopg.connect(**self.connect_args)

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for version, statements in self.migrations.items():
                if version in applied:
                    continue
                for statement in statements:
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (version,))

    def load(self) -> dict[str, Any]:
        state: dict[str, Any] = {"workflows": {}, "workflow_versions": {}, "assets": {}, "jobs": {}, "history": [], "workers": {}, "client_versions": {}}
        with self.connect() as conn:
            for key, table in (("workflows", "workflows"), ("workflow_versions", "workflow_versions"), ("assets", "assets"), ("jobs", "jobs"), ("workers", "workers"), ("client_versions", "client_versions")):
                for row in conn.execute(f"SELECT payload FROM {table}").fetchall():
                    item = row[0]
                    if key == "workflow_versions":
                        state[key][item["id"]] = item
                    elif key == "client_versions":
                        state[key][item["version"]] = item
                    else:
                        state[key][item["id"]] = item
            for row in conn.execute("SELECT job_id, asset_id FROM job_assets").fetchall():
                job = state["jobs"].get(row[0])
                if job is not None and row[1] not in job["asset_ids"]:
                    job["asset_ids"].append(row[1])
            state["history"] = [row[0] for row in conn.execute("SELECT payload FROM history ORDER BY (payload->>'created_at')").fetchall()]
        return state

    def save(self, state: dict[str, Any]) -> None:
        j = self.jsonb
        with self.connect() as conn:
            for item in state["workflows"].values():
                conn.execute("INSERT INTO workflows(id,name,enabled,version,graph,payload) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,enabled=EXCLUDED.enabled,version=EXCLUDED.version,graph=EXCLUDED.graph,payload=EXCLUDED.payload", (item["id"], item["name"], item["enabled"], item["version"], j(item["graph"]), j(item)))
            for item in state["workers"].values():
                conn.execute("INSERT INTO workers(id,approved,last_seen,client_version,capabilities,payload) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET approved=EXCLUDED.approved,last_seen=EXCLUDED.last_seen,client_version=EXCLUDED.client_version,capabilities=EXCLUDED.capabilities,payload=EXCLUDED.payload", (item["id"], item["approved"], item.get("last_seen"), item.get("client_version", ""), j(item["capabilities"]), j(item)))
            for item in state["assets"].values():
                conn.execute("INSERT INTO assets(id,workflow_id,name,status,current_node,payload) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,status=EXCLUDED.status,current_node=EXCLUDED.current_node,payload=EXCLUDED.payload", (item["id"], item["workflow_id"], item["name"], item["status"], item["current_node"], j(item)))
            for item in state["jobs"].values():
                conn.execute("INSERT INTO jobs(id,workflow_id,status,module_type,worker_id,payload) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET status=EXCLUDED.status,module_type=EXCLUDED.module_type,worker_id=EXCLUDED.worker_id,payload=EXCLUDED.payload", (item["id"], item["workflow_id"], item["status"], item["module_type"], item.get("worker_id"), j(item)))
                for asset_id in item["asset_ids"]:
                    conn.execute("INSERT INTO job_assets(job_id,asset_id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (item["id"], asset_id))
            for item in state["workflow_versions"].values():
                conn.execute("INSERT INTO workflow_versions(id,workflow_id,version,graph,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING", (item["id"], item["workflow_id"], item["version"], j(item["graph"]), j(item)))
            for item in state["history"]:
                conn.execute("INSERT INTO history(id,asset_id,job_id,event,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING", (item["id"], item.get("asset_id"), item.get("job_id"), item["event"], j(item)))
            for item in state["client_versions"].values():
                conn.execute("INSERT INTO client_versions(version,package_path,payload) VALUES(%s,%s,%s) ON CONFLICT(version) DO UPDATE SET package_path=EXCLUDED.package_path,payload=EXCLUDED.payload", (item["version"], item["package_path"], j(item)))


class EventHub:
    def __init__(self) -> None:
        self.listeners: set[queue.Queue[dict[str, Any]]] = set()
        self.lock = threading.Lock()

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        target: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100)
        with self.lock:
            self.listeners.add(target)
        return target

    def unsubscribe(self, target: queue.Queue[dict[str, Any]]) -> None:
        with self.lock:
            self.listeners.discard(target)

    def publish(self, event: str, data: dict[str, Any]) -> None:
        message = {"type": event, "data": data, "timestamp": now()}
        with self.lock:
            listeners = tuple(self.listeners)
        for target in listeners:
            try:
                target.put_nowait(message)
            except queue.Full:
                try:
                    target.get_nowait()
                    target.put_nowait(message)
                except queue.Empty:
                    pass


class Engine:
    def __init__(self, store: Any | None = None, heartbeat_timeout_seconds: int = 30, events: EventHub | None = None):
        self.store = store or MemoryStore()
        self.state = self.store.load()
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.events = events or EventHub()
        self.lock = threading.RLock()

    def _persist(self, event: str, data: dict[str, Any]) -> None:
        self.store.save(self.state)
        self.events.publish(event, data)

    def _history(self, event: str, message: str = "", asset_id: str | None = None, job_id: str | None = None, details: dict[str, Any] | None = None) -> dict[str, Any]:
        item = {"id": new_id(), "event": event, "message": message, "asset_id": asset_id, "job_id": job_id, "details": details or {}, "created_at": now()}
        self.state["history"].append(item)
        return item

    def _workflow(self, workflow_id: str) -> dict[str, Any]:
        item = self.state["workflows"].get(workflow_id)
        if item is None:
            raise APIError(404, "workflow not found")
        return item

    def _asset(self, asset_id: str) -> dict[str, Any]:
        item = self.state["assets"].get(asset_id)
        if item is None or item["status"] == "removed":
            raise APIError(404, "asset not found")
        return item

    def _worker(self, worker_id: str) -> dict[str, Any]:
        item = self.state["workers"].get(worker_id)
        if item is None:
            raise APIError(404, "worker not found")
        return item

    @staticmethod
    def _node_map(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {node["id"]: node for node in workflow["graph"]["nodes"]}

    @staticmethod
    def _predecessors(workflow: dict[str, Any]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for edge in workflow["graph"]["edges"]:
            result.setdefault(edge["to"], []).append(edge["from"])
        return result


    def _advance(self, asset: dict[str, Any]) -> None:
        if asset["status"] in {"failed", "completed", "removed", "paused"} or asset.get("paused"):
            return
        workflow = self.state["workflows"].get(asset["workflow_id"])
        if workflow is None:
            asset["status"] = "failed"
            self._history("asset.failed", "Workflow no longer exists", asset["id"])
            return
        if not workflow["enabled"]:
            return
        nodes = self._node_map(workflow)
        modules = [node for node in workflow["graph"]["nodes"] if node["type"] != "start"]
        module_ids = {node["id"] for node in modules}
        active = [job for job in self.state["jobs"].values() if asset["id"] in job["asset_ids"] and job["status"] in {"queued", "running"}]
        removed = [job for job in active if job["module_id"] not in nodes or nodes[job["module_id"]]["type"] != job["module_type"]]
        if removed:
            for job in active:
                job["status"] = "failed"
                job["finished_at"] = now()
            asset["status"] = "failed"
            self._history("asset.failed", "Current module was removed", asset["id"], removed[0]["id"])
            return
        completed = set(asset.setdefault("completed_modules", []))
        active_nodes = {job["module_id"] for job in active}
        predecessors = self._predecessors(workflow)
        for node in modules:
            node_id = node["id"]
            if node_id in completed or node_id in active_nodes:
                continue
            parents = predecessors.get(node_id, [])
            if not parents or not all(parent == "start" or parent in completed for parent in parents):
                continue
            job = {"id": new_id(), "workflow_id": workflow["id"], "asset_ids": [asset["id"]], "module_id": node_id, "module_type": node["type"], "settings": deepcopy(node["settings"]), "status": "queued", "worker_id": None, "attempt": 0, "output": None, "created_at": now(), "started_at": None, "finished_at": None, "logs": []}
            self.state["jobs"][job["id"]] = job
            active.append(job)
            active_nodes.add(node_id)
            self._history("job.queued", f"Job queued for {node['type']}", asset["id"], job["id"], {"module_id": node_id})
        if module_ids.issubset(completed):
            asset["status"] = "completed"
            asset["current_node"] = modules[-1]["id"] if modules else "start"
            self._history("asset.completed", "Workflow completed", asset["id"])
            return
        active = [job for job in self.state["jobs"].values() if asset["id"] in job["asset_ids"] and job["status"] in {"queued", "running"}]
        asset["status"] = "running" if any(job["status"] == "running" for job in active) else "queued"
        asset["current_node"] = active[0]["module_id"] if active else "start"

    def create_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        graph = normalize_graph(payload.get("graph"))
        item = {"id": new_id(), "name": str(payload.get("name", "Untitled workflow")).strip() or "Untitled workflow", "enabled": bool(payload.get("enabled", True)), "version": 1, "graph": graph, "created_at": now(), "updated_at": now()}
        with self.lock:
            self.state["workflows"][item["id"]] = item
            version = {"id": new_id(), "workflow_id": item["id"], "version": 1, "graph": deepcopy(graph), "created_at": now()}
            self.state["workflow_versions"][version["id"]] = version
            self._history("workflow.created", item["name"], details={"workflow_id": item["id"]})
            self._persist("workflow.updated", item)
            return deepcopy(item)

    def update_workflow(self, workflow_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        graph = normalize_graph(payload.get("graph")) if "graph" in payload else None
        with self.lock:
            item = self._workflow(workflow_id)
            if graph is not None:
                item["graph"] = graph
            if "name" in payload:
                item["name"] = str(payload["name"]).strip() or item["name"]
            if "enabled" in payload:
                item["enabled"] = bool(payload["enabled"])
            item["version"] += 1
            item["updated_at"] = now()
            version = {"id": new_id(), "workflow_id": item["id"], "version": item["version"], "graph": deepcopy(item["graph"]), "created_at": now()}
            self.state["workflow_versions"][version["id"]] = version
            current_ids = set(self._node_map(item))
            for asset in self.state["assets"].values():
                if asset["workflow_id"] != workflow_id or asset["status"] in {"removed", "completed", "failed"}:
                    continue
                active = [job for job in self.state["jobs"].values() if asset["id"] in job["asset_ids"] and job["status"] in {"queued", "running"}]
                removed = [job for job in active if job["module_id"] not in current_ids or self._node_map(item)[job["module_id"]]["type"] != job["module_type"]]
                if removed:
                    for job in active:
                        job["status"] = "failed"
                        job["finished_at"] = now()
                    asset["status"] = "failed"
                    self._history("asset.failed", "Current module was removed from workflow", asset["id"], removed[0]["id"])
                else:
                    current_nodes = self._node_map(item)
                    for job in active:
                        if job["status"] == "queued":
                            job["settings"] = deepcopy(current_nodes[job["module_id"]]["settings"])
                    self._advance(asset)
            self._history("workflow.updated", item["name"], details={"workflow_id": item["id"], "version": item["version"]})
            self._persist("workflow.updated", item)
            return deepcopy(item)

    def list_workflows(self) -> list[dict[str, Any]]:
        with self.lock:
            return [deepcopy(item) for item in self.state["workflows"].values()]

    def workflow_versions(self, workflow_id: str) -> list[dict[str, Any]]:
        self._workflow(workflow_id)
        return sorted((deepcopy(v) for v in self.state["workflow_versions"].values() if v["workflow_id"] == workflow_id), key=lambda v: v["version"])

    def set_workflow_enabled(self, workflow_id: str, enabled: bool) -> dict[str, Any]:
        return self.update_workflow(workflow_id, {"enabled": enabled})

    def create_asset(self, workflow_id: str, name: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            workflow = self._workflow(workflow_id)
            item = {"id": new_id(), "workflow_id": workflow_id, "name": name.strip() or "Unnamed asset", "metadata": metadata or {}, "status": "queued", "resume_status": None, "paused": False, "current_node": "start", "completed_modules": [], "created_at": now(), "updated_at": now()}
            self.state["assets"][item["id"]] = item
            self._history("asset.created", item["name"], item["id"])
            if workflow["enabled"]:
                self._advance(item)
            self._persist("asset.updated", item)
            return deepcopy(item)

    def import_assets(self, workflow_id: str, csv_text: str) -> list[dict[str, Any]]:
        try:
            reader = csv.DictReader(io.StringIO(csv_text))
            if not reader.fieldnames or "name" not in reader.fieldnames:
                raise APIError(400, "CSV must include a name column")
            rows = list(reader)
        except csv.Error as exc:
            raise APIError(400, f"invalid CSV: {exc}") from exc
        created = []
        for row in rows:
            metadata = {key: value for key, value in row.items() if key != "name" and key is not None}
            created.append(self.create_asset(workflow_id, row.get("name") or "", metadata))
        return created

    def list_assets(self, status: str | None = None, workflow_id: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            result = []
            for item in self.state["assets"].values():
                if item["status"] == "removed" or (status and item["status"] != status) or (workflow_id and item["workflow_id"] != workflow_id):
                    continue
                result.append(deepcopy(item))
            return sorted(result, key=lambda item: item["created_at"])

    def asset_detail(self, asset_id: str) -> dict[str, Any]:
        with self.lock:
            asset = deepcopy(self._asset(asset_id))
            jobs = [deepcopy(job) for job in self.state["jobs"].values() if asset_id in job["asset_ids"]]
            history = [deepcopy(item) for item in self.state["history"] if item.get("asset_id") == asset_id]
            return {**asset, "jobs": sorted(jobs, key=lambda item: item["created_at"]), "history": history}

    def asset_action(self, asset_id: str, action: str) -> dict[str, Any]:
        with self.lock:
            item = self._asset(asset_id)
            if action == "retry":
                for job in self.state["jobs"].values():
                    if asset_id in job["asset_ids"] and job["status"] in {"queued", "running"}:
                        job["status"] = "cancelled"
                        job["finished_at"] = now()
                item.update({"status": "queued", "resume_status": None, "paused": False, "current_node": "start", "completed_modules": [], "updated_at": now()})
                self._history("asset.reprocessed", item["name"], asset_id)
                self._advance(item)
            elif action == "pause":
                if not item["paused"]:
                    item["resume_status"] = item["status"]
                    item["status"] = "paused"
                    item["paused"] = True
                    self._history("asset.paused", item["name"], asset_id)
            elif action == "resume":
                if item["paused"]:
                    item["status"] = item.pop("resume_status", None) or "queued"
                    item["paused"] = False
                    item["updated_at"] = now()
                    self._history("asset.resumed", item["name"], asset_id)
                    self._advance(item)
            elif action == "remove":
                for job in self.state["jobs"].values():
                    if asset_id in job["asset_ids"] and job["status"] in {"queued", "running"}:
                        job["status"] = "cancelled"
                        job["finished_at"] = now()
                item["status"] = "removed"
                item["paused"] = True
                self._history("asset.removed", item["name"], asset_id)
            else:
                raise APIError(400, "action must be retry, pause, resume or remove")
            item["updated_at"] = now()
            self._persist("asset.updated", item)
            return deepcopy(item)

    def register_worker(self, payload: dict[str, Any]) -> dict[str, Any]:
        capabilities = payload.get("capabilities", [])
        if not isinstance(capabilities, list) or any(not isinstance(value, str) for value in capabilities):
            raise APIError(400, "capabilities must be a list of strings")
        parallel = payload.get("max_parallel", 1)
        if isinstance(parallel, bool) or not isinstance(parallel, int) or parallel < 1:
            raise APIError(400, "max_parallel must be a positive integer")
        with self.lock:
            item = {"id": new_id(), "name": str(payload.get("name", "Worker")).strip() or "Worker", "approved": False, "blocked": False, "capabilities": sorted(set(capabilities)), "max_parallel": parallel, "client_version": str(payload.get("client_version", "")), "desired_client_version": None, "last_seen": now(), "created_at": now()}
            item["reported_status"] = "new"
            self.state["workers"][item["id"]] = item
            self._history("worker.registered", item["name"], details={"worker_id": item["id"]})
            self._persist("worker.updated", self.worker_view(item))
            return {**self.worker_view(item), "update": self.worker_update(item)}

    def worker_update(self, worker: dict[str, Any]) -> dict[str, Any] | None:
        desired = worker.get("desired_client_version")
        if desired and desired != worker.get("client_version"):
            return {"version": desired, "download_url": f"/api/client-packages/{desired}"}
        return None

    def heartbeat(self, worker_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            worker = self._worker(worker_id)
            if "capabilities" in payload:
                if not isinstance(payload["capabilities"], list) or any(not isinstance(value, str) for value in payload["capabilities"]):
                    raise APIError(400, "capabilities must be a list of strings")
                worker["capabilities"] = sorted(set(payload["capabilities"]))
            if "client_version" in payload:
                worker["client_version"] = str(payload["client_version"])
            worker["last_seen"] = now()
            worker["reported_status"] = self.worker_status(worker)
            self._persist("worker.updated", self.worker_view(worker))
            return {**self.worker_view(worker), "update": self.worker_update(worker)}

    def worker_status(self, worker: dict[str, Any]) -> str:
        if worker.get("blocked"):
            return "blocked"
        if not worker.get("approved"):
            return "new"
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(worker["last_seen"])).total_seconds()
        except (ValueError, TypeError, KeyError):
            return "offline"
        return "offline" if age > self.heartbeat_timeout_seconds else "online"

    def worker_view(self, worker: dict[str, Any]) -> dict[str, Any]:
        return {**deepcopy(worker), "status": self.worker_status(worker), "active_jobs": sum(1 for job in self.state["jobs"].values() if job.get("worker_id") == worker["id"] and job["status"] == "running")}

    def list_workers(self) -> list[dict[str, Any]]:
        self.expire_workers()
        with self.lock:
            return [self.worker_view(worker) for worker in self.state["workers"].values()]

    def worker_action(self, worker_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            worker = self._worker(worker_id)
            if action == "approve":
                worker["approved"] = True
                worker["blocked"] = False
                worker["last_seen"] = now()
            elif action == "block":
                worker["blocked"] = True
            elif action == "unblock":
                worker["blocked"] = False
            elif action == "rename":
                name = str(payload.get("name", "")).strip()
                if not name:
                    raise APIError(400, "name is required")
                worker["name"] = name
            else:
                raise APIError(400, "action must be approve, block, unblock or rename")
            worker["reported_status"] = self.worker_status(worker)
            self._history("worker.updated", worker["name"], details={"worker_id": worker_id, "action": action})
            self._persist("worker.updated", self.worker_view(worker))
            return self.worker_view(worker)

    def claim_jobs(self, worker_id: str) -> list[dict[str, Any]]:
        with self.lock:
            requeued = self.expire_workers(persist=False)
            worker = self._worker(worker_id)
            if self.worker_status(worker) != "online":
                return []
            active_count = sum(1 for job in self.state["jobs"].values() if job.get("worker_id") == worker_id and job["status"] == "running")
            capacity = max(0, worker["max_parallel"] - active_count)
            claimed = []
            for job in sorted(self.state["jobs"].values(), key=lambda item: item["created_at"]):
                if capacity == 0:
                    break
                if job["status"] != "queued" or job["module_type"] not in worker["capabilities"] and "*" not in worker["capabilities"]:
                    continue
                asset_ids = job["asset_ids"]
                if not asset_ids or any(self.state["assets"].get(asset_id, {}).get("paused", True) for asset_id in asset_ids):
                    continue
                job.update({"status": "running", "worker_id": worker_id, "started_at": now(), "attempt": job["attempt"] + 1})
                for asset_id in asset_ids:
                    self.state["assets"][asset_id]["status"] = "running"
                    self._history("job.started", worker["name"], asset_id, job["id"], {"attempt": job["attempt"]})
                claimed.append(deepcopy(job))
                capacity -= 1
            if claimed or requeued:
                self._persist("job.updated", {"jobs": claimed, "requeued": requeued})
            return claimed

    def job_result(self, worker_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("status")
        if result not in {"success", "failed"}:
            raise APIError(400, "status must be success or failed")
        with self.lock:
            worker = self._worker(worker_id)
            job = self.state["jobs"].get(job_id)
            if job is None:
                raise APIError(404, "job not found")
            if job["worker_id"] != worker_id:
                raise APIError(403, "job is not assigned to this worker")
            if job["status"] != "running":
                raise APIError(409, "job is not running")
            job["status"] = result
            job["output"] = payload.get("output")
            job["finished_at"] = now()
            if payload.get("log"):
                entry = {"message": str(payload["log"]), "created_at": now()}
                job["logs"].append(entry)
            for asset_id in job["asset_ids"]:
                asset = self._asset(asset_id)
                if payload.get("log"):
                    self._history("job.log", str(payload["log"]), asset_id, job_id)
                if result == "failed":
                    asset["status"] = "failed"
                    for other in self.state["jobs"].values():
                        if asset_id in other["asset_ids"] and other["status"] in {"queued", "running"}:
                            other["status"] = "cancelled"
                            other["finished_at"] = now()
                    self._history("asset.failed", str(payload.get("message", "Job failed")), asset_id, job_id)
                else:
                    completed = asset.setdefault("completed_modules", [])
                    if job["module_id"] not in completed:
                        completed.append(job["module_id"])
                    if asset.get("paused"):
                        asset["resume_status"] = "queued"
                        asset["status"] = "paused"
                    else:
                        asset["status"] = "queued"
                    self._history("job.succeeded", "Job completed", asset_id, job_id)
                    self._advance(asset)
                asset["updated_at"] = now()
            self._persist("job.updated", deepcopy(job))
            return deepcopy(job)

    def job_log(self, worker_id: str, job_id: str, message: str) -> dict[str, Any]:
        with self.lock:
            job = self.state["jobs"].get(job_id)
            if job is None:
                raise APIError(404, "job not found")
            if job.get("worker_id") != worker_id or job["status"] != "running":
                raise APIError(403, "job is not active on this worker")
            entry = {"message": str(message), "created_at": now()}
            job["logs"].append(entry)
            for asset_id in job["asset_ids"]:
                self._history("job.log", entry["message"], asset_id, job_id)
            self._persist("job.updated", deepcopy(job))
            return entry

    def expire_workers(self, persist: bool = True) -> int:
        with self.lock:
            expired = [worker for worker in self.state["workers"].values() if worker["approved"] and not worker["blocked"] and self.worker_status(worker) == "offline"]
            changed = 0
            status_changed = []
            for worker in expired:
                if worker.get("reported_status") != "offline":
                    worker["reported_status"] = "offline"
                    status_changed.append(worker)
                for job in self.state["jobs"].values():
                    if job.get("worker_id") == worker["id"] and job["status"] == "running":
                        job.update({"status": "queued", "worker_id": None, "started_at": None})
                        for asset_id in job["asset_ids"]:
                            asset = self.state["assets"].get(asset_id)
                            if asset and not asset["paused"]:
                                asset["status"] = "queued"
                            self._history("job.requeued", "Worker went offline", asset_id, job["id"])
                        changed += 1
            if (changed or status_changed) and persist:
                self._persist("worker.updated", {"workers": [self.worker_view(w) for w in status_changed], "requeued": changed})
            return changed

    def dashboard(self) -> dict[str, Any]:
        with self.lock:
            workflows = []
            for workflow in self.state["workflows"].values():
                counts: dict[str, int] = {}
                for asset in self.state["assets"].values():
                    if asset["workflow_id"] == workflow["id"] and asset["status"] != "removed":
                        counts[asset["status"]] = counts.get(asset["status"], 0) + 1
                workflows.append({**deepcopy(workflow), "asset_counts": counts})
            return {"workflows": workflows, "workers": self.list_workers()}

    def list_jobs(self, workflow_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            items = [deepcopy(job) for job in self.state["jobs"].values() if (not workflow_id or job["workflow_id"] == workflow_id) and (not status or job["status"] == status)]
            return sorted(items, key=lambda item: item["created_at"])

    def publish_rollout(self, version: str, worker_ids: list[str] | None, package: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if version not in self.state["client_versions"]:
                self.state["client_versions"][version] = package
            targets = list(self.state["workers"].values()) if not worker_ids or "all" in worker_ids else [self._worker(worker_id) for worker_id in worker_ids]
            for worker in targets:
                worker["desired_client_version"] = version
            rollout = {"version": version, "worker_ids": [worker["id"] for worker in targets], "created_at": now()}
            self._history("rollout.created", version, details=rollout)
            self._persist("rollout.updated", rollout)
            return rollout


@dataclass
class Response:
    status: int = 200
    data: Any = field(default_factory=dict)
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


def _body_dict(body: Any) -> dict[str, Any]:
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise APIError(400, "request body must be a JSON object")
    return body


def package_directory(root: Path, config: dict[str, Any] | None = None) -> Path:
    configured = Path((config or {}).get("rollout", {}).get("package_dir", "dist/client")).expanduser()
    return (configured if configured.is_absolute() else root / configured).resolve()


def package_index(root: Path, engine: Engine, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    package_dir = package_directory(root, config)
    found = []
    if not package_dir.is_dir():
        return found
    for path in sorted(package_dir.glob("*.zip")):
        version = path.stem
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", version):
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("version") != version:
                continue
        except (OSError, KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError):
            continue
        item = {"version": version, "package_path": str(path.resolve()), "size": path.stat().st_size, "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}
        engine.state["client_versions"][version] = item
        found.append(item)
    if found:
        engine.store.save(engine.state)
    return found


def dispatch(method: str, raw_path: str, query_string: str, body: Any, engine: Engine, root: Path, config: dict[str, Any] | None = None) -> Response:
    path = unquote(raw_path)
    params = parse_qs(query_string)
    payload = {} if isinstance(body, str) else _body_dict(body)
    if method == "GET" and path == "/api/health":
        return Response(data={"status": "ok", "version": __version__})
    if method == "GET" and path == "/api/module-types":
        return Response(data={"items": module_types()})
    if method == "GET" and path == "/api/dashboard":
        return Response(data=engine.dashboard())
    if path == "/api/workflows":
        if method == "GET":
            return Response(data={"items": engine.list_workflows()})
        if method == "POST":
            return Response(201, engine.create_workflow(payload))
    match = re.fullmatch(r"/api/workflows/([^/]+)", path)
    if match:
        workflow_id = match.group(1)
        if method == "GET":
            return Response(data=engine._workflow(workflow_id))
        if method in {"PUT", "PATCH"}:
            return Response(data=engine.update_workflow(workflow_id, payload))
    match = re.fullmatch(r"/api/workflows/([^/]+)/actions", path)
    if match and method == "POST":
        action = payload.get("action")
        if action not in {"pause", "resume"}:
            raise APIError(400, "action must be pause or resume")
        return Response(data=engine.set_workflow_enabled(match.group(1), action == "resume"))
    match = re.fullmatch(r"/api/workflows/([^/]+)/versions", path)
    if match and method == "GET":
        return Response(data={"items": engine.workflow_versions(match.group(1))})
    match = re.fullmatch(r"/api/workflows/([^/]+)/versions/(\d+)", path)
    if match and method == "GET":
        versions = engine.workflow_versions(match.group(1))
        item = next((v for v in versions if v["version"] == int(match.group(2))), None)
        if item is None:
            raise APIError(404, "workflow version not found")
        return Response(data=item)
    if path == "/api/assets":
        if method == "GET":
            return Response(data={"items": engine.list_assets(params.get("status", [None])[0], params.get("workflow_id", [None])[0])})
        if method == "POST":
            workflow_id = str(payload.get("workflow_id", ""))
            if not workflow_id or not isinstance(payload.get("name"), str):
                raise APIError(400, "workflow_id and name are required")
            metadata = payload.get("metadata", {})
            if not isinstance(metadata, dict):
                raise APIError(400, "metadata must be an object")
            return Response(201, engine.create_asset(workflow_id, payload["name"], metadata))
    if path == "/api/assets/import" and method == "POST":
        text = body if isinstance(body, str) else payload.get("csv")
        workflow_id = payload.get("workflow_id") or params.get("workflow_id", [None])[0]
        if not isinstance(text, str) or not workflow_id:
            raise APIError(400, "workflow_id and CSV text are required")
        return Response(201, {"items": engine.import_assets(str(workflow_id), text)})
    match = re.fullmatch(r"/api/assets/([^/]+)", path)
    if match and method == "GET":
        return Response(data=engine.asset_detail(match.group(1)))
    match = re.fullmatch(r"/api/assets/([^/]+)/actions", path)
    if match and method == "POST":
        return Response(data=engine.asset_action(match.group(1), str(payload.get("action", ""))))
    if path == "/api/workers" and method == "GET":
        return Response(data={"items": engine.list_workers()})
    match = re.fullmatch(r"/api/workers/([^/]+)/actions", path)
    if match and method == "POST":
        return Response(data=engine.worker_action(match.group(1), str(payload.get("action", "")), payload))
    if path == "/api/jobs" and method == "GET":
        return Response(data={"items": engine.list_jobs(params.get("workflow_id", [None])[0], params.get("status", [None])[0])})
    if path == "/api/worker/register" and method == "POST":
        return Response(201, engine.register_worker(payload))
    match = re.fullmatch(r"/api/worker/([^/]+)/heartbeat", path)
    if match and method == "POST":
        return Response(data=engine.heartbeat(match.group(1), payload))
    match = re.fullmatch(r"/api/worker/([^/]+)/jobs/claim", path)
    if match and method == "POST":
        return Response(data={"items": engine.claim_jobs(match.group(1))})
    match = re.fullmatch(r"/api/worker/([^/]+)/jobs/([^/]+)/result", path)
    if match and method == "POST":
        return Response(data=engine.job_result(match.group(1), match.group(2), payload))
    match = re.fullmatch(r"/api/worker/([^/]+)/jobs/([^/]+)/log", path)
    if match and method == "POST":
        if not isinstance(payload.get("message"), str):
            raise APIError(400, "message is required")
        return Response(201, engine.job_log(match.group(1), match.group(2), payload["message"]))
    match = re.fullmatch(r"/api/worker/([^/]+)/rollout", path)
    if match and method == "GET":
        worker = engine._worker(match.group(1))
        return Response(data={"desired_version": worker.get("desired_client_version"), "update": engine.worker_update(worker)})
    if path == "/api/client-packages" and method == "GET":
        items = package_index(root, engine, config)
        return Response(data={"items": [{key: item[key] for key in ("version", "size", "created_at")} for item in items]})
    match = re.fullmatch(r"/api/client-packages/([^/]+)", path)
    if match and method == "GET":
        version = match.group(1)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", version):
            raise APIError(400, "invalid client version")
        packages = {item["version"]: item for item in package_index(root, engine, config)}
        item = packages.get(version)
        if item is None:
            raise APIError(404, "client package not found")
        return Response(data=Path(item["package_path"]).read_bytes(), content_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{version}.zip"'})
    if path == "/api/rollouts":
        if method == "GET":
            items = package_index(root, engine, config)
            packages = [{key: item[key] for key in ("version", "size", "created_at")} for item in items]
            return Response(data={"packages": packages, "workers": engine.list_workers()})
        if method == "POST":
            version = str(payload.get("version", ""))
            packages = {item["version"]: item for item in package_index(root, engine, config)}
            if version not in packages:
                raise APIError(404, "client package not found")
            worker_ids = payload.get("worker_ids")
            if worker_ids is not None and (not isinstance(worker_ids, list) or any(not isinstance(value, str) for value in worker_ids)):
                raise APIError(400, "worker_ids must be a list of ids or omitted for all workers")
            return Response(202, engine.publish_rollout(version, worker_ids, packages[version]))
    if path == "/api/auth/login" and method == "POST":
        raise APIError(501, "login integration is not configured")
    raise APIError(404, "endpoint not found")


class PostgresManager:
    def __init__(self, root: Path, config: dict[str, Any]):
        self.root = root
        self.config = config
        self.owned = False

    def _path(self, key: str) -> Path:
        value = Path(str(self.config[key])).expanduser()
        return (value if value.is_absolute() else self.root / value).resolve()

    @staticmethod
    def _binary(directory: Path, name: str) -> Path:
        candidates = [directory / name, directory / f"{name}.exe"]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    def start(self) -> None:
        bin_dir = self._path("bin_dir")
        data_dir = self._path("data_dir")
        pg_ctl = self._binary(bin_dir, "pg_ctl")
        initdb = self._binary(bin_dir, "initdb")
        if not pg_ctl.is_file() or not initdb.is_file():
            raise RuntimeError(f"PostgreSQL binaries not found in {bin_dir}")
        status = subprocess.run([str(pg_ctl), "status", "-D", str(data_dir)], capture_output=True, text=True)
        if status.returncode == 0:
            return
        data_dir.mkdir(parents=True, exist_ok=True)
        if not (data_dir / "PG_VERSION").exists():
            subprocess.run([str(initdb), "-D", str(data_dir), "-U", str(self.config["user"]), "--auth-local=trust", "--auth-host=trust", "--encoding=UTF8"], check=True, capture_output=True, text=True)
        options = f"-h 127.0.0.1 -p {int(self.config['port'])}"
        subprocess.run([str(pg_ctl), "start", "-D", str(data_dir), "-l", str(data_dir / "marathon-postgres.log"), "-o", options, "-w"], check=True, capture_output=True, text=True)
        self.owned = True

    def stop(self) -> None:
        if not self.owned:
            return
        pg_ctl = self._binary(self._path("bin_dir"), "pg_ctl")
        subprocess.run([str(pg_ctl), "stop", "-D", str(self._path("data_dir")), "-m", "fast", "-w"], check=False, capture_output=True, text=True)
        self.owned = False


def connect_args(config: dict[str, Any]) -> dict[str, Any]:
    values = {"host": "127.0.0.1", "port": int(config["port"]), "user": config["user"], "dbname": config["database"], "connect_timeout": 5}
    if config.get("password"):
        values["password"] = config["password"]
    return values


def ensure_database(config: dict[str, Any]) -> None:
    import psycopg
    from psycopg import sql
    args = connect_args(config)
    args["dbname"] = "postgres"
    with psycopg.connect(**args, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (config["database"],)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(config["database"])))


def load_config(root: Path) -> dict[str, Any]:
    import tomllib
    path = root / "server.toml"
    if not path.is_file():
        raise RuntimeError(f"server.toml not found at project root: {path}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], engine: Engine, root: Path, config: dict[str, Any]):
        self.engine = engine
        self.root = root
        self.config = config
        self.events = engine.events
        super().__init__(address, RequestHandler)


class RequestHandler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, response: Response) -> None:
        if response.content_type == "application/zip":
            content = response.data
        else:
            content = json.dumps(response.data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store" if response.content_type.startswith("application/json") else "public, max-age=300")
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 10 * 1024 * 1024:
            raise APIError(413, "request body exceeds 10 MiB")
        raw = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("text/csv"):
            return raw.decode("utf-8-sig")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise APIError(400, "request body must be valid JSON") from exc

    def _events(self) -> None:
        listener = self.server.events.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(b": connected\n\n")
        self.wfile.flush()
        try:
            while True:
                try:
                    item = listener.get(timeout=15)
                    raw = json.dumps({"data": item["data"], "timestamp": item["timestamp"]}, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(f"event: {item['type']}\ndata: {raw}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            self.server.events.unsubscribe(listener)

    def _static(self, url_path: str) -> None:
        configured = Path(self.server.config.get("server", {}).get("static_dir", "marathon/server/static"))
        static_root = (configured if configured.is_absolute() else self.server.root / configured).resolve()
        relative = url_path.lstrip("/") or "index.html"
        candidate = (static_root / relative).resolve()
        if not candidate.is_relative_to(static_root):
            self._send(Response(403, {"error": "forbidden"}))
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file() and url_path in {"/", ""}:
            candidate = static_root / "index.html"
        if not candidate.is_file():
            self._send(Response(404, {"error": "not found"}))
            return
        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if candidate.name == "index.html" else "public, max-age=300")
        self.end_headers()
        self.wfile.write(data)

    def _handle(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/api/events" and self.command == "GET":
            self._events()
            return
        if not parsed.path.startswith("/api/"):
            self._static(parsed.path)
            return
        try:
            body = self._read_body() if self.command in {"POST", "PUT", "PATCH", "DELETE"} else {}
            response = dispatch(self.command, parsed.path, parsed.query, body, self.server.engine, self.server.root, self.server.config)
        except APIError as exc:
            response = Response(exc.status, {"error": exc.message})
        except Exception:
            response = Response(500, {"error": "internal server error"})
        self._send(response)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    server_config = config.get("server", {})
    postgres_config = config.get("postgres", {})
    required = {"bin_dir", "data_dir", "port", "user", "database"}
    missing = required - set(postgres_config)
    if missing:
        raise RuntimeError(f"Missing PostgreSQL settings: {', '.join(sorted(missing))}")
    manager = PostgresManager(root, postgres_config)
    try:
        manager.start()
        ensure_database(postgres_config)
        store = PostgresStore(connect_args(postgres_config))
        engine = Engine(store, int(server_config.get("heartbeat_timeout_seconds", 30)))
        certfile = server_config.get("tls_certfile")
        keyfile = server_config.get("tls_keyfile")
        if bool(certfile) != bool(keyfile):
            raise RuntimeError("tls_certfile and tls_keyfile must both be configured")
        address = (str(server_config.get("host", "127.0.0.1")), int(server_config.get("port", 8765)))
        httpd = Server(address, engine, root, config)
        if certfile and keyfile:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str((root / certfile).resolve()), str((root / keyfile).resolve()))
            httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        monitor_stop = threading.Event()
        interval = max(1.0, min(10.0, engine.heartbeat_timeout_seconds / 2))

        def monitor_workers() -> None:
            while not monitor_stop.wait(interval):
                engine.expire_workers()

        monitor = threading.Thread(target=monitor_workers, daemon=True)
        monitor.start()
        print(f"Marathon server listening on {'https' if certfile else 'http'}://{address[0]}:{address[1]}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            monitor_stop.set()
            monitor.join(timeout=2)
            httpd.server_close()
    finally:
        manager.stop()
