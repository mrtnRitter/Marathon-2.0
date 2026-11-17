# Engine – verbindlicher Umsetzungsvertrag

Gilt für den Release Engine. Grundlage: `docs/mission_log.md` (Abschnitt Engine). Bezeichner im Code englisch, Texte für Nutzer deutsch und wörtlich wie hier.

## 1. Konfiguration `config/marathon.toml`

Ersetzt `config/server.toml` (gleiche Einträge `host`, `port`, `[database]`). Lader bleibt `load_server_config()` in `marathon/server/config.py`.

```toml
[engine]
tick_interval_s = 0.5   # Takt der Engine in Sekunden (0.05–10)
poll_timeout_s = 25     # So lange hält der Server die Anfrage eines Workers offen (1–60)
worker_timeout_s = 30   # Ohne Meldung gilt ein Worker danach als ausgefallen (10–600)
pickup_timeout_s = 10   # Abhol-Frist für zugeteilte Jobs (1–600)
max_attempts = 3        # Unterbrochene Versuche je Asset und Modul (1–10)

[server_worker]
enabled = true          # Server-Worker mit dem Server starten
max_parallel = 2        # Parallele Jobs des Server-Workers (1–64)
last_choice = []        # Modultypen, für die der Server-Worker nur letzte Wahl ist, z. B. ["transcode", "qc"]
```

```python
@dataclass(frozen=True)
class EngineConfig:
    tick_interval_s: float = 0.5
    poll_timeout_s: float = 25.0
    worker_timeout_s: float = 30.0
    pickup_timeout_s: float = 10.0
    max_attempts: int = 3

@dataclass(frozen=True)
class ServerWorkerConfig:
    enabled: bool = True
    max_parallel: int = 2
    last_choice: tuple[str, ...] = ()

# ServerConfig bekommt zusätzlich:
#   engine: EngineConfig = field(default_factory=EngineConfig)
#   server_worker: ServerWorkerConfig = field(default_factory=ServerWorkerConfig)
```

Zahlen: `int` oder `float`, nie `bool`; `max_attempts`, `max_parallel` nur `int`. `last_choice`: Liste von Texten, je `^[a-z0-9_]{1,64}$`. Fehler: `ConfigError` mit Abschnitt und Schlüssel, z. B. `[engine] poll_timeout_s in <datei> muss eine Zahl zwischen 1 und 60 sein`.

## 2. Datenbank – Migration `0005`

| Tabelle | Änderung |
|---|---|
| `module_types` | `execution TEXT NOT NULL DEFAULT 'none'`, Check `execution IN ('worker','engine','none')` |
| `workflows` | `pausing BOOLEAN NOT NULL DEFAULT false`, `last_assigned_at TIMESTAMPTZ NULL` |
| `assets` | `from_module_id TEXT NULL`, `from_output_id TEXT NULL`, `attempts INTEGER NOT NULL DEFAULT 0` (Check `>= 0`), `wait_until TIMESTAMPTZ NULL` |
| `jobs` | `type_id TEXT NOT NULL DEFAULT ''`, `version INTEGER NULL`, `settings JSONB NOT NULL DEFAULT '{}'`, `pickup_deadline TIMESTAMPTZ NULL`, `error TEXT NULL`; `ck_jobs_status` neu: `status IN ('open','assigned','running','cancelling','succeeded','failed','cancelled','interrupted')`; Index `ix_jobs_status` auf `status` |
| `job_assets` | `output_id TEXT NULL` |
| `workers` | `priorities JSONB NOT NULL DEFAULT '{}'`, `client_slots INTEGER NOT NULL DEFAULT 1` (Check `>= 1`) |

Downgrade: setzt Jobs mit `running`, `cancelling`, `interrupted` auf `cancelled`, stellt den alten Check wieder her und entfernt alle neuen Spalten und den Index.

Aktive Jobs: `active_job_statuses = ("assigned", "running", "cancelling")`. Ein Asset hat höchstens einen aktiven Job.

Dateipfad eines Assets: `metadata["path"]`, sonst `metadata["source_path"]`; geändert wird immer `metadata["path"]`.

## 3. Modulkatalog

- Neuer optionaler Schlüssel im Deskriptor `MODULE["execution"]`: `"worker"` (läuft als Job auf einem Worker), `"engine"` (erledigt die Engine), `"none"` (wird nicht ausgeführt). Standard `"none"`; anderer Wert → `CatalogError`. `build_catalog()` gibt den Schlüssel immer mit aus; `sync_module_types()` schreibt ihn in `module_types.execution`.
- Ausgänge mit `"data": True` sind keine Asset-Wege (Prioliste `list`). Die Engine folgt ihnen nie und zählt sie nicht als eingehende Verbindung.
- Werte: `wait` → `engine`; `copy_file`, `move_file`, `rename_file` → `worker`; `watchfolder`, `priolist` → `none`. Prioliste-Ausgang `list` bekommt `"data": True`.
- Engine-Modul `wait`: Wartezeit `settings["wait_s"]` (Standard 60). Andere Engine-Modultypen gibt es nicht.

## 4. Zustände und Übergänge

### 4.1 Asset

| `status` | `current_module_id` | `step_status` | Bedeutung |
|---|---|---|---|
| `pending` | Quellmodul oder NULL | NULL | wartet auf Eintritt in den Workflow |
| `in_progress` | Modul | `waiting` | steht an einem Modul, kein aktiver Job |
| `in_progress` | Modul | `running` | genau ein aktiver Job **oder** Warten läuft (`wait_until` gesetzt) |
| `done` | letztes Modul | NULL | Erledigt |
| `failed` | Modul, an dem es endete | NULL | Fehlgeschlagen |
| `on_hold` | – | – | Engine fasst es nicht an |

Die Engine bewegt Assets nur in Workflows mit `active AND NOT pausing`. Ergebnisse laufender Jobs werden immer ausgewertet.

### 4.2 Job

| Von | Ereignis | Nach | Asset |
|---|---|---|---|
| – | Zuteilung | `assigned` (Worker, `pickup_deadline`) | `running` |
| `assigned` | Worker meldet Start | `running` (`started_at`) | – |
| `assigned` | Abhol-Frist abgelaufen, Worker offline oder neu angemeldet | `cancelled`, Fehler „Nicht abgeholt“ | `waiting` am selben Modul, Versuche unverändert |
| `assigned` | Pausieren / Modul entfernt | `cancelled` | zurück (`return_asset`) |
| `running` | Ergebnis | `succeeded` (`output_id`) | über den gemeldeten Ausgang weiter |
| `running` | Fehler im Modul | `failed` (`error`) | über Ausgang `error`, ohne diesen Ausgang Fehlgeschlagen |
| `running` | Pausieren / Modul entfernt | `cancelling` | bleibt `running` |
| `running`, `cancelling` | Worker antwortet nicht, neu angemeldet oder meldet ungefragt Abbruch | `interrupted` | Unterbrechungsregel |
| `cancelling` | Ergebnis oder Fehler | `succeeded` / `failed` | wie bei `running` (fertiges Ergebnis wird übernommen) |
| `cancelling` | Abbruch bestätigt | `cancelled` | zurück (`return_asset`) |
| Endzustand | jede Meldung | unverändert | – (Log „Spätes Ergebnis verworfen“) |

**Unterbrechungsregel:** `attempts + 1`; Historie (error) „Versuch {n} an „{modul}“ unterbrochen: {grund}“. Ist `attempts >= max_attempts`: Fehlgeschlagen mit „Nach {n} unterbrochenen Versuchen fehlgeschlagen“, sonst `return_asset`.

### 4.3 Worker

| Von | Ereignis | Nach |
|---|---|---|
| – | erste Anmeldung | `new`; Worker-ID `server-worker` sofort `ready` |
| `new`, `locked` | Freigeben | `ready` |
| jeder | Sperren | `locked` (laufende Jobs laufen weiter, keine neuen) |
| `ready`, `busy` | `worker_timeout_s` ohne Meldung | `offline` |
| `offline` | Anmeldung oder Anfrage | `ready` |
| `ready` ⇄ `busy` | Engine-Takt: aktiver Job vorhanden ⇄ keiner | |

Jobs bekommen nur `ready`/`busy`. Freie Plätze = `min(max_parallel, client_slots)` − aktive Jobs. Angebot = Modultypen in `capabilities` (meldet der Worker) ohne Vorrang `off`. Vorrang je Modultyp in `priorities`: `preferred` (0), `normal` (1, Standard), `last` (2), `off`. Bei der ersten Anmeldung werden die gemeldeten `last_choice`-Typen auf `last` gesetzt.

### 4.4 Workflow

| Zustand | `active` | `pausing` |
|---|---|---|
| Läuft | true | false |
| Wird pausiert | false | true |
| Pausiert | false | false |

Start während *Wird pausiert* → Fehler „Workflow wird gerade pausiert“. *Wird pausiert* endet, sobald der Workflow keine aktiven Jobs mehr hat.

## 5. Engine-Funktionen

Alle Funktionen laufen in der Transaktion des Aufrufers. Sperrreihenfolge: `workflows` → `jobs` → `assets` → `workers` (`SELECT … FOR UPDATE`).

### 5.1 `marathon/server/engine/flow.py`

```python
@dataclass(frozen=True)
class GraphModule:
    id: str
    name: str
    type_id: str
    settings: dict[str, Any]
    execution: str              # worker | engine | none
    has_input: bool
    outputs: tuple[str, ...]    # Ausgangs-IDs ohne data-Ausgänge

@dataclass(frozen=True)
class Graph:
    workflow_id: str
    version: int
    modules: dict[str, GraphModule]
    routes: dict[tuple[str, str], str]   # (source_module_id, output_id) -> target_module_id, ohne data-Ausgänge

def load_graph(connection, workflow_id: str) -> Graph | None
def asset_file_path(metadata: Mapping[str, Any]) -> str | None
def fail_asset(connection, asset_id: str, message: str) -> None
def route_asset(connection, asset_id: str, graph: Graph, from_module_id: str, output_id: str, keep_attempts: bool = False) -> None
def return_asset(connection, asset_id: str, graph: Graph) -> None
def enter_asset(connection, asset_id: str, graph: Graph) -> None
```

- `fail_asset`: `status='failed'`, `step_status=NULL`, `wait_until=NULL`; Historie (error) `message`; Log-Zeile (error, `job_id` NULL) am `current_module_id`.
- `route_asset`: Fehlt `from_module_id` im Graphen → `fail_asset("Modul nicht mehr im Workflow")`. Gibt es ein Ziel: `current_module_id=Ziel`, `step_status='waiting'`, `status='in_progress'`, `from_module_id`, `from_output_id=output_id`, `wait_until=NULL`, `attempts=0` (außer `keep_attempts`). Kein Ziel: `current_module_id=from_module_id`, `step_status=NULL`, `wait_until=NULL`; bei `output_id == "error"` Fehlgeschlagen mit Historie (error) „Fehlgeschlagen an „{modulname}““, sonst `done` mit Historie (success) „Workflow abgeschlossen“.
- `return_asset`: Ohne `from_module_id`: steht das Asset an einem Modul des Graphen → `waiting` dort (`wait_until=NULL`, Versuche bleiben); sonst `fail_asset("Modul nicht mehr im Workflow")`. Mit `from_module_id`: `route_asset(from_module_id, from_output_id, keep_attempts=(Ziel == current_module_id))`.
- `enter_asset` (nur `pending`): `status='in_progress'`, dann
  1. `current_module_id` ist ein Modul ohne Eingang im Graphen → `route_asset(current_module_id, "success")`;
  2. `current_module_id` gesetzt, aber nicht im Graphen → `fail_asset("Modul nicht mehr im Workflow")`;
  3. `current_module_id` ist ein Modul mit Eingang → `waiting` dort;
  4. NULL: Quellmodule = Module ohne Eingang mit Ausgang `success`. Genau eins → `route_asset(quelle, "success")`. Keins: Module mit Eingang ohne eingehende Route; genau eins → `waiting` dort (`from_module_id` NULL). Sonst `fail_asset("Startpunkt nicht eindeutig")`.

### 5.2 `marathon/server/engine/jobs.py`

```python
active_job_statuses = ("assigned", "running", "cancelling")

class JobNotFound(LookupError): ...
class JobWorkerMismatch(Exception): ...

def create_job(connection, asset_id: str, graph: Graph, worker_id: str, now: datetime, pickup_timeout_s: float) -> str
def start_job(connection, job_id: str, worker_id: str, now: datetime) -> bool
def apply_job_result(connection, job_id: str, worker_id: str, result: Mapping[str, Any], now: datetime, max_attempts: int) -> bool
def interrupt_job(connection, job_id: str, reason: str, now: datetime, max_attempts: int) -> None
def cancel_job(connection, job_id: str, reason: str, now: datetime) -> None
def drop_unstarted_job(connection, job_id: str, now: datetime) -> None
def request_pause(connection, workflow_id: str, now: datetime) -> bool
def remove_missing_modules(connection, workflow_id: str, now: datetime) -> None
def graph_impact(connection, workflow_id: str, module_ids: Iterable[str]) -> dict[str, Any]
```

- `create_job`: ID `job-<12 hex>`, `status='assigned'`, `attempt = attempts + 1`, `type_id`, `version=graph.version`, `settings` = Moduleinstellungen, `pickup_deadline = now + pickup_timeout_s`, `created_at = now`; Zeile in `job_assets`; Asset `step_status='running'`; Log (info) „Zugeteilt an {workername}“.
- `start_job`: `assigned` und passender Worker → `running`, `started_at=now`, True; sonst False. Unbekannt → `JobNotFound`.
- `apply_job_result(result)`: Schlüssel `outcome` (`result` | `error` | `cancelled`), `output` (str | None), `log` (list[str]), `asset_path` (str | None), `spawned` (list[{path, output, log}]), `error` (str | None). Unbekannt → `JobNotFound`, fremder Worker → `JobWorkerMismatch`. Endzustand → Log (warn) „Spätes Ergebnis verworfen“, False. `assigned` zählt wie `running`. Steht das Asset nicht `running` am Modul des Jobs, wird nur der Job abgeschlossen (Log warn „Asset passt nicht zum Job“). Sonst:
  - `cancelled`: bei `cancelling` → Job `cancelled`, `return_asset`; sonst `interrupt_job(reason="Worker beendet")`.
  - `error`: Job `failed`, `error`; Historie und Log (error) mit dem Fehlertext; hat das Modul den Ausgang `error` → `route_asset(..., "error")`, sonst `fail_asset(fehlertext)`.
  - `result`: `output` muss None oder ein Ausgang des Moduls sein, sonst wie `error` mit „Ungültiger Ausgang: {output}“. Job `succeeded`, `progress=100`, `output_id`, `job_assets.output_id`. Jede Log-Zeile → `log_lines` (info; error bei Ausgang `error`) und Historie (info; error bei Ausgang `error`). `asset_path` → `metadata["path"]`. Jedes `spawned`: neues Asset (ID `as-<12 hex>`, Name = Dateiname, `metadata={"path": path}`, `status='in_progress'`, `current_module_id` = Modul des Jobs, `list_position` am Ende), Historie (info) aus `spawned.log`, dann `route_asset(modul, spawned.output)`. Original: `output` None → `done` mit Historie (success) „Erledigt an „{modulname}““, sonst `route_asset(modul, output)`.
  - Immer `finished_at = now`. Rückgabe True.
- `interrupt_job`: Job `interrupted`, `error=reason`, `finished_at`; Unterbrechungsregel (4.2); Log (warn) mit demselben Text.
- `cancel_job`: `assigned` → `cancelled`, `error=reason`, `finished_at`, `return_asset`; `running` → `cancelling`, `error=reason`; sonst nichts.
- `drop_unstarted_job`: nur `assigned` → `cancelled`, `error="Nicht abgeholt"`, `finished_at`; Asset `waiting` am selben Modul.
- `request_pause`: Workflow fehlt → False. `active=false`, `scheduled_active=false`, `pausing=true`; jeder aktive Job → `cancel_job(reason="Workflow pausiert")`; Assets mit `wait_until` → `return_asset`; bleibt kein aktiver Job → `pausing=false`. True.
- `remove_missing_modules` (nach jedem Speichern): `in_progress`-Assets, deren `current_module_id` nicht im neuen Graphen ist: mit aktivem Job → `cancel_job(reason="Modul aus dem Workflow entfernt")`, ohne → `return_asset`.
- `graph_impact`: `{"removed_modules": [{"id", "name", "assets", "jobs"}], "assets": n, "jobs": n}` – Module der aktuellen Version, die in `module_ids` fehlen, mit ihren `in_progress`-Assets und aktiven Jobs; nur Module mit Assets werden gelistet.

## 6. Engine-Takt `marathon/server/engine/tick.py`

```python
def run_engine_tick(engine: Engine, now: datetime, config: EngineConfig) -> dict[str, int]
class EngineRunner:  # wie ScheduleRunner: start(), stop(timeout_s=5.0) -> bool, Thread "marathon-engine"
    def __init__(self, engine_provider: Callable[[], Engine], config: EngineConfig, clock: Callable[[], datetime] = _utc_now) -> None
```

Jeder Schritt in eigener Transaktion mit `lock_timeout 5s`, `statement_timeout 10s` und `pg_try_advisory_xact_lock(hashtext('marathon.engine'))` (nicht erhalten → Schritt überspringen). Ausnahme in einem Schritt: Warnung im Logger `marathon.engine`, nächster Schritt läuft. Reihenfolge:

1. **Ausfälle:** aktive Jobs, deren Worker `COALESCE(last_seen, created_at) < now - worker_timeout_s`: `assigned` → `drop_unstarted_job`, sonst `interrupt_job(reason="Worker antwortet nicht")`. Solche Worker mit Status `ready`/`busy` → `offline`.
2. **Abhol-Frist:** `assigned` mit `pickup_deadline < now` → `drop_unstarted_job`.
3. **Pausieren abschließen:** `pausing=false` für Workflows ohne aktive Jobs.
4. **Assets bewegen** (nur `active AND NOT pausing`, je Workflow höchstens 500 Assets je Art, nach `list_position, id`): `pending` → `enter_asset`; `waiting` an Engine-Modul `wait` → `running`, `wait_until = now + wait_s`; `waiting` an anderem Modul mit `execution != 'worker'` → `fail_asset("Modul kann nicht ausgeführt werden: {type_id}")`; Modul fehlt → `fail_asset("Modul nicht mehr im Workflow")`; `running` mit `wait_until <= now` → Historie (info) „Wartezeit abgelaufen“, `route_asset(modul, "success")`.
5. **Zuteilen:** Durchgänge über alle laufenden Workflows nach `last_assigned_at NULLS FIRST, id`; je Durchgang höchstens ein neuer Job je Workflow. Im Workflow das erste `waiting`-Asset (nach `list_position, id`, höchstens 200 geprüft) an einem Worker-Modul, für dessen Modultyp ein Worker mit freiem Platz das Angebot hat. Ohne Dateipfad → `fail_asset("Asset hat keine Datei")`. Kandidaten nach (Vorrang, aktive Jobs, Name, ID). Danach `last_assigned_at = now + k µs` (k zählt je Zuteilung im Takt hoch). Ende, wenn ein Durchgang nichts zuteilt.
6. **Beschäftigt:** `ready` ⇄ `busy` nach aktiven Jobs.

## 7. Worker-Schnittstelle (`/api/v1/worker/…`, Protokoll 1)

- Jede Anfrage braucht `Authorization: Bearer <token>`; sonst 401 „Worker-Token ungültig“. Token: `.data/worker.token`, beim Serverstart erzeugt, falls er fehlt (`secrets.token_urlsafe(32)`); `marathon/server/worker_auth.py` mit `load_or_create_token(path: Path = data_dir / "worker.token") -> str`. Vergleich mit `secrets.compare_digest`. Der Server-Worker bekommt ihn über die Umgebungsvariable `MARATHON_WORKER_TOKEN`.
- `create_app(..., engine_config: EngineConfig | None = None, worker_token: str | None = None)`: ohne `worker_token` wird er beim ersten Bedarf mit `load_or_create_token()` geladen.

| Endpunkt | Eingabe | Antwort |
|---|---|---|
| `POST /worker/register` | `worker_id` (`^[A-Za-z0-9_-]{1,64}$`), `name`, `client_version`, `capabilities: list[str]`, `max_parallel: int ≥ 1`, `last_choice: list[str] = []`, `protocol: int` | `{"worker_id", "status", "protocol": 1, "poll_timeout_s"}`; `protocol != 1` → 409 „Protokollversion nicht unterstützt“ |
| `POST /worker/poll` | `worker_id`, `running: list[str]` | `{"jobs": [...], "cancel": [...]}` |
| `POST /worker/jobs/{job_id}/start` | `worker_id` | 200 `{"ok": true}`; 409 „Job nicht mehr gültig“; 404 |
| `POST /worker/jobs/{job_id}/progress` | `worker_id`, `progress: 0–100`, `log: list[str] = []` | `{"cancel": bool}` (true bei `cancelling` oder Endzustand) |
| `POST /worker/jobs/{job_id}/result` | `worker_id`, Ergebnis wie `apply_job_result` | `{"accepted": bool}`; 404 unbekannt; 403 fremder Worker |

- **Anmelden:** neuer Worker → Zeile mit `status` `new` (`server-worker`: `ready`), `client_slots=max_parallel`, `max_parallel=max_parallel`, `priorities` mit `last_choice` auf `last`. Bekannter Worker: `client_version`, `capabilities`, `client_slots`, `ip_address`, `last_seen` aktualisieren (Name, `max_parallel`, `priorities` bleiben); `offline` → `ready`; alle aktiven Jobs des Workers: `assigned` → `drop_unstarted_job`, sonst `interrupt_job(reason="Worker neu gestartet")`.
- **Anfrage (Long-Poll):** unbekannter Worker → 404 „Worker unbekannt“. `last_seen` sofort und danach höchstens alle 5 s; `offline` → `ready`. `jobs` = alle `assigned`-Jobs des Workers als `{"job_id", "type_id", "settings", "asset": {"id", "name", "path"}}`; `cancel` = Jobs des Workers im Zustand `cancelling` plus IDs aus `running`, die für diesen Worker nicht `running`/`cancelling` sind. Sofortige Antwort, wenn eine Liste nicht leer ist; sonst Prüfung alle 0,5 s bis `poll_timeout_s` oder bis die Verbindung getrennt ist. Der Endpunkt ist `async`, Datenbankarbeit über `run_in_threadpool`.

## 8. Worker-Laufzeit `marathon/worker/`

```
python -m marathon.worker --server URL --worker-id ID --name NAME --max-parallel N [--last-choice a,b] [--exit-on-stdin-eof]
```

- `--server` ist die Basis-URL des Servers (z. B. `http://127.0.0.1:8080`); die Laufzeit hängt `/api/v1` an.
- Token aus `MARATHON_WORKER_TOKEN`; fehlt er: Meldung „MARATHON_WORKER_TOKEN fehlt“ auf stderr, Exit 2.
- `capabilities` = IDs aller Katalogeinträge mit `execution == "worker"`; `client_version` = `app_version`; `protocol` = 1.
- Nur Standardbibliothek (`urllib.request`, `json`, `threading`). Zeitlimits: 10 s je Anfrage, Long-Poll `poll_timeout_s + 10` s.
- Ablauf: Anmelden (Wiederholung nach 1, 2, 5, 10, 10 … s) → Anfrageschleife. Netzfehler oder 5xx bei der Anfrage: erneut nach derselben Wartefolge, ohne neue Anmeldung. 404 „Worker unbekannt“ → neu anmelden. 401 → Meldung „Worker-Token ungültig“, Exit 3.
- Neuer Job (ID noch unbekannt, freier Platz): `start`; bei 200 Modul in eigenem Thread mit eigenem `threading.Event`, sonst verwerfen. `cancel`-IDs setzen das Event des Jobs.
- Modulaufruf: `catalog_module(type_id).run(ModuleJob(asset_path, settings, stop))`. Ergebnis: `ModuleResult` → `outcome="result"` (`output`, `log`, `asset_path`, `spawned`); `JobStopped` → `outcome="cancelled"`; andere Ausnahme → `outcome="error"`, `error = str(e) or type(e).__name__`; Modultyp unbekannt → `error` „Modultyp unbekannt: {type_id}“.
- Ergebnis senden: Wiederholung alle 2 s bis Erfolg, 403/404 oder 120 s; beim Beenden höchstens ein Versuch.
- Beenden (SIGINT, SIGTERM, stdin-EOF bei `--exit-on-stdin-eof`): alle Job-Events setzen, höchstens 10 s auf Job-Threads warten, Exit 0. Der Long-Poll läuft in einem Daemon-Thread; Beenden wartet nicht auf ihn.

## 9. Server-Worker-Aufsicht `marathon/server/worker_supervisor.py`

```python
def server_url(host: str, port: int) -> str          # 0.0.0.0, ::, "" -> 127.0.0.1
def server_worker_command(url: str, config: ServerWorkerConfig) -> list[str]
class WorkerSupervisor:
    def __init__(self, command: list[str], env: Mapping[str, str], cwd: Path, log_file: Path,
                 backoff_s: Sequence[float] = (1, 2, 5, 10, 30), stable_s: float = 60.0,
                 graceful_timeout_s: float = 10.0, kill_timeout_s: float = 5.0) -> None
    def start(self) -> None
    def stop(self) -> bool
```

- Kommando: `[sys.executable, "-m", "marathon.worker", "--server", url, "--worker-id", "server-worker", "--name", "Server", "--max-parallel", str(n), "--exit-on-stdin-eof"]`, bei `last_choice` zusätzlich `["--last-choice", "a,b"]`.
- Kindprozess mit `stdin=PIPE`, Ausgabe angehängt an `log_file` (`.data/server-worker.log`); POSIX `start_new_session=True`, Windows `CREATE_NEW_PROCESS_GROUP`.
- Endet das Kind ungeplant oder startet es nicht: Warnung im Logger `marathon.supervisor`, Neustart nach Wartefolge; nach `stable_s` Laufzeit beginnt die Folge von vorn. Keine Ausnahme erreicht den Server.
- `stop()`: stdin schließen → `graceful_timeout_s` warten → `terminate` → `kill_timeout_s` → `kill`; True, wenn das Kind beendet ist.

## 10. Verwaltung

- `PUT /workflows/{id}/active` mit `active=false` → `request_pause`; Antwort enthält `pausing`. `active=true` während `pausing` → 409 „Workflow wird gerade pausiert“. Workflow-Datensätze (`list_workflows`, `get_workflow`) bekommen `pausing: bool`.
- `save_graph`: nach dem Speichern `remove_missing_modules`; Pausieren wegen fehlerhafter Einstellungen über `request_pause`.
- `POST /workflows/{id}/graph/impact` (Eingabe wie `PUT …/graph`) → `graph_impact` für die Modul-IDs der Eingabe; 404 bei unbekanntem Workflow.
- `reprocess_assets`: setzt zusätzlich `from_module_id`, `from_output_id`, `wait_until` auf NULL und `attempts` auf 0.
- Worker: `PUT /workers/{id}` (`name` 1–100 Zeichen, `max_parallel` 1–64, `priorities` {Modultyp: `preferred`|`normal`|`last`|`off`}), `POST /workers/{id}/approve`, `POST /workers/{id}/lock`; Antwort ist der Worker-Datensatz; 404 / 422. `list_workers` liefert zusätzlich `priorities`, `client_slots`; `running_jobs` zählt aktive Jobs.

## 11. Oberfläche

- Job-Status: `assigned` „zugeteilt“, `running` „läuft“, `cancelling` „wird abgebrochen“, `interrupted` „unterbrochen“.
- *Wird pausiert*: Status-Badge „Wird pausiert“, alle Play/Pause-Buttons zeigen „Wird pausiert“ und sind gesperrt; die Seite lädt jede Sekunde neu, solange ein angezeigter Workflow pausiert wird; dauert es länger als 1 s: Hinweis „Workflow wird gerade pausiert“.
- Speichern eines Workflows: vorher `…/graph/impact`; bei `assets > 0` Bestätigung im Seitenpanel „Änderung betrifft laufende Assets“ mit je Modul „{Name}: {n} Assets, {m} laufende Jobs werden abgebrochen“, Schaltflächen „Abbrechen“ und „Speichern“.
- Worker-Seite: Freigeben, Sperren, Bearbeiten (Name, Parallele Jobs, Vorrang je angebotenem Modultyp: Bevorzugt / Normal / Letzte Wahl / Aus); „Version ausrollen“ bleibt „Noch nicht verfügbar“.
