# m2 prototype – shared contract (v0.1.0)

Binding interface between server, agent/modules and web UI. Change only together.

## Layout
- `m2/__init__.py` – `__version__ = "0.1.0"` (single source; `pyproject.toml` same version)
- `m2/server/` – FastAPI + SQLite, start: `python -m m2.server` (http://localhost:8000)
- `m2/server/static/` – web UI, served at `/` (no build step)
- `m2/agent/` – agent, start: `python -m m2.agent --server URL --name NAME --capabilities a,b --config agent.json`
- `m2/modules/<name>/manifest.json` (+ `module.py` for agent-run modules)
- `tests/` – pytest

## Module manifest
```json
{"name": "wait", "version": "1.0.0", "kind": "source|process|action|control",
 "inputs": ["in"], "outputs": ["success", "error"],
 "params_schema": {"type": "object", "properties": {}},
 "capabilities": [], "batch": false, "concurrency": 1}
```
- `kind: "source"` modules run on the server as pass-through (clip enters, leaves via `success`). Prototype source: `manual_source`.
- All other modules run in an agent. Agent module API: `run(ctx, clips, params) -> list[dict]`,
  `ctx.progress(pct)`, `ctx.log(line)`, `ctx.job_dir(clip) -> Path`, `ctx.resolve(file_ref) -> Path`.
  Result per clip: `{"clip_id", "status": "success|error", "output_port", "message", "metadata": {}, "files_add": [file_ref]}`.
- File reference: `{"storage": "media", "path": "job_x/file.mxf", "role": "restored"}`. Agent maps `storage` to a local root via its config (`{"storages": {"media": "D:/media"}}`). Job folder = `<storage root>/<job name>/`, flat.

## Workflow graph
```json
{"nodes": [{"id": "n1", "module": "wait", "params": {}, "position": {"x": 0, "y": 0}, "retry": {"max_attempts": 3}}],
 "edges": [{"id": "e1", "source": "n1", "source_port": "success", "target": "n2"}]}
```
Workflows are versioned; a clip stays on the version it started with.

## REST API (JSON, prefix /api)
| Method | Path | Body → Response |
|---|---|---|
| GET | /health | → `{status, version}` |
| GET | /modules | → `[manifest]` |
| GET/POST | /workflows | POST `{name}` → `{id, name}`; GET → `[{id, name, latest_version}]` |
| GET | /workflows/{wid} | → `{id, name, latest_version, graph}` |
| POST | /workflows/{wid}/versions | `{graph}` → `{version}` |
| POST | /workflows/{wid}/clips | `{name, job, metadata, files}` → `{id}` (enters all `manual_source` nodes of latest version) |
| GET | /workflows/{wid}/summary | → `{nodes: {node_id: {queued, running, done, failed}}, clips: {active, finished, failed}}` |
| GET | /workflows/{wid}/nodes/{nid}/tasks | → `[task]` |
| GET | /clips?workflow_id= | → `[clip]` |
| GET | /clips/{cid} | → `{clip, events: [event]}` (timeline) |
| GET | /workers | → `[{id, name, capabilities, tags, status, last_heartbeat}]` |
| POST | /tasks/{tid}/retry | manual re-release of a failed task |
| POST | /agents/register | `{name, capabilities, tags, modules}` → `{worker_id}` |
| POST | /agents/{wid}/heartbeat | → `{ok}` |
| POST | /agents/{wid}/lease | → 204 or `{task_id, module, module_version, params, attempt, lease_expires_at, clips: [{id, name, job, metadata, files}]}` |
| POST | /tasks/{tid}/heartbeat | `{progress, log: [lines]}` → extends lease |
| POST | /tasks/{tid}/result | `{results: [per-clip result]}` |
| GET | /events/stream | Server-Sent Events, one JSON event per message |

Task states: `queued, running, done, failed`. Clip states: `active, finished, failed`.

## Rules
- Event log (append-only table) is the source of truth; summaries derive from it/tasks.
- Lease 30 s, extended by task heartbeat; on expiry task is requeued (attempt +1).
- Error: requeue while `attempt < retry.max_attempts`, then route via `error` port; no `error` edge → clip `failed`. Node without outgoing edge for the port → clip `finished`.
- Lease only to agents whose capabilities/modules match the manifest.
- Event: `{id, ts, type, workflow_id, clip_id, node_id, task_id, worker_id, data}`; types: `clip.created, clip.entered_node, task.queued, task.leased, task.progress, task.succeeded, task.failed, task.requeued, clip.finished, clip.failed, worker.registered, worker.heartbeat`.
- No login; keep one auth hook (FastAPI dependency, no-op).
