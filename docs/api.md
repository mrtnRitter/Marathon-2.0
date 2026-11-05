# Marathon Server API

Base path: `/api`. All request/response bodies are UTF-8 JSON unless an endpoint says `text/csv` or ZIP. Errors are `{ "error": "message" }` with HTTP 400 for invalid input, 403 for ownership/permission failures, 404 for unknown entities, 409 for invalid state transitions, and 501 for the unconfigured login. Identifiers are opaque strings. Timestamps are ISO-8601 UTC.

Core representations: a workflow is `{id,name,enabled,version,graph,created_at,updated_at}`; an asset is `{id,workflow_id,name,metadata,status,paused,current_node,completed_modules,created_at,updated_at}`; a job is `{id,workflow_id,asset_ids,module_id,module_type,settings,status,worker_id,attempt,output,created_at,started_at,finished_at,logs}`; a worker view adds `{status,active_jobs}` to its registration fields. Optional values may be `null`. Workflow graphs use `nodes: [{id,type,settings}]` and `edges: [{from,to}]`; there is exactly one fixed `start` node with id `start`, inserted if omitted. Every module must be connected to start and the graph must be acyclic. Supported node types and form descriptions come from `GET /module-types`. A `start` node runs on the server and never creates a job. `wait` is a worker module with `seconds` (number, default 1, minimum 0) and `fail` (boolean, default false). Parallel branches are scheduled independently and joins wait for all incoming modules.

## UI endpoints

- `GET /health` → `{status,version}`.
- `GET /module-types` → `{items:[{type,execution,description,settings}]}`.
- `GET /dashboard` → `{workflows:[workflow + asset_counts],workers:[worker]}`.
- `GET /workflows` → `{items:[workflow]}`. `POST /workflows` `{name,enabled?,graph?}` → `201 workflow`.
- `GET /workflows/{id}` → workflow. `PUT|PATCH /workflows/{id}` accepts any of `name`, `enabled`, `graph` and creates an immutable numbered version. Removed current modules fail their assets immediately.
- `POST /workflows/{id}/actions` `{action:"pause"|"resume"}`. A paused workflow publishes no new jobs; resuming publishes for eligible assets.
- `GET /workflows/{id}/versions` → `{items:[{id,workflow_id,version,graph,created_at}]}`. `GET /workflows/{id}/versions/{number}` → one version.
- `GET /assets?status=&workflow_id=` → `{items:[asset]}`. Asset statuses are `queued`, `running`, `paused`, `completed`, `failed`; removed assets are omitted.
- `POST /assets` `{workflow_id,name,metadata?}` → `201 asset`.
- `POST /assets/import` accepts either JSON `{workflow_id,csv}` or `text/csv` body with `workflow_id` query parameter. CSV requires a `name` column; other columns become metadata. → `201 {items:[asset]}`.
- `GET /assets/{id}` → asset plus its `jobs` and chronological `history` (including worker logs).
- `POST /assets/{id}/actions` `{action:"retry"|"pause"|"resume"|"remove"}`. Retry starts again at `start`; remove is a soft removal.
- `GET /jobs?workflow_id=&status=` → `{items:[job]}`. Each job has an `asset_ids` array containing exactly one ID today; storage uses a join table to permit multiple assets later.
- `GET /workers` → `{items:[worker]}`. Worker status is `new`, `blocked`, `online` or `offline`.
- `POST /workers/{id}/actions` supports `{action:"approve"|"block"|"unblock"}` and `{action:"rename",name}`.
- `GET /client-packages` → `{items:[{version,size,created_at}]}`. `GET /rollouts` → available `packages` and `workers` with desired rollout versions.
- `POST /rollouts` `{version,worker_ids?:[id,...]}`; omission or `worker_ids:["all"]` targets every registered worker. → `202 {version,worker_ids,created_at}`. The desired version is sent on the next worker heartbeat.
- `POST /auth/login` is reserved and returns `501` until a login provider is connected.

## Worker endpoints

- `POST /worker/register` `{name,capabilities:[module_type,...],max_parallel,client_version?}` → `201 {id,name,status:"new",...,update}`. New workers require UI approval.
- `POST /worker/{id}/heartbeat` `{capabilities?,client_version?}` → worker view plus `update` (null or `{version,download_url}`). Heartbeat refreshes online status.
- `POST /worker/{id}/jobs/claim` with `{}` → `{items:[job]}` up to remaining parallel capacity. Claims require an approved, unblocked, online worker with a matching capability (`*` matches all). Job settings are a snapshot; workers execute wait duration and report failure when `fail=true`.
- `POST /worker/{id}/jobs/{job_id}/result` `{status:"success"|"failed",output?,message?,log?}`. The server advances successful assets to the next module or `completed`; a failed result marks the asset `failed`. Only the assigned worker may report.
- `POST /worker/{id}/jobs/{job_id}/log` `{message}` → `201 log entry`; entries appear in asset history.
- `GET /worker/{id}/rollout` → `{desired_version,update}`. `GET /client-packages/{version}` downloads the ZIP.

## Client package format

The server scans `dist/client/*.zip`; the filename stem is the rollout version and must contain only letters, digits, `.`, `_`, `+`, or `-`. The ZIP root must contain UTF-8 `manifest.json` with a matching string `version`; the remaining package files are provided by the build, including the worker starter and Python runtime. Invalid/mismatched archives are ignored. Download path is `/api/client-packages/{version}` and content type is `application/zip`.

## Live events and static UI

`GET /api/events` is an SSE stream (`text/event-stream`). Each message is `event: <type>` plus JSON `data: {data,timestamp}`. Event types: `workflow.updated`, `asset.updated`, `job.updated`, `worker.updated`, and `rollout.updated`. The server emits comment heartbeats every 15 seconds; use REST endpoints for the initial snapshot and after reconnecting. Built UI files are served from the configured `server.static_dir` (default `marathon/server/static/`), with `/` mapped to `index.html`.

## Server settings

The project-root `server.toml` contains `[server]` host/port, heartbeat timeout, static directory, and optional paired `tls_certfile`/`tls_keyfile`; `[postgres]` program directory, data directory, local port, user, database, and optional password; `[rollout]` documents the package directory. PostgreSQL is initialized/started before serving and stopped on normal shutdown when this process started it. The auth endpoint is a no-op placeholder.
