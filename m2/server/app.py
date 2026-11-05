from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from m2 import __version__
from m2.server.database import Database, json_value, unpack

LEASE_SECONDS = 30


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def identifier() -> str:
    return str(uuid.uuid4())


def _manifest_name(manifest: dict[str, Any]) -> str | None:
    return manifest.get("name") or manifest.get("module")


def load_manifests(directory: str | Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    root = Path(directory)
    if root.is_dir():
        for path in sorted(root.glob("*/manifest.json")):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(manifest, dict):
                continue
            name = _manifest_name(manifest)
            if name:
                result[name] = manifest
    result.setdefault(
        "manual_source",
        {
            "name": "manual_source",
            "version": "1.0.0",
            "kind": "source",
            "inputs": [],
            "outputs": ["success"],
            "params_schema": {"type": "object", "properties": {}},
            "capabilities": [],
            "batch": False,
            "concurrency": 1,
        },
    )
    return result


def create_app(
    database_path: str | Path | None = None,
    modules_dir: str | Path | None = None,
    lease_seconds: int = LEASE_SECONDS,
    clock: Callable[[], datetime] = utc_now,
) -> FastAPI:
    db_path = database_path or os.environ.get("M2_DB_PATH", "m2.sqlite3")
    manifest_path = modules_dir or os.environ.get(
        "M2_MODULES_DIR", Path(__file__).resolve().parents[1] / "modules"
    )
    database = Database(db_path)
    manifests = load_manifests(manifest_path)
    app = FastAPI(title="m2 prototype", version=__version__)
    app.state.database = database
    app.state.manifests = manifests
    app.state.lease_seconds = lease_seconds
    app.state.clock = clock

    async def auth_hook() -> None:
        return None

    def now() -> datetime:
        value = app.state.clock()
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def emit(
        connection,
        event_type: str,
        *,
        workflow_id: str | None = None,
        clip_id: str | None = None,
        node_id: str | None = None,
        task_id: str | None = None,
        worker_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> int:
        cursor = connection.execute(
            """INSERT INTO events(ts, type, workflow_id, clip_id, node_id, task_id, worker_id, data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp(now()), event_type, workflow_id, clip_id, node_id, task_id, worker_id, json_value(data or {})),
        )
        return int(cursor.lastrowid)

    def event_dict(row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "ts": row["ts"],
            "type": row["type"],
            "workflow_id": row["workflow_id"],
            "clip_id": row["clip_id"],
            "node_id": row["node_id"],
            "task_id": row["task_id"],
            "worker_id": row["worker_id"],
            "data": unpack(row["data"], {}),
        }

    def get_graph(connection, workflow_id: str, version: int) -> dict[str, Any]:
        row = connection.execute(
            "SELECT graph FROM workflow_versions WHERE workflow_id=? AND version=?",
            (workflow_id, version),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "workflow version not found")
        return unpack(row["graph"], {})

    def clip_row(connection, clip_id: str):
        row = connection.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "clip not found")
        return row

    def clip_payload(row, state: str | None = None) -> dict[str, Any]:
        return {
            "id": row["id"],
            "workflow_id": row["workflow_id"],
            "workflow_version": row["workflow_version"],
            "name": row["name"],
            "job": row["job"],
            "metadata": unpack(row["metadata"], {}),
            "files": unpack(row["files"], []),
            "status": state,
            "created_at": row["created_at"],
        }

    def route_clip(connection, clip_id: str, node_id: str, port: str) -> None:
        row = clip_row(connection, clip_id)
        graph = get_graph(connection, row["workflow_id"], row["workflow_version"])
        edges = [edge for edge in graph.get("edges", [])
                 if edge.get("source") == node_id and edge.get("source_port", "success") == port]
        if not edges:
            if port == "error":
                emit(connection, "clip.failed", workflow_id=row["workflow_id"], clip_id=clip_id,
                     node_id=node_id, data={"message": "unhandled error"})
            else:
                emit(connection, "clip.finished", workflow_id=row["workflow_id"], clip_id=clip_id,
                     node_id=node_id, data={})
            return
        pending = [edge.get("target") for edge in edges if edge.get("target")]
        while pending:
            target = pending.pop(0)
            target_node = next((node for node in graph.get("nodes", []) if node.get("id") == target), None)
            if target_node is None:
                emit(connection, "clip.failed", workflow_id=row["workflow_id"], clip_id=clip_id,
                     node_id=node_id, data={"message": f"unknown target node: {target}"})
                continue
            emit(connection, "clip.entered_node", workflow_id=row["workflow_id"], clip_id=clip_id,
                 node_id=target, data={"module": target_node.get("module")})
            module = target_node.get("module")
            manifest = app.state.manifests.get(module, {})
            if manifest.get("kind") == "source" or module == "manual_source":
                next_edges = [edge for edge in graph.get("edges", [])
                              if edge.get("source") == target and edge.get("source_port", "success") == "success"]
                if next_edges:
                    pending.extend(edge.get("target") for edge in next_edges if edge.get("target"))
                else:
                    emit(connection, "clip.finished", workflow_id=row["workflow_id"], clip_id=clip_id,
                         node_id=target, data={})
                continue
            retry = target_node.get("retry") or {}
            max_attempts = max(1, int(retry.get("max_attempts", 1)))
            task_id = identifier()
            created = timestamp(now())
            connection.execute(
                """INSERT INTO tasks(id, workflow_id, workflow_version, node_id, module, params, clip_ids,
                   state, attempt, max_attempts, logs, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 1, ?, '[]', ?, ?)""",
                (task_id, row["workflow_id"], row["workflow_version"], target, module or "", 
                 json_value(target_node.get("params") or {}), json_value([clip_id]), max_attempts, created, created),
            )
            emit(connection, "task.queued", workflow_id=row["workflow_id"], clip_id=clip_id,
                 node_id=target, task_id=task_id, data={"attempt": 1})

    def fail_task(connection, task, message: str, worker_id: str | None = None) -> None:
        clip_ids = unpack(task["clip_ids"], [])
        for clip_id in clip_ids:
            emit(connection, "task.failed", workflow_id=task["workflow_id"], clip_id=clip_id,
                 node_id=task["node_id"], task_id=task["id"], worker_id=worker_id,
                 data={"attempt": task["attempt"], "message": message})
        if task["attempt"] < task["max_attempts"]:
            next_attempt = task["attempt"] + 1
            connection.execute(
                "UPDATE tasks SET state='queued', attempt=?, worker_id=NULL, lease_expires_at=NULL, message=?, updated_at=? WHERE id=?",
                (next_attempt, message, timestamp(now()), task["id"]),
            )
            for clip_id in clip_ids:
                emit(connection, "task.requeued", workflow_id=task["workflow_id"], clip_id=clip_id,
                     node_id=task["node_id"], task_id=task["id"], data={"attempt": next_attempt, "message": message})
                emit(connection, "task.queued", workflow_id=task["workflow_id"], clip_id=clip_id,
                     node_id=task["node_id"], task_id=task["id"], data={"attempt": next_attempt})
            return
        connection.execute(
            "UPDATE tasks SET state='failed', worker_id=NULL, lease_expires_at=NULL, message=?, updated_at=? WHERE id=?",
            (message, timestamp(now()), task["id"]),
        )
        for clip_id in clip_ids:
            route_clip(connection, clip_id, task["node_id"], "error")

    def expire_leases(connection) -> None:
        current = timestamp(now())
        expired = connection.execute(
            "SELECT * FROM tasks WHERE state='running' AND lease_expires_at <= ?", (current,)
        ).fetchall()
        for task in expired:
            worker_id = task["worker_id"]
            fail_task(connection, task, "lease expired", worker_id)
            if worker_id:
                connection.execute("UPDATE workers SET status='idle' WHERE id=?", (worker_id,))

    def worker_supports(worker, manifest: dict[str, Any], module_name: str) -> bool:
        modules = unpack(worker["modules"], [])
        names = {item if isinstance(item, str) else item.get("name", item.get("module"))
                 for item in modules if isinstance(item, (str, dict))}
        required = set(manifest.get("capabilities") or [])
        capabilities = set(unpack(worker["capabilities"], []))
        return module_name in names and required.issubset(capabilities)

    @app.get("/api/health", dependencies=[Depends(auth_hook)])
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/modules", dependencies=[Depends(auth_hook)])
    def modules():
        return list(app.state.manifests.values())

    @app.get("/api/workflows", dependencies=[Depends(auth_hook)])
    def list_workflows():
        with database.connection() as connection:
            rows = connection.execute(
                """SELECT w.id, w.name, COALESCE(MAX(v.version), 0) AS latest_version
                   FROM workflows w LEFT JOIN workflow_versions v ON v.workflow_id=w.id
                   GROUP BY w.id ORDER BY w.created_at"""
            ).fetchall()
            return [dict(row) for row in rows]

    @app.post("/api/workflows", dependencies=[Depends(auth_hook)])
    def create_workflow(body: dict[str, Any]):
        name = str(body.get("name", "")).strip()
        if not name:
            raise HTTPException(422, "name is required")
        workflow_id = identifier()
        with database.connection() as connection:
            connection.execute("INSERT INTO workflows(id, name, created_at) VALUES (?, ?, ?)",
                               (workflow_id, name, timestamp(now())))
        return {"id": workflow_id, "name": name}

    @app.get("/api/workflows/{workflow_id}", dependencies=[Depends(auth_hook)])
    def get_workflow(workflow_id: str):
        with database.connection() as connection:
            row = connection.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "workflow not found")
            version = connection.execute(
                "SELECT version, graph FROM workflow_versions WHERE workflow_id=? ORDER BY version DESC LIMIT 1",
                (workflow_id,),
            ).fetchone()
            return {"id": row["id"], "name": row["name"],
                    "latest_version": version["version"] if version else 0,
                    "graph": unpack(version["graph"], {}) if version else {"nodes": [], "edges": []}}

    @app.post("/api/workflows/{workflow_id}/versions", dependencies=[Depends(auth_hook)])
    def create_workflow_version(workflow_id: str, body: dict[str, Any]):
        graph = body.get("graph")
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes", []), list) or not isinstance(graph.get("edges", []), list):
            raise HTTPException(422, "graph must contain node and edge lists")
        with database.connection(immediate=True) as connection:
            if connection.execute("SELECT 1 FROM workflows WHERE id=?", (workflow_id,)).fetchone() is None:
                raise HTTPException(404, "workflow not found")
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0)+1 FROM workflow_versions WHERE workflow_id=?", (workflow_id,)
            ).fetchone()[0]
            connection.execute("INSERT INTO workflow_versions(workflow_id, version, graph, created_at) VALUES (?, ?, ?, ?)",
                               (workflow_id, version, json_value(graph), timestamp(now())))
        return {"version": version}

    @app.post("/api/workflows/{workflow_id}/clips", dependencies=[Depends(auth_hook)])
    def create_clip(workflow_id: str, body: dict[str, Any]):
        with database.connection(immediate=True) as connection:
            workflow = connection.execute("SELECT id FROM workflows WHERE id=?", (workflow_id,)).fetchone()
            if workflow is None:
                raise HTTPException(404, "workflow not found")
            version_row = connection.execute(
                "SELECT version, graph FROM workflow_versions WHERE workflow_id=? ORDER BY version DESC LIMIT 1",
                (workflow_id,),
            ).fetchone()
            if version_row is None:
                raise HTTPException(409, "workflow has no version")
            clip_id = identifier()
            name = str(body.get("name") or clip_id)
            job = str(body.get("job") or "")
            metadata = body.get("metadata") or {}
            files = body.get("files") or []
            if not isinstance(metadata, dict) or not isinstance(files, list):
                raise HTTPException(422, "metadata must be an object and files a list")
            created = timestamp(now())
            connection.execute(
                "INSERT INTO clips(id, workflow_id, workflow_version, name, job, metadata, files, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (clip_id, workflow_id, version_row["version"], name, job,
                 json_value(metadata), json_value(files), created),
            )
            emit(connection, "clip.created", workflow_id=workflow_id, clip_id=clip_id,
                 data={"name": name, "job": job})
            graph = unpack(version_row["graph"], {})
            sources = [node for node in graph.get("nodes", [])
                       if node.get("module") == "manual_source"]
            for source in sources:
                emit(connection, "clip.entered_node", workflow_id=workflow_id, clip_id=clip_id,
                     node_id=source.get("id"), data={"module": source.get("module")})
                route_clip(connection, clip_id, source.get("id"), "success")
        return {"id": clip_id}

    @app.get("/api/workflows/{workflow_id}/summary", dependencies=[Depends(auth_hook)])
    def workflow_summary(workflow_id: str):
        with database.connection() as connection:
            workflow = connection.execute("SELECT id FROM workflows WHERE id=?", (workflow_id,)).fetchone()
            if workflow is None:
                raise HTTPException(404, "workflow not found")
            expire_leases(connection)
            latest = connection.execute("SELECT graph FROM workflow_versions WHERE workflow_id=? ORDER BY version DESC LIMIT 1",
                                        (workflow_id,)).fetchone()
            node_counts: dict[str, dict[str, int]] = {}
            if latest:
                for node in unpack(latest["graph"], {}).get("nodes", []):
                    node_counts[node.get("id")] = {"queued": 0, "running": 0, "done": 0, "failed": 0}
            tasks = connection.execute("SELECT node_id, state, COUNT(*) count FROM tasks WHERE workflow_id=? GROUP BY node_id, state",
                                        (workflow_id,)).fetchall()
            for row in tasks:
                node_counts.setdefault(row["node_id"], {"queued": 0, "running": 0, "done": 0, "failed": 0})[row["state"]] = row["count"]
            clips = connection.execute("SELECT id FROM clips WHERE workflow_id=?", (workflow_id,)).fetchall()
            states = {"active": 0, "finished": 0, "failed": 0}
            for clip in clips:
                events = connection.execute("SELECT type FROM events WHERE clip_id=? ORDER BY id", (clip["id"],)).fetchall()
                state = "active"
                for event in events:
                    if event["type"] == "clip.finished":
                        state = "finished"
                    elif event["type"] == "clip.failed":
                        state = "failed"
                    elif event["type"] in ("clip.entered_node", "task.queued", "task.requeued"):
                        state = "active"
                states[state] += 1
            return {"nodes": node_counts, "clips": states}

    @app.get("/api/workflows/{workflow_id}/nodes/{node_id}/tasks", dependencies=[Depends(auth_hook)])
    def node_tasks(workflow_id: str, node_id: str):
        with database.connection() as connection:
            if connection.execute("SELECT 1 FROM workflows WHERE id=?", (workflow_id,)).fetchone() is None:
                raise HTTPException(404, "workflow not found")
            expire_leases(connection)
            rows = connection.execute("SELECT * FROM tasks WHERE workflow_id=? AND node_id=? ORDER BY created_at",
                                      (workflow_id, node_id)).fetchall()
            return [{**dict(row), "params": unpack(row["params"], {}),
                     "clip_ids": unpack(row["clip_ids"], []), "logs": unpack(row["logs"], [])}
                    for row in rows]

    @app.get("/api/clips", dependencies=[Depends(auth_hook)])
    def list_clips(workflow_id: str | None = None):
        with database.connection() as connection:
            if workflow_id is None:
                rows = connection.execute("SELECT * FROM clips ORDER BY created_at").fetchall()
            else:
                rows = connection.execute("SELECT * FROM clips WHERE workflow_id=? ORDER BY created_at", (workflow_id,)).fetchall()
            result = []
            for row in rows:
                events = connection.execute("SELECT type FROM events WHERE clip_id=? ORDER BY id", (row["id"],)).fetchall()
                state = "active"
                for event in events:
                    if event["type"] == "clip.finished":
                        state = "finished"
                    elif event["type"] == "clip.failed":
                        state = "failed"
                    elif event["type"] in ("clip.entered_node", "task.queued", "task.requeued"):
                        state = "active"
                result.append(clip_payload(row, state))
            return result

    @app.get("/api/clips/{clip_id}", dependencies=[Depends(auth_hook)])
    def get_clip(clip_id: str):
        with database.connection() as connection:
            row = clip_row(connection, clip_id)
            events_rows = connection.execute("SELECT * FROM events WHERE clip_id=? ORDER BY id", (clip_id,)).fetchall()
            events = [event_dict(item) for item in events_rows]
            state = "active"
            for event in events:
                if event["type"] == "clip.finished":
                    state = "finished"
                elif event["type"] == "clip.failed":
                    state = "failed"
                elif event["type"] in ("clip.entered_node", "task.queued", "task.requeued"):
                    state = "active"
            return {"clip": clip_payload(row, state), "events": events}

    @app.get("/api/workers", dependencies=[Depends(auth_hook)])
    def list_workers():
        with database.connection() as connection:
            expire_leases(connection)
            rows = connection.execute("SELECT * FROM workers ORDER BY name").fetchall()
            return [{"id": row["id"], "name": row["name"],
                     "capabilities": unpack(row["capabilities"], []),
                     "tags": unpack(row["tags"], []), "status": row["status"],
                     "last_heartbeat": row["last_heartbeat"]} for row in rows]

    @app.post("/api/tasks/{task_id}/retry", dependencies=[Depends(auth_hook)])
    def retry_task(task_id: str):
        with database.connection(immediate=True) as connection:
            expire_leases(connection)
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise HTTPException(404, "task not found")
            if task["state"] != "failed":
                raise HTTPException(409, "only failed tasks can be retried")
            attempt = task["attempt"] + 1
            current = timestamp(now())
            connection.execute("UPDATE tasks SET state='queued', attempt=?, worker_id=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?",
                               (attempt, current, task_id))
            for clip_id in unpack(task["clip_ids"], []):
                emit(connection, "task.requeued", workflow_id=task["workflow_id"], clip_id=clip_id,
                     node_id=task["node_id"], task_id=task_id, data={"attempt": attempt, "manual": True})
                emit(connection, "task.queued", workflow_id=task["workflow_id"], clip_id=clip_id,
                     node_id=task["node_id"], task_id=task_id, data={"attempt": attempt})
        return {"task_id": task_id, "state": "queued", "attempt": attempt}

    @app.post("/api/agents/register", dependencies=[Depends(auth_hook)])
    def register_agent(body: dict[str, Any]):
        name = str(body.get("name", "")).strip()
        if not name:
            raise HTTPException(422, "name is required")
        worker_id = identifier()
        current = timestamp(now())
        with database.connection() as connection:
            connection.execute(
                "INSERT INTO workers(id, name, capabilities, tags, modules, status, last_heartbeat) VALUES (?, ?, ?, ?, ?, 'idle', ?)",
                (worker_id, name, json_value(body.get("capabilities") or []),
                 json_value(body.get("tags") or []), json_value(body.get("modules") or []), current),
            )
            emit(connection, "worker.registered", worker_id=worker_id,
                 data={"name": name, "capabilities": body.get("capabilities") or [],
                       "tags": body.get("tags") or [], "modules": body.get("modules") or []})
        return {"worker_id": worker_id}

    @app.post("/api/agents/{worker_id}/heartbeat", dependencies=[Depends(auth_hook)])
    def agent_heartbeat(worker_id: str):
        with database.connection() as connection:
            worker = connection.execute("SELECT id FROM workers WHERE id=?", (worker_id,)).fetchone()
            if worker is None:
                raise HTTPException(404, "worker not found")
            current = timestamp(now())
            connection.execute("UPDATE workers SET last_heartbeat=?, status=CASE WHEN status='busy' THEN 'busy' ELSE 'idle' END WHERE id=?",
                               (current, worker_id))
            emit(connection, "worker.heartbeat", worker_id=worker_id, data={})
        return {"ok": True}

    @app.post("/api/agents/{worker_id}/lease", dependencies=[Depends(auth_hook)])
    def lease_task(worker_id: str):
        with database.connection(immediate=True) as connection:
            expire_leases(connection)
            worker = connection.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
            if worker is None:
                raise HTTPException(404, "worker not found")
            tasks = connection.execute("SELECT * FROM tasks WHERE state='queued' ORDER BY created_at").fetchall()
            task = next((candidate for candidate in tasks
                         if worker_supports(worker, app.state.manifests.get(candidate["module"], {}), candidate["module"])), None)
            if task is None:
                return Response(status_code=204)
            expires = timestamp(now() + timedelta(seconds=app.state.lease_seconds))
            current = timestamp(now())
            connection.execute("UPDATE tasks SET state='running', worker_id=?, lease_expires_at=?, updated_at=? WHERE id=?",
                               (worker_id, expires, current, task["id"]))
            connection.execute("UPDATE workers SET status='busy', last_heartbeat=? WHERE id=?", (current, worker_id))
            clip_ids = unpack(task["clip_ids"], [])
            clips = []
            for clip_id in clip_ids:
                clip = clip_row(connection, clip_id)
                clips.append({"id": clip["id"], "name": clip["name"], "job": clip["job"],
                              "metadata": unpack(clip["metadata"], {}), "files": unpack(clip["files"], [])})
                emit(connection, "task.leased", workflow_id=task["workflow_id"], clip_id=clip_id,
                     node_id=task["node_id"], task_id=task["id"], worker_id=worker_id,
                     data={"attempt": task["attempt"], "lease_expires_at": expires})
            manifest = app.state.manifests.get(task["module"], {})
            return {"task_id": task["id"], "module": task["module"],
                    "module_version": manifest.get("version", "0.0.0"),
                    "params": unpack(task["params"], {}), "attempt": task["attempt"],
                    "lease_expires_at": expires, "clips": clips}

    @app.post("/api/tasks/{task_id}/heartbeat", dependencies=[Depends(auth_hook)])
    def task_heartbeat(task_id: str, body: dict[str, Any]):
        with database.connection(immediate=True) as connection:
            expire_leases(connection)
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise HTTPException(404, "task not found")
            if task["state"] != "running":
                raise HTTPException(409, "task is not leased")
            expires = timestamp(now() + timedelta(seconds=app.state.lease_seconds))
            progress = body.get("progress", task["progress"])
            logs = unpack(task["logs"], [])
            new_logs = body.get("log") or []
            if isinstance(new_logs, str):
                new_logs = [new_logs]
            if isinstance(new_logs, list):
                logs.extend(str(line) for line in new_logs)
            connection.execute("UPDATE tasks SET lease_expires_at=?, progress=?, logs=?, updated_at=? WHERE id=?",
                               (expires, progress, json_value(logs), timestamp(now()), task_id))
            for clip_id in unpack(task["clip_ids"], []):
                emit(connection, "task.progress", workflow_id=task["workflow_id"], clip_id=clip_id,
                     node_id=task["node_id"], task_id=task_id, worker_id=task["worker_id"],
                     data={"progress": progress, "log": new_logs, "lease_expires_at": expires})
        return {"ok": True, "lease_expires_at": expires}

    @app.post("/api/tasks/{task_id}/result", dependencies=[Depends(auth_hook)])
    def task_result(task_id: str, body: dict[str, Any]):
        results = body.get("results")
        if not isinstance(results, list):
            raise HTTPException(422, "results must be a list")
        with database.connection(immediate=True) as connection:
            expire_leases(connection)
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise HTTPException(404, "task not found")
            if task["state"] != "running":
                raise HTTPException(409, "task is not leased")
            expected = set(unpack(task["clip_ids"], []))
            submitted = {str(result.get("clip_id")) for result in results if isinstance(result, dict)}
            if not expected.issubset(submitted):
                raise HTTPException(422, "a result is required for every leased clip")
            worker_id = task["worker_id"]
            connection.execute("UPDATE workers SET status='idle' WHERE id=?", (worker_id,))
            for result in results:
                if not isinstance(result, dict) or str(result.get("clip_id")) not in expected:
                    continue
                clip_id = str(result["clip_id"])
                success = result.get("status") == "success"
                port = result.get("output_port") or ("success" if success else "error")
                message = str(result.get("message") or "")
                clip = clip_row(connection, clip_id)
                metadata = unpack(clip["metadata"], {})
                metadata.update(result.get("metadata") or {})
                files = unpack(clip["files"], [])
                files.extend(result.get("files_add") or [])
                connection.execute("UPDATE clips SET metadata=?, files=? WHERE id=?",
                                   (json_value(metadata), json_value(files), clip_id))
                emit(connection, "task.succeeded" if success else "task.failed",
                     workflow_id=task["workflow_id"], clip_id=clip_id, node_id=task["node_id"],
                     task_id=task_id, worker_id=worker_id,
                     data={"attempt": task["attempt"], "output_port": port, "message": message,
                           "metadata": result.get("metadata") or {}, "files_add": result.get("files_add") or []})
                if success:
                    connection.execute(
                        "UPDATE tasks SET state='done', worker_id=NULL, lease_expires_at=NULL, progress=100, message=?, updated_at=? WHERE id=?",
                        (message, timestamp(now()), task_id),
                    )
                    route_clip(connection, clip_id, task["node_id"], port)
                elif task["attempt"] < task["max_attempts"]:
                    next_attempt = task["attempt"] + 1
                    connection.execute(
                        "UPDATE tasks SET state='queued', attempt=?, worker_id=NULL, lease_expires_at=NULL, message=?, updated_at=? WHERE id=?",
                        (next_attempt, message, timestamp(now()), task_id),
                    )
                    emit(connection, "task.requeued", workflow_id=task["workflow_id"], clip_id=clip_id,
                         node_id=task["node_id"], task_id=task_id,
                         data={"attempt": next_attempt, "message": message})
                    emit(connection, "task.queued", workflow_id=task["workflow_id"], clip_id=clip_id,
                         node_id=task["node_id"], task_id=task_id, data={"attempt": next_attempt})
                else:
                    connection.execute(
                        "UPDATE tasks SET state='failed', worker_id=NULL, lease_expires_at=NULL, message=?, updated_at=? WHERE id=?",
                        (message, timestamp(now()), task_id),
                    )
                    route_clip(connection, clip_id, task["node_id"], "error")
        return {"ok": True}

    @app.get("/api/events/stream", dependencies=[Depends(auth_hook)])
    async def event_stream(request: Request, after: int = 0):
        header_id = request.headers.get("last-event-id")
        try:
            cursor = max(after, int(header_id)) if header_id else after
        except ValueError:
            cursor = after

        async def generate():
            nonlocal cursor
            while not await request.is_disconnected():
                with database.connection() as connection:
                    rows = connection.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT 100", (cursor,)).fetchall()
                for row in rows:
                    cursor = row["id"]
                    yield f"data: {json.dumps(event_dict(row), separators=(',', ':'))}\n\n"
                if not rows:
                    await asyncio.sleep(0.25)

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    static_dir = Path(__file__).with_name("static")
    app.mount("/", StaticFiles(directory=str(static_dir), html=True, check_dir=False), name="static")
    return app
