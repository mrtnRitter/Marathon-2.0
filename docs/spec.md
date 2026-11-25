# Marathon 2.0 – Architektur & verbindlicher Systemvertrag

Zentrale, widerspruchsfreie Spezifikation für Marathon 2.0. Begleitendes Release- und Backlog-Protokoll: `docs/mission_log.md`.

Bezeichner im Code englisch (`snake_case`), Texte für Nutzer deutsch und wörtlich wie hier.

- **Teil 1** hält die entschiedenen Architektur-Grundsätze fest.
- **Teil 2** beschreibt den **bereits umgesetzten** Systemvertrag für Kern, Engine und Worker-Steuerung (Stand Migration `0009`, Worker-Protokoll 2; Abnahme offen).
- **Teil 2a** beschreibt den Vertrag für den Release *HTTPS* (keine Migration, Worker-Protokoll unverändert).
- **Teil 3** beschreibt den **geplanten** Vertrag für die Releases *Zugang & Nutzer*, *Worker-Client & Steuerung*, *Datei- und Ordner-Dialog* und *Lokale Watchfolder & Rechnerwechsel* (Zugang & Nutzer: Migration `0010_users`, Worker-Protokoll unverändert; übrige Releases: eigene Migrationen, Worker-Protokoll 3; noch nicht umgesetzt). Offene Punkte und Entscheidungen je Release stehen in `docs/mission_log.md` und gehen bis zur Klärung vor; nach der Klärung werden sie hier eingearbeitet. Der vorgelagerte Release *HTTPS* steht in Teil 2a. Migration und Protokollnummer je Release werden bei der Klärung festgelegt. Wo Teil 3 Punkte aus Teil 2 erweitert oder ablöst, ist das markiert und gilt ab Umsetzung von Teil 3.

---

# Teil 1: Architektur & Grundsätze

## 1.1 Ziel, Rahmen & Leitplanken

- **Workflow-Engine als Webanwendung** im lokalen Netz, optional später öffentlich erreichbar.
- **Ein Server** liefert die Weboberfläche aus, steuert die Workflows und hält die PostgreSQL-Datenbank.
- **Worker-Rechner** führen einen kleinen installierten Worker-Client aus, der ausschließlich ausgehende Verbindungen zum Server aufbaut.
- **Node-Editor im Browser** (React Flow / xyflow) mit Bedienung und Oberfläche nach Vorbild FileFlows.
- **Eigener, schlanker Kern** ohne Übernahme von Altcode; interne Bezeichnung „m2“, im Code `marathon`, Startversion **2.0.0**.
- **Betriebssystem:** Start auf Windows 11 (Server und Worker), später Linux; der Code ist von Anfang an plattformunabhängig.
- **Leitprinzip Entkopplung (Nutzer vs. Worker):** Nutzerkonten (`users`) und Worker-Rechner (`workers`) sind streng voneinander getrennt. Nutzer erhalten Zugriff auf Workflows über ihren Account; Worker authentifizieren sich als eigenständige Geräte über ihr individuelles Worker-Token (Details in Teil 3).

**Leitplanken für Skalierbarkeit und Wartbarkeit**

- **Kein fachlicher Zustand im Server-RAM:** Alle fachlichen Zustände stehen in PostgreSQL (mehrere Server-Prozesse parallel möglich). Transiente Kanal-/Long-Poll-Mechanismen halten keinen fachlichen Dauerzustand.
- **Versionierte Schnittstellen:** Server, Worker-Client und Oberfläche sprechen nur über klar definierte, versionierte APIs (`/api/v1/…`, Worker-Protokollversion).
- **Schemaänderungen nur über Migrationen** (`marathon/server/db/migrations/versions/`).
- **Erweiterbarer Modulkatalog:** Neue Modultypen kommen unter `marathon/modules/` ohne Änderung am Engine-Kern dazu.
- **Konfiguration statt Hardcoding:** Einstellungen liegen in `config/marathon.toml` (bzw. in der Tabelle `settings` mit Config-Startwerten), Zugangsdaten niemals im Code oder in `marathon.toml`.

## 1.2 Kernidee & Begriffe

> Es gibt eine **Liste von Assets**. Die Assets durchlaufen einen **Workflow** aus **Modulen**.
> Damit ein Asset ein Modul durchläuft, erzeugt der Server einen **Job**. **Worker** holen sich Jobs ab, verarbeiten sie und melden Fortschritt, Logs und Ergebnis.
> Mit jedem Ergebnis ändert sich der **Status** des Assets auf der Liste, bis es das Ende des Workflows erreicht.

- **Der Server entscheidet, der Worker führt aus:** Der Worker kennt keinen Workflow, nur seinen Job.
- **Abholen statt Zuschieben:** Worker bauen ausschließlich ausgehende Verbindungen zum Server auf.
- **Asset-Neutralität:** Für den Kern ist ein Asset ein Listeneintrag mit Kennung, Name und frei belegbaren Metadaten; erst die Module interpretieren den Inhalt.

| Begriff | Bedeutung |
|---|---|
| **Workflow** | Versionierte Definition aus Modulen und ihren Verbindungen. Jeder Workflow hat genau eine Asset-Liste. |
| **Modultyp** | Baustein aus dem Katalog (`marathon/modules/`). Beschreibt Eingang, Ausgänge, Einstellungen und Ausführungsart (`worker`, `engine`, `none`). |
| **Modul** | Ein im Workflow platzierter Modultyp mit eigenen Einstellungen. |
| **Ausgang** | Benannter Ausgang eines Moduls (typischerweise *Erfolg* und *Fehler*). Verbindungen führen vom Ausgang zum nächsten Modul. |
| **Asset** | Eintrag auf der Liste eines Workflows mit Gesamtstatus, Schrittstatus, Position (aktuelles Modul), Metadaten und Historie. |
| **Job** | Arbeitsauftrag: ein Modul für ein oder mehrere Assets. Hat Versuche, Fortschritt und ein Ergebnis je Asset. |
| **Worker** | Rechner mit Worker-Laufzeit/-Client. Hat gemeldete Fähigkeiten (`capabilities`), konfigurierte Fähigkeiten-Rangliste (`skills`), Status, Verbindungsstatus und Client-Version. |
| **Historie** | Unveränderliche, angehängte Protokolleinträge zu Statuswechseln, Fehler- und Erfolgsmeldungen. |

## 1.3 Datenhaltung, Oberfläche, Installation & Auto-Update

- **Datenhaltung:** Eine **PostgreSQL**-Datenbank für alles. Der aktuelle Zustand wird direkt gespeichert; jede Zustandsänderung schreibt in derselben Transaktion die Historie/Logs mit.
- **Oberfläche (Vorbild FileFlows):**
  - Nur **Dark Theme** (vorerst kein helles Theme), zentrale Farben und Statusfarben (`ui/src/theme/`).
  - Durchgängige Monospace-Schrift (**JetBrains Mono**, OFL), lokal ausgeliefert.
  - Feste Seitenleiste (**Dashboard**, **Workflows**, **Assets**, **Worker**, **Einstellungen** – Sichtbarkeit ab Teil 3 rollenabhängig). Innerhalb eines Workflows Reiter **Module** (*Überwachen* / *Bearbeiten*) und **Assets**; Asset-Detail mit Kopfdaten, Metadaten, Zeitstrahl und Logs.
- **Installation & Abhängigkeiten:** Alles lokal im Projektordner über `build.py` (`.venv`, gepinnte Requirements `requirements/server.txt` und künftig `requirements/client.txt`, lokales PostgreSQL unter `.tools/postgres`, lokales Node.js unter `.tools/node` nur zum Bauen, gebaute Oberfläche unter `marathon/server/static`). Zur Laufzeit wird nichts aus dem Internet nachgeladen.
- **Architektur-Vorgabe Auto-Update (für späteren Rollout im Backlog):**
  - Aufbau des Worker-Clients aus einem kleinen, selten geänderten **Starter** und der austauschbaren **Client-Laufzeit**.
  - Rollout geht vom Server aus (Freigabe für alle oder ausgewählte Worker); Client erfährt per Lebenszeichen von neuen Versionen, lädt das Paket, prüft die Prüfsumme, lässt laufende Jobs auslaufen (Drain), tauscht die Laufzeit, startet neu und meldet die neue Version.
  - **Rückfall:** Startet die neue Version nicht sauber, kehrt der Starter zur vorherigen Version zurück und meldet den Fehler.
  - Server und Client tragen dieselbe Versionsnummer (Start `2.0.0`); Module sollen perspektivisch über denselben Weg verteilt werden.

**Referenzen (Vorbild & Bausteine):** FileFlows Webconsole-Dokumentation (Dashboard, Files, Flows, Flow-Editor, Agents, Updates), React Flow (`xyflow/xyflow`, MIT).

---

# Teil 2: Aktueller Systemvertrag – Kern, Engine & Worker-Steuerung (umgesetzt, Migration `0009`, Protokoll 2)

Widerspruchsfreie Zusammenfassung des aktuell umgesetzten Standes (Engine + Live-Kanal + Zeitplan-Wartemodus + Worker-Steuerung).

## 2.1 Konfiguration `config/marathon.toml`

Lader: `load_server_config()` in `marathon/server/config.py`. Enthält `host`, `port`, `[database]` sowie:

```toml
[engine]
tick_interval_s = 0.5   # Takt der Engine in Sekunden (0.05–10)
poll_timeout_s = 25     # So lange hält der Server die Anfrage eines Workers offen (1–60)
worker_timeout_s = 30   # Ohne Meldung gilt ein Worker danach als ausgefallen (10–600)
pickup_timeout_s = 10   # Abhol-Frist für zugeteilte Jobs (1–600)
max_attempts = 3        # Unterbrochene Versuche je Asset und Modul (1–10)

[server_worker]
enabled = true                                      # Server-Worker mit dem Server starten
skills = ["copy_file", "move_file", "rename_file"]  # Fähigkeiten ab Werk, oben = höchste Prio; danach gilt die Worker-Seite

[folder_dialog]
server_address = ""     # Standard für die Server-Adresse im Ordner-Dialog (Rechnername oder IP, z. B. 10.0.77.11); danach gilt der Wert aus der Oberfläche
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
    skills: tuple[str, ...] = ("copy_file", "move_file", "rename_file")

@dataclass(frozen=True)
class FolderDialogConfig:
    server_address: str = ""

# ServerConfig enthält zusätzlich:
#   engine: EngineConfig = field(default_factory=EngineConfig)
#   server_worker: ServerWorkerConfig = field(default_factory=ServerWorkerConfig)
#   folder_dialog: FolderDialogConfig = field(default_factory=FolderDialogConfig)
```

Validierung:
- Zahlen: `int` oder `float`, nie `bool`; `max_attempts` nur `int`.
- Unbekannte Schlüssel (u. a. früheres `last_choice` oder `[server_worker] max_parallel`) werden ignoriert. `host` bleibt rein in der Konfiguration und ist nicht in der Oberfläche änderbar.
- `server_address`: Text, leer oder `^[A-Za-z0-9._-]{1,253}$`; sonst `ConfigError` „[folder_dialog] server_address in {datei} muss ein Rechnername oder eine IP-Adresse sein“.
- `skills`: Liste von Texten, je `^[a-z0-9_]{1,64}$`, ohne Doppelte; sonst `ConfigError` „[server_worker] skills in {datei} muss eine Liste von Modultyp-IDs ohne Doppelte sein“.
- Übrige Fehler: `ConfigError` mit Abschnitt und Schlüssel, z. B. `[engine] poll_timeout_s in <datei> muss eine Zahl zwischen 1 und 60 sein`.

## 2.2 Datenbank (Stand Migration `0009`) & Seed

Aktueller Schema-Stand nach Migrationen `0001`–`0009` (`0005_engine.py`, `0006_live_notify.py`, `0007_schedule_waiting.py`, `0008_worker_release.py`, `0009_worker_cleanup.py`):

| Tabelle | Wesentliche Spalten & Constraints |
|---|---|
| `module_types` | `execution TEXT NOT NULL DEFAULT 'none'` (`CHECK execution IN ('worker','engine','none')`) |
| `workflows` | `pausing BOOLEAN NOT NULL DEFAULT false`, `last_assigned_at TIMESTAMPTZ NULL`, Zeitplan-Felder |
| `assets` | `from_module_id TEXT NULL`, `from_output_id TEXT NULL`, `attempts INTEGER NOT NULL DEFAULT 0` (`CHECK >= 0`), `wait_until TIMESTAMPTZ NULL` *(Erweiterung um `locality_worker_id` in Teil 3)* |
| `jobs` | `type_id TEXT NOT NULL DEFAULT ''`, `version INTEGER NULL`, `settings JSONB NOT NULL DEFAULT '{}'`, `pickup_deadline TIMESTAMPTZ NULL`, `error TEXT NULL`; `ck_jobs_status`: `status IN ('open','assigned','running','cancelling','succeeded','failed','cancelled','interrupted')`; Index `ix_jobs_status` |
| `job_assets` | `output_id TEXT NULL`, `outcome TEXT NULL` (`CHECK outcome IS NULL OR outcome IN ('result','error','cancelled','interrupted')`), `finished_at TIMESTAMPTZ NULL`, `error TEXT NULL` |
| `workers` | `skills JSONB NOT NULL DEFAULT '[]'`, `online BOOLEAN NOT NULL DEFAULT false`, `ck_workers_status`: `status IN ('ready','stopping','stopped')` (frühere Spalten `priorities`, `max_parallel`, `client_slots` sind mit `0009` entfernt; *Erweiterung um `draining`, `token_hash`, `approved`, `availability_config` usw. in Teil 3*) |
| `log_lines` | `asset_id TEXT NULL` (ohne Fremdschlüssel, damit Logs das Löschen eines Assets überleben), Index `ix_log_lines_asset_id` |
| `settings` | `key TEXT PRIMARY KEY`, `value JSONB NOT NULL`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` |

**Grundregeln:**
- Aktive Jobs: `active_job_statuses = ("assigned", "running", "cancelling")`. Ein Asset hat höchstens einen aktiven Job.
- Dateipfad eines Assets: `metadata["path"]`, sonst `metadata["source_path"]`; geändert wird immer `metadata["path"]`.

**Standardwerte (`marathon/server/db/seed.py`):**

```python
def seed_defaults(connection: Connection, config: ServerConfig) -> None
```

Aufruf in `python -m marathon.server.db upgrade` nach `upgrade_database()` in eigener Transaktion (also bei jedem `run.py`-Start). Schreibt nur fehlende Werte (`ON CONFLICT DO NOTHING`):
- `settings`: `folder_dialog.server_address` = `config.folder_dialog.server_address` (JSON-Text).
- `workers`: Zeile `server-worker`, Name „Server-Worker“, `status 'ready'`, `online false`, `skills` = `config.server_worker.skills`, `capabilities '[]'`, `client_version ''`.

## 2.3 Modulkatalog

- Optionaler Schlüssel im Deskriptor `MODULE["execution"]`: `"worker"` (läuft als Job auf einem Worker), `"engine"` (erledigt die Engine), `"none"` (wird nicht ausgeführt). Standard `"none"`; anderer Wert → `CatalogError`. `build_catalog()` gibt den Schlüssel immer mit aus; `sync_module_types()` schreibt ihn in `module_types.execution`.
- Ausgänge mit `"data": True` sind keine Asset-Wege (Prioliste `list`). Die Engine folgt ihnen nie und zählt sie nicht als eingehende Verbindung.
- Aktuelle Werte: `wait` → `engine`; `copy_file`, `move_file`, `rename_file` → `worker`; `watchfolder`, `priolist` → `none` *(in Teil 3 wechselt `watchfolder` auf `worker`)*. Prioliste-Ausgang `list` hat `"data": True`.
- `wait`: Wartezeit `settings["wait_s"]` (Standard 60).
- `copy_file` und `move_file`: Einstellung `{"key": "parallel", "label": "Parallele Verarbeitung", "type": "number", "default": 1, "min": 1, "max": 16, "help": "So viele Assets bearbeitet ein Job gleichzeitig, jedes in einem eigenen Strom"}`; `validate_settings`: ganze Zahl (kein bool) 1–16, sonst „Ganze Zahl von 1 bis 16“.

## 2.4 Zustände und Übergänge

### Asset

| `status` | `current_module_id` | `step_status` | Bedeutung |
|---|---|---|---|
| `pending` | Quellmodul oder NULL | NULL | wartet auf Eintritt in den Workflow |
| `in_progress` | Modul | `waiting` | steht an einem Modul, kein aktiver Job |
| `in_progress` | Modul | `running` | genau ein aktiver Job (Worker-Job oder Engine-Job `wait`) |
| `done` | letztes Modul | NULL | Erledigt |
| `failed` | Modul, an dem es endete | NULL | Fehlgeschlagen |
| `on_hold` | – | – | Engine fasst es nicht an |

Die Engine bewegt Assets nur in Workflows mit `active AND NOT pausing`. Ergebnisse laufender Jobs werden immer ausgewertet.

### Job

| Von | Ereignis | Nach | Asset |
|---|---|---|---|
| – | Zuteilung | `assigned` (Worker, `pickup_deadline`) | `running` |
| `assigned` | Worker meldet Start | `running` (`started_at`) | – |
| `assigned` | Abhol-Frist abgelaufen, Worker offline oder neu angemeldet | `cancelled`, Fehler „Nicht abgeholt“ | `waiting` am selben Modul, Versuche unverändert |
| `assigned` | Pausieren / Modul entfernt / Worker gestoppt | `cancelled` | zurück (`return_asset`) |
| `running` | Ergebnis | `succeeded` (`output_id`) | über den gemeldeten Ausgang weiter |
| `running` | Fehler im Modul | `failed` (`error`) | über Ausgang `error`, ohne diesen Ausgang Fehlgeschlagen |
| `running` | Pausieren / Modul entfernt / Worker gestoppt | `cancelling` | bleibt `running` |
| `running`, `cancelling` | Worker antwortet nicht, neu angemeldet oder meldet ungefragt Abbruch | `interrupted` | Unterbrechungsregel |
| `cancelling` | Ergebnis oder Fehler | `succeeded` / `failed` | wie bei `running` (fertiges Ergebnis wird übernommen) |
| `cancelling` | Abbruch bestätigt | `cancelled` | zurück (`return_asset`) |
| Endzustand | jede Meldung | unverändert | – (Log „Spätes Ergebnis verworfen“) |

**Unterbrechungsregel:** `attempts + 1`; Historie (error) „Versuch {n} an „{modul}“ unterbrochen: {grund}“. Ist `attempts >= max_attempts`: Fehlgeschlagen mit „Nach {n} unterbrochenen Versuchen fehlgeschlagen“, sonst `return_asset`.

### Worker (Stand Protokoll 2; Erweiterung um `draining` und Verfügbarkeit in Teil 3)

`status`: `ready` („bereit“), `stopping` („Wird gestoppt“), `stopped` („gestoppt“, rot). Davon getrennt `online` (`bool`, Anzeige „online“ / „offline“).

| Ereignis | Wirkung |
|---|---|
| erste Anmeldung | `status 'stopped'` (Worker-ID `server-worker`: `'ready'`), `skills` = gemeldete `capabilities` in gemeldeter Reihenfolge, `online true` |
| erneute Anmeldung | `capabilities`, `client_version`, `ip_address`, `last_seen`, `online true`; `status`, `name`, `skills` bleiben; aktive Jobs: `assigned` → `drop_unstarted_job`, sonst `interrupt_job("Worker neu gestartet")` |
| Anfrage (Long-Poll) | `last_seen`, `online true` |
| Engine: `COALESCE(last_seen, created_at) < now - worker_timeout_s` | aktive Jobs wie bei Ausfall (`assigned` → `drop_unstarted_job`, sonst `interrupt_job("Worker antwortet nicht")`); `online false`; `status` bleibt |
| `POST /workers/{id}/start` | `stopped` → `ready`; `ready` bleibt; `stopping` → 409 „Worker wird gerade gestoppt“ |
| `POST /workers/{id}/stop` | `ready` → `stopping`; jeder aktive Job → `cancel_job(reason="Worker gestoppt")`; kein aktiver Job → sofort `stopped`; `stopped`/`stopping` bleiben |
| Engine-Takt | `stopping` ohne aktiven Job → `stopped` |

Stoppen zählt nicht als Versuch: bestätigter Abbruch → `return_asset`. Ein fertiges Ergebnis während `cancelling` wird übernommen; unfertige Kopien entfernen die Module selbst.

### Workflow

| Zustand | `active` | `pausing` |
|---|---|---|
| Läuft | true | false |
| Wird pausiert | false | true |
| Pausiert | false | false |

Start während *Wird pausiert* → 409 „Workflow wird gerade pausiert“. *Wird pausiert* endet, sobald der Workflow keine aktiven Jobs mehr hat.

## 2.5 Engine-Funktionen & Engine-Takt

Alle Funktionen laufen in der Transaktion des Aufrufers. Sperrreihenfolge: `workflows` → `jobs` → `assets` → `workers` (`SELECT … FOR UPDATE`).

### `marathon/server/engine/flow.py`

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

- `fail_asset`: `status='failed'`, `step_status=NULL`, `wait_until=NULL`; Historie (error) `message`; Log-Zeile (error, `job_id` NULL, mit `asset_id`) am `current_module_id`.
- `route_asset`: Fehlt `from_module_id` im Graphen → `fail_asset("Modul nicht mehr im Workflow")`. Gibt es ein Ziel: `current_module_id=Ziel`, `step_status='waiting'`, `status='in_progress'`, `from_module_id`, `from_output_id=output_id`, `wait_until=NULL`, `attempts=0` (außer `keep_attempts`). Kein Ziel: `current_module_id=from_module_id`, `step_status=NULL`, `wait_until=NULL`; bei `output_id == "error"` Fehlgeschlagen mit Historie (error) „Fehlgeschlagen an „{modulname}““, sonst `done` mit Historie (success) „Workflow abgeschlossen“.
- `return_asset`: Ohne `from_module_id`: steht das Asset an einem Modul des Graphen → `waiting` dort (`wait_until=NULL`, Versuche bleiben); sonst `fail_asset("Modul nicht mehr im Workflow")`. Mit `from_module_id`: `route_asset(from_module_id, from_output_id, keep_attempts=(Ziel == current_module_id))`.
- `enter_asset` (nur `pending`): `status='in_progress'`, dann
  1. `current_module_id` ist ein Modul ohne Eingang im Graphen → `route_asset(current_module_id, "success")`;
  2. `current_module_id` gesetzt, aber nicht im Graphen → `fail_asset("Modul nicht mehr im Workflow")`;
  3. `current_module_id` ist ein Modul mit Eingang → `waiting` dort;
  4. NULL: Quellmodule = Module ohne Eingang mit Ausgang `success`. Genau eins → `route_asset(quelle, "success")`. Keins: Module mit Eingang ohne eingehende Route; genau eins → `waiting` dort (`from_module_id` NULL). Sonst `fail_asset("Startpunkt nicht eindeutig")`.

### `marathon/server/engine/jobs.py` (Jobs mit einem oder mehreren Assets & Warten als Engine-Job)

```python
active_job_statuses = ("assigned", "running", "cancelling")

class JobNotFound(LookupError): ...
class JobWorkerMismatch(Exception): ...

def create_batch_job(connection, asset_ids: Sequence[str], graph: Graph, worker_id: str, now: datetime, pickup_timeout_s: float) -> str
def create_job(connection, asset_id: str, graph: Graph, worker_id: str, now: datetime, pickup_timeout_s: float) -> str  # = create_batch_job([asset_id], ...)
def create_engine_job(connection, asset_id: str, graph: Graph, now: datetime) -> str
def apply_asset_result(connection, job_id: str, asset_id: str, worker_id: str | None, result: Mapping[str, Any], now: datetime, max_attempts: int) -> bool
def apply_job_result(connection, job_id: str, worker_id: str | None, result: Mapping[str, Any], now: datetime, max_attempts: int) -> bool
def start_job(connection, job_id: str, worker_id: str, now: datetime) -> bool
def interrupt_job(connection, job_id: str, reason: str, now: datetime, max_attempts: int) -> None
def cancel_job(connection, job_id: str, reason: str, now: datetime) -> None
def drop_unstarted_job(connection, job_id: str, now: datetime) -> None
def request_pause(connection, workflow_id: str, now: datetime) -> bool
def remove_missing_modules(connection, workflow_id: str, now: datetime) -> None
def graph_impact(connection, workflow_id: str, module_ids: Iterable[str]) -> dict[str, Any]
```

- `create_batch_job`: alle Assets `in_progress`/`waiting` am selben Modul des Graphen, keines mit aktivem Job, mindestens eins (sonst `ValueError`). Job `job-<12 hex>`, `status='assigned'`, `pickup_deadline = now + pickup_timeout_s`, `type_id`, `version=graph.version`, `settings`, `attempt` = größtes `attempts` der Assets + 1, `created_at = now`; je Asset eine Zeile in `job_assets`, Asset `step_status='running'`, Log (info, mit `asset_id`) „Zugeteilt an {workername}“.
- `start_job`: `assigned` und passender Worker → `running`, `started_at=now`, `True`; sonst `False`. Unbekannt → `JobNotFound`.
- `apply_asset_result`: Schlüssel von `result`: `outcome` (`result` | `error` | `cancelled`), `output` (`str | None`), `log` (`list[str]`), `asset_path` (`str | None`), `spawned` (`list[{path, output, log}]`), `error` (`str | None`).
  - Job unbekannt oder Asset nicht im Job → `JobNotFound`; `worker_id` ≠ Worker des Jobs → `JobWorkerMismatch`.
  - Job im Endzustand oder `job_assets.outcome` dieses Assets schon gesetzt → Log (warn, `asset_id`) „Spätes Ergebnis verworfen“, `False`. `assigned` zählt wie `running`.
  - Asset steht nicht `running` am Modul des Jobs → nur `job_assets` abschließen, Log (warn) „Asset passt nicht zum Job“.
  - `cancelled`: bei Job `cancelling` → `outcome 'cancelled'`, `return_asset`; sonst Unterbrechungsregel für dieses Asset mit Grund „Worker beendet“, `outcome 'interrupted'`.
  - `error`: `outcome 'error'`, `job_assets.error`; Historie und Log (error) mit Fehlertext; hat das Modul den Ausgang `error` → `route_asset(..., "error")`, sonst `fail_asset(fehlertext)`.
  - `result`: ungültiger Ausgang → wie `error` mit „Ungültiger Ausgang: {output}“. Sonst `outcome 'result'`, `job_assets.output_id`. Jede Log-Zeile → `log_lines` (info; error bei Ausgang `error`, mit `asset_id`) und Historie. `asset_path` → `metadata["path"]`. Jedes `spawned`: neues Asset (`as-<12 hex>`, Name = Dateiname, `metadata={"path": path}`, `status='in_progress'`, `current_module_id` = Modul des Jobs, `list_position` am Ende), Historie (info) aus `spawned.log`, dann `route_asset(modul, spawned.output)`. Original: `output` None → `done` mit Historie (success) „Erledigt an „{modulname}““, sonst `route_asset(modul, output)`.
  - Immer `job_assets.finished_at = now`, danach Log (info bzw. error, `asset_id`) „Ergebnis: {label}“ (`label` = Beschriftung des Ausgangs aus `module_types.outputs`, „Erledigt“ bei `output` None, „Abgebrochen“ bei `cancelled`, „Fehler“ bei `error`). Rückgabe `True`.
  - Job-Abschluss: Hat jedes Asset ein `outcome`, endet der Job: `cancelled` (wenn `cancelling` und mind. ein Asset `cancelled`), sonst `interrupted` (wenn mind. ein Asset `interrupted`), sonst `failed` (`error` = erster Fehlertext, wenn mind. ein Asset `error`), sonst `succeeded`. `output_id` = gemeinsamer Ausgang aller Assets, sonst NULL; `progress=100`; `finished_at=now`. Solange Assets offen sind: `progress` = gerundeter Anteil abgeschlossener Assets in %.
- `apply_job_result`: wendet dasselbe Ergebnis in `asset_id`-Reihenfolge auf jedes offene Asset des Jobs an; Endzustand → Log (warn) „Spätes Ergebnis verworfen“, `False`.
- `interrupt_job`: alle offenen Assets → `outcome 'interrupted'` + Unterbrechungsregel + Log (warn).
- `drop_unstarted_job`: nur `assigned` → `cancelled`, `error="Nicht abgeholt"`, `finished_at`, alle Assets `outcome 'cancelled'` und `waiting` am selben Modul.
- `cancel_job`: `assigned` → alle `cancelled` + `return_asset`; `running` → `cancelling`. Engine-Job (`worker_id` NULL): sofort `cancelled`, alle offenen Assets `outcome 'cancelled'`, `return_asset` (setzt `wait_until` NULL).
- `request_pause`: Workflow fehlt → `False`. `active=false`, `scheduled_active=false`, `pausing=true`; jeder aktive Job → `cancel_job(reason="Workflow pausiert")`; Assets mit `wait_until` → `return_asset`; kein aktiver Job übrig → `pausing=false`. `True`.
- `remove_missing_modules` (nach jedem Speichern): `in_progress`-Assets, deren `current_module_id` im neuen Graphen fehlt: mit aktivem Job → `cancel_job(reason="Modul aus dem Workflow entfernt")`, ohne → `return_asset`.
- `graph_impact`: `{"removed_modules": [{"id", "name", "assets", "jobs"}], "assets": n, "jobs": n}` – nur entfernte Module mit `in_progress`-Assets.
- **Warten als Engine-Job (`create_engine_job`):** Asset `waiting` an Modul `wait` → Job `running`, `worker_id` NULL, `started_at=now`, `attempt = attempts + 1`, `type_id='wait'`, `version`, `settings`; Zeile in `job_assets`; Asset `step_status='running'`, `wait_until = now + wait_s` (Standard 60); Log (info, `asset_id`) „Wartezeit gestartet: {wait_s:g} s“. Bei `wait_until <= now`: `apply_asset_result(..., worker_id=None, result={"outcome": "result", "output": "success", "log": ["Wartezeit abgelaufen"]})`.

### Engine-Takt `marathon/server/engine/tick.py`

```python
def run_engine_tick(engine: Engine, now: datetime, config: EngineConfig) -> dict[str, int]
class EngineRunner:  # start(), stop(timeout_s=5.0) -> bool, Thread "marathon-engine"
    def __init__(self, engine_provider: Callable[[], Engine], config: EngineConfig, clock: Callable[[], datetime] = _utc_now) -> None
```

Jeder Schritt in eigener Transaktion mit `lock_timeout 5s`, `statement_timeout 10s` und `pg_try_advisory_xact_lock(hashtext('marathon.engine'))` (nicht erhalten → Schritt überspringen). Ausnahme in einem Schritt: Warnung im Logger `marathon.engine`, nächster Schritt läuft. Reihenfolge:

1. **Ausfälle:** aktive Jobs, deren Worker `COALESCE(last_seen, created_at) < now - worker_timeout_s`: `assigned` → `drop_unstarted_job`, sonst `interrupt_job(reason="Worker antwortet nicht")`; solche Worker → `online false` (`status` bleibt).
2. **Abhol-Frist:** `assigned` mit `pickup_deadline < now` → `drop_unstarted_job`.
3. **Pausieren abschließen:** `pausing=false` für Workflows ohne aktive Jobs.
4. **Worker-Stopp abschließen:** `stopping` ohne aktiven Job → `stopped` *(in Teil 3 analog für `draining` → `stopped`)*.
5. **Assets bewegen** (nur `active AND NOT pausing`, je Workflow max. 500 Assets je Art, nach `list_position, id`): `pending` → `enter_asset`; `waiting` an `wait` → `create_engine_job`; `waiting` an anderem Modul mit `execution != 'worker'` → `fail_asset("Modul kann nicht ausgeführt werden: {type_id}")`; Modul fehlt → `fail_asset("Modul nicht mehr im Workflow")`; `running` mit `wait_until <= now` → Engine-Job abschließen.
6. **Zuteilen:**
   - Ein Worker hat höchstens **einen** aktiven Job. Kandidaten: `status = 'ready' AND online` und kein aktiver Job *(in Teil 3 zusätzlich `approved AND client_available` sowie Sticky-Locality-Filter)*.
   - Angebot = `skills`, gefiltert auf `capabilities`, in Reihenfolge von `skills`. Rang für Modultyp T = Position im Angebot (0 = oben); nicht enthalten = kein Kandidat.
   - Fairness: Durchgänge über laufende Workflows nach `last_assigned_at NULLS FIRST, id`, je Durchgang höchstens ein neuer Job je Workflow. Im Workflow das erste `waiting`-Asset (nach `list_position, id`, max. 200 geprüft) an einem Worker-Modul mit passendem Kandidaten. Bester Kandidat = kleinster `(Rang, Name, ID)`.
   - Losgröße = Moduleinstellung `parallel` (1–16), sonst 1; bündelt weitere `waiting`-Assets desselben Moduls in Listenreihenfolge. Assets ohne Dateipfad → `fail_asset("Asset hat keine Datei")`.
   - Danach `last_assigned_at = now + k µs` (k zählt je Zuteilung im Takt hoch). Ende, wenn ein Durchgang nichts zuteilt.

## 2.6 Worker-Schnittstelle (Protokoll 2), Worker-Laufzeit & Server-Worker-Aufsicht

### Worker-Schnittstelle (`/api/v1/worker/…`, Protokoll 2; Erweiterung auf Protokoll 3 in Teil 3)

- Auth: `Authorization: Bearer <token>`, sonst 401 „Worker-Token ungültig“. Token in `.data/worker.token` (`marathon/server/worker_auth.py`: `load_or_create_token`, Vergleich mit `secrets.compare_digest`; Server-Worker erhält ihn über `MARATHON_WORKER_TOKEN`). *Ab Teil 3: individuelle Worker-Tokens je Rechner.*
- `create_app(..., engine_config: EngineConfig | None = None, worker_token: str | None = None)`.

| Endpunkt | Eingabe | Antwort |
|---|---|---|
| `POST /worker/register` | `worker_id` (`^[A-Za-z0-9_-]{1,64}$`), `name`, `client_version`, `capabilities: list[str]`, `protocol: 2` (unbekannte Felder wie `max_parallel` ignoriert) | `{"worker_id", "status", "protocol": 2, "poll_timeout_s"}`; `protocol != 2` → 409 „Protokollversion nicht unterstützt“ |
| `POST /worker/poll` | `worker_id`, `running: list[str]` | `{"jobs": [{"job_id", "type_id", "settings", "assets": [{"id", "name", "path"}, …]}], "cancel": [...]}` (Assets nach `id`) |
| `POST /worker/jobs/{job_id}/start` | `worker_id` | 200 `{"ok": true}`; 409 „Job nicht mehr gültig“; 404 |
| `POST /worker/jobs/{job_id}/progress` | `worker_id`, `progress: 0–100`, `log: list[str] = []` | `{"cancel": bool}` (`true` bei `cancelling` oder Endzustand) |
| `POST /worker/jobs/{job_id}/assets/{asset_id}/result` | `worker_id`, Ergebnis wie `apply_asset_result` | `{"accepted": bool}`; 404 „Job nicht gefunden“ (auch Asset nicht im Job); 403 „Job gehört einem anderen Worker“ |
| `POST /worker/jobs/{job_id}/result` | `worker_id`, Ergebnis wie `apply_job_result` | `{"accepted": bool}`; 404; 403 |

- **Long-Poll:** Unbekannter Worker → 404 „Worker unbekannt“. `last_seen` sofort und danach höchstens alle 5 s; `online true`. Sofortige Antwort bei nicht leerem `jobs`/`cancel`, sonst Prüfung alle 0,5 s bis `poll_timeout_s` oder Verbindungsende (`async`, DB via `run_in_threadpool`).
- **Server-Stopp:** `app.state.shutdown_event` (`threading.Event`) wird bei Uvicorn-Shutdown gesetzt; offene Long-Polls antworten innerhalb von 0,5 s mit `{"jobs": [], "cancel": []}` ohne Ausnahme/Traceback.

### Worker-Laufzeit (`marathon/worker/`)

```bash
python -m marathon.worker --server URL --worker-id ID --name NAME [--exit-on-stdin-eof]
```

- `--server`: Basis-URL (Laufzeit hängt `/api/v1` an). `MARATHON_WORKER_TOKEN` fehlt → stderr „MARATHON_WORKER_TOKEN fehlt“, Exit 2.
- `capabilities` = IDs aller Katalogeinträge mit `execution == "worker"`; `client_version` = `app_version`; `protocol` = 2.
- Nur Standardbibliothek (`urllib.request`, `json`, `threading`). Timeouts: 10 s je Anfrage, Long-Poll `poll_timeout_s + 10` s.
- Ablauf: Anmelden (Backoff 1, 2, 5, 10, 10 … s) → Poll-Schleife. Netzfehler/5xx: Backoff ohne neue Anmeldung. 404 „Worker unbekannt“ → neu anmelden. 401 → „Worker-Token ungültig“, Exit 3.
- Höchstens ein Job gleichzeitig. Nach `start` (200) läuft je Asset ein eigener Thread mit `catalog_module(type_id).run(ModuleJob(asset_path, settings, stop))` und gemeinsamem `threading.Event`. Ergebnis je Asset an `/worker/jobs/{job_id}/assets/{asset_id}/result` (Retry alle 2 s bis Erfolg, 403/404 oder 120 s; beim Beenden max. 1 Versuch). `ModuleResult` → `result`, `JobStopped` → `cancelled`, andere Ausnahme → `error`, unbekannter Modultyp → „Modultyp unbekannt: {type_id}“.
- Beenden (SIGINT, SIGTERM, stdin-EOF bei `--exit-on-stdin-eof`): Event setzen, max. 10 s auf alle Asset-Threads warten, Exit 0.

### Server-Worker-Aufsicht (`marathon/server/worker_supervisor.py`)

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

- Kommando: `[sys.executable, "-m", "marathon.worker", "--server", url, "--worker-id", "server-worker", "--name", "Server-Worker", "--exit-on-stdin-eof"]`.
- Kindprozess mit `stdin=PIPE`, Ausgabe angehängt an `.data/server-worker.log`; POSIX `start_new_session=True`, Windows `CREATE_NEW_PROCESS_GROUP`.
- Ungeplantes Ende: Warnung im Logger `marathon.supervisor`, Neustart nach Backoff; nach `stable_s` stabiler Laufzeit Neustart der Folge. `stop()`: stdin schließen → `graceful_timeout_s` warten → `terminate` → `kill_timeout_s` → `kill`.

## 2.7 Verwaltung, Einstellungen & Oberfläche (aktueller Stand)

- **Workflows:**
  - `PUT /workflows/{id}/active` mit `active=false` → `request_pause`; Antwort enthält `pausing`. `active=true` während `pausing` → 409 „Workflow wird gerade pausiert“.
  - `save_graph`: nach Speichern `remove_missing_modules`; Pausieren wegen fehlerhafter Einstellungen über `request_pause`.
  - `POST /workflows/{id}/graph/impact` → `graph_impact` für die Modul-IDs der Eingabe (404 bei unbekanntem Workflow). Bei `assets > 0` Bestätigung im Seitenpanel „Änderung betrifft laufende Assets“ mit je Modul „{Name}: {n} Assets, {m} laufende Jobs werden abgebrochen“, Schaltflächen „Abbrechen“ und „Speichern“.
  - `reprocess_assets`: setzt `from_module_id`, `from_output_id`, `wait_until` auf NULL, `attempts` auf 0; keine Obergrenze für die Anzahl der Assets.
- **Worker-Verwaltung:**
  - `PUT /workers/{id}` mit `{"name": str, "skills": list[str]}`: `name` 1–100 Zeichen nach `strip()` („Name fehlt“, „Name ist zu lang (max. 100 Zeichen)“); jede Fähigkeit `^[a-z0-9_]{1,64}$` („Ungültiger Modultyp: {id}“), ohne Doppelte („Fähigkeit doppelt: {id}“), in `capabilities` des Workers („Fähigkeit wird vom Rechner nicht gemeldet: {id}“) → sonst 422; 404 „Worker nicht gefunden“.
  - `POST /workers/{id}/start`, `POST /workers/{id}/stop` *(in Teil 3 zusätzlich `/stop-now` und Trennung Drain vs. Sofort-Stopp)*.
  - Worker-Datensatz: `id`, `name`, `status`, `online`, `capabilities`, `skills`, `running_jobs`, `client_version`, `last_seen` (`COALESCE(last_seen, created_at)`), `ip_address`.
- **Einstellungen (`GET/PUT /api/v1/settings`):**
  - Form: `{"folder_dialog": {"server_address": str}}` (fehlt der Datensatz: `""`). Validierung: leer oder `^[A-Za-z0-9._-]{1,253}$`, sonst 422 „Server-Adresse: nur Rechnername oder IP-Adresse, z. B. 10.0.77.11“. Upsert mit `updated_at`.
  - Einstellungsseite zeigt nur echte Einstellungen (Abschnitt „Ordner-Dialog“, Feld „Server-Adresse“, Meldung „Gespeichert“). *(Erweiterung um `auth.allowed_domains` in Teil 3, §3.3.)*
  - Ordner-Dialog: Umschalter „Laufwerke“ | „Server“ (`\\<Server-Adresse>`, aktiv solange Pfad mit `\\` beginnt; ohne Adresse: „Keine Server-Adresse eingestellt (Einstellungen)“). *(Ausbau zum `FileBrowserDialog` in Teil 3.)*
- **Oberfläche & Überwachen:**
  - Job-Status: `assigned` „zugeteilt“, `running` „läuft“, `cancelling` „wird abgebrochen“, `interrupted` „unterbrochen“.
  - *Wird pausiert*: Status-Badge „Wird pausiert“, Play/Pause-Buttons gesperrt; bei Dauer > 1 s Hinweis „Workflow wird gerade pausiert“.
  - Zähler „erledigt“ je Modul = Anzahl `job_assets` von Jobs dieses Moduls mit `outcome 'result'` und `output_id IS DISTINCT FROM 'error'`. Warten-Modul zeigt aktuell „läuft“, „erledigt“, „Fehler“ *(Spalte „wartet“ kommt in Teil 3 zurück)*.
  - Modul-Log (Reiter „Logs“): neueste 500 Einträge des Moduls (inkl. Jobs) außer Stufe Info, also nur Warnungen und Fehler, aufsteigend nach Zeit: Zeit, Stufe, Asset (Name oder –), Worker (Name oder –), Text. Ohne solche Einträge: „Keine Vorkommnisse“. Die Liste ist so hoch wie das Seitenpanel. *(Release Zugang & Nutzer)*
  - Asset-Zeitleiste: Ausgang je Asset aus `job_assets.output_id`.
  - Prioliste: Menüpunkt „Bearbeiten“. Überwachen → Einstellungen: Werte ausgegraut (nur lesen). Verbindungslinien beginnen/enden am äußeren Rand der Kreise, auch beim Ziehen.
  - Worker-Seite: Starten/Stoppen in Liste und Kopfleiste, Status/Verbindung/Fähigkeiten/Jobs/Client-Version/Zuletzt gesehen, Kacheln (bereit, Wird gestoppt, gestoppt, offline), Bearbeiten (Name + sortierbare Fähigkeiten-Liste mit Hilfetext „Weiter oben = höhere Priorität. Nicht in der Liste = übernimmt diesen Modultyp nicht.“), „Version ausrollen“ weiterhin „Noch nicht verfügbar“.

---

# Teil 2a: Release HTTPS (keine Migration, Protokoll unverändert)

Marathon ist nur über `https://` erreichbar. Marathon selbst spricht nur HTTP und liegt immer hinter einem Proxy: ab Werk der mitgelieferte Caddy, wahlweise ein externer Reverse-Proxy. Marathon enthält keinen eigenen TLS-Code.

## 2a.1 Aufbau

- **Marathon** lauscht auf `host`/`port` aus `config/marathon.toml` (ab Werk `127.0.0.1:8080`), nur HTTP. Für einen externen Proxy auf einem anderen Rechner wird `host` auf eine Netzwerkadresse gesetzt.
- **Mitgelieferter Proxy:** Caddy `2.11.9` in `.tools/caddy` (`caddy.exe` unter Windows, sonst `caddy`), eingerichtet von `build.py` mit fester Prüfsumme. Der Server startet ihn beim Start als Kindprozess und überwacht ihn wie den Server-Worker (Neustart mit Wartezeit 1, 2, 5, 10, 30 s; Ausgaben `.data/caddy.log`); beim Beenden des Servers wird er beendet.
  - Konfiguration `.cert/Caddyfile`, bei jedem Serverstart aus den Einstellungen neu geschrieben; Daten (CA, Zertifikate) in `.cert/caddy`.
  - Lauscht nur auf dem TCP-Port aus `public_url` (ab Werk 443) mit HTTP/1.1 und HTTP/2; kein Port 80, kein HTTP/3, keine Admin-Schnittstelle, keine automatische Installation des Stammzertifikats.
  - Zertifikat für den Rechnernamen aus `public_url` aus Caddys interner CA (`tls internal`); Caddy erneuert es selbstständig. Andere Rechnernamen erhalten kein Zertifikat (Verbindungsfehler statt Umleitung).
  - Leitet an `127.0.0.1:<port>` weiter (bei `host` `0.0.0.0`/`::`/leer) bzw. an `host:port`, ohne Pufferung (Live-Kanal, Long-Poll).
- **Externer Reverse-Proxy:** mitgelieferter Proxy aus; der externe Proxy terminiert TLS, setzt `X-Forwarded-Proto` (und optional `X-Forwarded-Host`, `X-Forwarded-For`) und leitet an Marathon weiter. Er muss Anfragen länger als `poll_timeout_s` offen halten und den Live-Kanal ungepuffert durchreichen.

Caddyfile (Werte in spitzen Klammern; Port-Zeile mit dem Port aus `public_url`):

```text
{
	admin off
	skip_install_trust
	auto_https disable_redirects
	storage file_system {
		root "<.cert/caddy als absoluter Pfad mit />"
	}
	https_port <port>
	servers {
		protocols h1 h2
	}
}

<public_url> {
	tls internal
	reverse_proxy <upstream> {
		flush_interval -1
	}
}
```

## 2a.2 Konfiguration und Einstellungen

```toml
[proxy]
public_url = "https://laptop-martin-r"  # Adresse, unter der Marathon erreichbar ist (nur https://, ohne Pfad)
builtin = true                          # Mitgelieferten Proxy (Caddy) starten; false = externer Reverse-Proxy
trusted_proxies = []                    # IP-Adressen externer Reverse-Proxys; bei builtin = true gilt 127.0.0.1 automatisch
```

- Standardwerte ohne Abschnitt: `public_url = "https://localhost"`, `builtin = true`, `trusted_proxies = []`.
- **Startwerte:** Beim Start (`python -m marathon.server.db upgrade`) werden die Schlüssel `proxy.public_url`, `proxy.builtin` und `proxy.trusted_proxies` in `settings` angelegt, sofern noch kein Eintrag existiert; danach gilt die Datenbank.
- **Wirksamkeit:** Der Server liest die drei Werte beim Start; Änderungen gelten nach einem Neustart des Servers.
- **Prüfregeln** (gleich für toml und Einstellungen):
  - `public_url`: Schema `https`, Rechnername aus `A–Z a–z 0–9 . -` (1–253 Zeichen), optional Port 1–65535, ohne Benutzer, Pfad (außer einem abschließenden `/`), Query oder Fragment. Gespeichert wird `https://<rechnername in Kleinbuchstaben>[:<port>]`, Port 443 entfällt. Fehler: „Öffentliche Adresse: nur https://-Adresse ohne Pfad, z. B. https://laptop-martin-r“
  - `trusted_proxies`: Liste von IPv4-/IPv6-Adressen (ohne Netzmasken), Leerraum entfernt, Duplikate entfernt (Reihenfolge bleibt). Fehler: „Proxy-Adressen: nur IP-Adressen, z. B. 10.0.77.20“
  - `builtin = false` mit leerer `trusted_proxies`: „Proxy-Adressen: mindestens eine Adresse eintragen, wenn der mitgelieferte Proxy aus ist“
  - toml-Fehler als `ConfigError` mit Präfix `[proxy] <schlüssel> in <datei>: `; `builtin` kein Wahrheitswert: „[proxy] builtin in <datei> muss true oder false sein“; Abschnitt keine Tabelle: „[proxy] in <datei> muss eine Tabelle sein“.
- **API** `GET/PUT /api/v1/settings`: `{ "folder_dialog": { "server_address": "" }, "proxy": { "public_url": "https://laptop-martin-r", "builtin": true, "trusted_proxies": [] } }`. `PUT` verlangt beide Abschnitte, prüft alle Werte vor dem Speichern und antwortet mit den gespeicherten (normalisierten) Werten; Prüffehler `422` mit `detail` = Fehlertext, nichts wird gespeichert. Ohne Eintrag liefert `GET` die Standardwerte.
- **Oberfläche** (Einstellungen, Abschnitt „Proxy & HTTPS“ nach „Ordner-Dialog“):
  - „Öffentliche Adresse“ (Platzhalter `https://laptop-martin-r`; Hilfe „Adresse, unter der Marathon erreichbar ist (nur https://, ohne Pfad)“)
  - Schalter „Proxy verwenden“ ohne Hilfetext: aus = mitgelieferter Proxy (Caddy), an = externer Reverse-Proxy; zeigt den umgekehrten Wert von `builtin`
  - „Proxy“ ohne Hilfetext: IP-Adressen externer Proxys, durch Komma getrennt (Platzhalter `0.0.0.0`); nur bei eingeschaltetem Schalter bearbeitbar
  - Hinweis „Änderungen in diesem Abschnitt gelten nach einem Neustart des Servers.“

## 2a.3 Weiterleitungsangaben und Umleitung

Gilt für jede HTTP-Anfrage an Marathon, sobald der Server seine Proxy-Einstellungen geladen hat:

1. **Vertrauenswürdige Absender:** `trusted_proxies`, bei `builtin = true` zusätzlich `127.0.0.1` und `::1`.
2. **Absender vertrauenswürdig und `X-Forwarded-Proto` vorhanden:** Schema = erster Wert von `X-Forwarded-Proto`; Rechnername = erster Wert von `X-Forwarded-Host`, sonst `Host`; Client-Adresse = erster gültiger Wert von `X-Forwarded-For`. Marathon übernimmt diese Werte für die Anfrage.
3. **Absender ist Loopback (`127.0.0.1`, `::1`) ohne `X-Forwarded-Proto`:** unverändert durchlassen (lokaler Direktzugriff auf dem Server-Rechner, z. B. Live-Vorschau und Rettungszugang `http://127.0.0.1:8080`).
4. **Sonst:** Schema und `Host` der Anfrage selbst; Weiterleitungsangaben werden ignoriert.
5. Weichen Schema oder Rechnername (Kleinbuchstaben, `:443` entfällt) von `public_url` ab: `308` mit `Location: <public_url><pfad>[?<query>]`.

## 2a.4 Worker

- `python -m marathon.worker --server <url>` akzeptiert nur `https://`-Adressen; sonst Exit-Code 2 mit „Server-Adresse muss mit https:// beginnen“.
- `--ca-file <pfad>` (optional): Das Server-Zertifikat wird ausschließlich gegen diese Datei geprüft. Ohne `--ca-file` gilt der Zertifikatsspeicher des Systems (Paket `truststore`). Fehlt oder ist die Datei ungültig: Exit-Code 2 mit „Ungültige Worker-Konfiguration: CA-Datei nicht lesbar: <pfad>“. Zertifikats- und Namensprüfung sind nie abschaltbar.
- **Server-Worker:** `--server <public_url>`, bei `builtin = true` zusätzlich `--ca-file .cert/caddy/pki/authorities/local/root.crt`. Bis Caddy das Stammzertifikat angelegt hat, endet der Server-Worker mit Exit-Code 2 und wird von der Aufsicht neu gestartet.

## 2a.5 Vertrauen und Einrichtung

- Stammzertifikat des mitgelieferten Proxys: `.cert/caddy/pki/authorities/local/root.crt` (entsteht beim ersten Start).
- `python run.py --trust` importiert es auf dem Server-Rechner: unter Windows mit `certutil -user -addstore Root <datei>` in den Zertifikatsspeicher des angemeldeten Benutzers (ohne Adminrechte, Windows fragt einmal nach); unter Linux nennt der Befehl Pfad und Vorgehen. Fehlt die Datei: „Stammzertifikat fehlt (<pfad>). Zuerst python run.py einmal starten.“
- Weitere Rechner (Browser und Worker) importieren die Datei einmal von Hand in „Vertrauenswürdige Stammzertifizierungsstellen“ des Benutzers. Ab Release *Worker-Client & Steuerung* übernimmt der Installer den Import.
- `build.py`, Schritt „Caddy 2.11.9 (.tools/caddy)“ nach PostgreSQL: `https://github.com/caddyserver/caddy/releases/download/v2.11.9/caddy_2.11.9_<paket>`; SHA-256 `windows_amd64.zip` = `ad93112d335ee687100c4c9f11bced16ca590377976b7fe7883882b9e797e472`, `linux_amd64.tar.gz` = `5b8cb7faf8726a2028ad96a66e54e6e81e8f361bc6175e5d22bbd997cc5177fb`.
- `.cert/` ist in `.gitignore`.

## 2a.6 Folgen für Teil 3

- `public_url` steht in `[proxy]` (nicht in `[auth]`) und kommt aus den Einstellungen.
- `[auth] cookie_secure` entfällt; Session-Cookies sind immer `Secure`.

---

# Teil 3: Geplante Releases – Infrastruktur (geplant)

Verbindlicher Architektur- und Umsetzungsvertrag (v2: User und Worker strikt getrennt) für die Releases *Zugang & Nutzer*, *Worker-Client & Steuerung*, *Datei- und Ordner-Dialog* und *Lokale Watchfolder & Rechnerwechsel*. Wo dieser Teil Regeln aus Teil 2 erweitert oder ersetzt, gilt mit Umsetzung des jeweiligen Releases Teil 3. Offene Punkte und Entscheidungen je Release stehen in `docs/mission_log.md` und gehen bis zur Klärung vor; nach der Klärung werden sie hier eingearbeitet. Der vorgelagerte Release *HTTPS* steht in Teil 2a. Migration und Protokollnummer je Release werden bei der Klärung festgelegt.

## 3.1 Konfiguration `config/marathon.toml` und Geheimnisse

```toml
[auth]
admin_emails = []                      # Break-Glass-Admins (immer Admin, in UI nicht herabstufbar/löschbar)
allowed_domains = []                   # Startwert für erlaubte Mail-Domains (danach gilt DB-Einstellung)
session_days = 180                     # Sliding-Expiration-Dauer einer Nutzer-Session in Tagen (1–365)
code_ttl_minutes = 15                  # Gültigkeit des Anmeldecodes in Minuten (1–60)

[smtp]
host = ""                              # Startwert; danach Einstellungen → Mailversand (Datenbank); leer = Mailversand nicht eingerichtet
port = 587                             # 1–65535
username = ""                          # Anmeldename; leer = ohne Anmeldung (z. B. Microsoft 365 Direct Send)
use_tls = true                         # STARTTLS aktivieren
from_address = ""                      # Absenderadresse, z. B. marathon@looks.film
from_name = "Marathon"                 # Anzeigename des Absenders

[worker_client]
require_admin_approval = false         # true = neue Worker müssen vor dem ersten Start einmalig vom Admin zugelassen werden
local_bridge_port = 47291              # Loopback-Port (127.0.0.1) des Tray-Clients zur lokalen Rechner-Erkennung im Browser
control_session_hours = 12             # Gültigkeit der lokalen Worker-Steuerungs-Session im Browser (1–168)
```

Regeln:
- **Keine Zugangsdaten in `marathon.toml` oder im Code:** Das SMTP-Passwort wird ausschließlich aus der Umgebungsvariable `MARATHON_SMTP_PASSWORD` oder (Fallback) aus `.data/smtp.pass` (Dateirechte `0600` unter Linux) gelesen.
- **Validierung beim Start:**
  - `admin_emails` und `allowed_domains` werden getrimmt, mit `casefold()` normalisiert, auf gültige Syntax geprüft und ohne Doppelte übernommen (Reihenfolge bleibt); ungültige Einträge liefern `ConfigError`. Bei `allowed_domains` wird ein führendes `@` entfernt.
  - `session_days` (1–365), `code_ttl_minutes` (1–60), `smtp.port` (1–65535), `smtp.use_tls` Wahrheitswert, `smtp.from_address` leer oder gültige Adresse; `host` gesetzt, aber `from_address` leer → `ConfigError`. `[worker_client]` (Release Worker-Client): `local_bridge_port` (1024–65535), `control_session_hours` (1–168).
- **Mailversand eingerichtet** heißt: `smtp.host` und `smtp.from_address` sind gesetzt.
- **Seed (`marathon/server/db/seed.py`):**
  - `settings`: Schlüssel `auth.allowed_domains` wird beim Start mit `config.auth.allowed_domains` angelegt, sofern noch kein Eintrag existiert; danach gilt die Datenbank.
  - `settings`: Schlüssel `smtp` wird beim Start mit den Werten aus `[smtp]` in `config/marathon.toml` angelegt, aber nur, wenn der Eintrag noch fehlt; danach gilt die Datenbank und Änderungen erfolgen unter Einstellungen → Mailversand.
  - `users`: Jede Adresse aus `config.auth.admin_emails` wird beim Start angelegt oder auf `role = 'admin'` synchronisiert.

## 3.2 Datenbank

Ausschließlich ergänzende Migrationen mit vollständigem Downgrade.

### Migration `0010_users.py` (Release Zugang & Nutzer, auf Basis von `0009_worker_cleanup.py`)

| Tabelle | Spalten & Constraints |
|---|---|
| `users` | `id TEXT PRIMARY KEY`, `email TEXT NOT NULL UNIQUE`, `display_name TEXT NOT NULL DEFAULT ''`, `role TEXT NOT NULL CHECK (role IN ('user','admin'))`, `external_subject TEXT NULL UNIQUE`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `last_login_at TIMESTAMPTZ NULL` |
| `auth_codes` | `id TEXT PRIMARY KEY`, `email TEXT NOT NULL`, `code_hash TEXT NOT NULL`, `expires_at TIMESTAMPTZ NOT NULL`, `consumed_at TIMESTAMPTZ NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` · Index `(email, created_at)` |
| `auth_failures` | `id BIGSERIAL PRIMARY KEY`, `email TEXT NOT NULL`, `failed_at TIMESTAMPTZ NOT NULL DEFAULT now()` · Index `(email, failed_at)` |
| `auth_locks` | `email TEXT PRIMARY KEY`, `locked_until TIMESTAMPTZ NOT NULL` |
| `sessions` | `id TEXT PRIMARY KEY`, `user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE`, `token_hash TEXT NOT NULL UNIQUE`, `expires_at TIMESTAMPTZ NOT NULL`, `last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `user_agent TEXT NOT NULL DEFAULT ''`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` · Index `(user_id)` |
| `workflow_shares` | `workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE`, `user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE`, `access TEXT NOT NULL CHECK (access IN ('view','run','edit'))`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `PRIMARY KEY (workflow_id, user_id)` |
| `workflows` (Erweiterung) | `owner_user_id TEXT NULL REFERENCES users(id) ON DELETE SET NULL` · Index `(owner_user_id)` |

Die neuen Tabellen erhalten dieselben Live-Kanal-Trigger wie die bestehenden Tabellen (Migration `0006`), außer `auth_codes`, `auth_failures` und `sessions`.

### Migration des Releases Worker-Client & Steuerung (Nummer bei Klärung)

| Tabelle | Spalten & Constraints |
|---|---|
| `worker_ephemeral_tokens` | `token_hash TEXT PRIMARY KEY`, `worker_id TEXT NOT NULL REFERENCES workers(id) ON DELETE CASCADE`, `purpose TEXT NOT NULL CHECK (purpose IN ('control_link','presence'))`, `expires_at TIMESTAMPTZ NOT NULL`, `consumed_at TIMESTAMPTZ NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` · Index `(worker_id, expires_at)` |
| `worker_control_sessions` | `id TEXT PRIMARY KEY`, `worker_id TEXT NOT NULL REFERENCES workers(id) ON DELETE CASCADE`, `token_hash TEXT NOT NULL UNIQUE`, `expires_at TIMESTAMPTZ NOT NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` · Index `(worker_id)` |
| `workers` (Erweiterung) | `token_hash TEXT NULL UNIQUE`; `approved BOOLEAN NOT NULL DEFAULT true`; `availability_config JSONB NOT NULL DEFAULT '{}'`; `client_available BOOLEAN NOT NULL DEFAULT true`; `availability_reason TEXT NOT NULL DEFAULT ''`; `ck_workers_status` erweitern auf `('ready','draining','stopping','stopped')` |
| `assets` (Erweiterung, Release Lokale Watchfolder) | `locality_worker_id TEXT NULL REFERENCES workers(id) ON DELETE SET NULL` · Index `(locality_worker_id)` |

*(Bewusst **kein** `owner_user_id` auf `workers` – Worker gehören Rechnern, nicht Nutzerkonten.)*

## 3.3 Zugang, Authentifizierung & Berechtigungen

### Auth-Schnittstelle (Andockstelle fürs spätere Ökosystem)

- `marathon/server/auth/provider.py` definiert die Schnittstelle `AuthProvider` (`resolve_session`, `start_login`, `complete_login`).
- Standard-Implementierung: `EmailCodeProvider` (Anmeldung per Einmalcode, ohne Link).
- Durch `users.external_subject` und normalisierte `users.email` kann später ein OIDC- oder Reverse-Proxy-Header-Provider eingehängt werden, ohne `users` oder `workflow_shares` umzubauen.

### Mailversand (`marathon/server/mail.py`)

- SMTP über die Python-Standardbibliothek (`smtplib`, `email.message`), keine neue Abhängigkeit; Zeitlimit 10 s je Versand (Verbindung, STARTTLS, Anmeldung, Senden).
- SMTP-Einstellungen werden in `settings` unter dem Schlüssel `smtp` gespeichert. Die Werte aus `[smtp]` in `config/marathon.toml` sind nur Startwerte beim ersten Start; danach gelten die Datenbankwerte. Ein leerer `host` bedeutet, dass der Mailversand nicht eingerichtet ist. Einstellungsänderungen gelten ohne Neustart.
- Nur Admins dürfen `GET /api/v1/settings/smtp` und `PUT /api/v1/settings/smtp` verwenden. GET und erfolgreiche PUT antworten mit `{host, port, username, use_tls, from_address, from_name, password_set}`; das Passwort selbst wird nie zurückgegeben.
- PUT akzeptiert `{host, port, username, use_tls, from_address, from_name, password}`. `password: null` lässt das gespeicherte Passwort unverändert; ein nichtleeres Passwort wird atomar nach `.data/smtp.pass` geschrieben (Dateirechte `0600` unter Linux). Die UI sendet bei leerem Passwortfeld `null`. `MARATHON_SMTP_PASSWORD` hat Vorrang vor der Datei. Ein gesetztes SMTP-Passwort wird nur verwendet, wenn `username` gesetzt ist.
- SMTP-Validierungsfehler antworten mit `422` und exakt einem dieser Texte: „Mailversand: Server nur als Rechnername oder IP-Adresse, z. B. smtp.office365.com“, „Mailversand: Port muss zwischen 1 und 65535 liegen“, „Mailversand: Absenderadresse fehlt“, „Mailversand: Absenderadresse ungültig“, „Mailversand: Absendername ist zu lang (max. 100 Zeichen)“, „Mailversand: Passwort darf keinen Zeilenumbruch enthalten“ oder „Mailversand: Passwort ist zu lang (max. 1024 Zeichen)“.
- Passwort und Code erscheinen nie in Logs oder Fehlermeldungen. Fehlt das Passwort bei gesetztem `username`: Fehler „SMTP-Passwort fehlt (MARATHON_SMTP_PASSWORD oder .data/smtp.pass)“.
- Prüfbefehl `python run.py mail-test <adresse>`: sendet Betreff „Marathon: Testmail“, Text „Der Mailversand von Marathon funktioniert.“; Erfolg „Testmail an <adresse> gesendet“ (Exit-Code 0), sonst „Fehler: Testmail nicht gesendet: <Grund>“ (Exit-Code 1). Als Einzelbefehl startet er die Datenbank bei Bedarf und stoppt sie wieder, wenn er sie selbst gestartet hat. Ohne eingerichteten Mailversand: „Fehler: Mailversand ist nicht eingerichtet (Einstellungen → Mailversand)“.
- Anmelde-Mail: Betreff „<code> ist Dein Marathon-Code“, Text „Dein Anmeldecode für Marathon: <code>\n\nDer Code gilt <code_ttl_minutes> Minuten. Falls Du keine Anmeldung angefordert hast, ignoriere diese E-Mail.“

### Anmeldung per Einmalcode

Grundsatz: Antworten verraten nie, ob ein Konto existiert oder eine Domain freigegeben ist.

1. **`GET /api/v1/auth/status`** (ohne Anmeldung) → `{ "mail_configured": bool }`. Ist der Mailversand nicht eingerichtet, zeigt die Anmeldeseite statt des Formulars „Anmeldung per E-Mail ist nicht eingerichtet. Bitte wende Dich an Deinen Administrator.“
2. **`POST /api/v1/auth/request-code`** mit `{ "email": "…" }`:
   - E-Mail trimmen & `casefold()`; ungültige Syntax → `422` „Bitte eine gültige E-Mail-Adresse eingeben“.
   - Mailversand nicht eingerichtet → `503` mit dem Text aus Schritt 1.
   - Adresse gesperrt (`auth_locks.locked_until > now()`) → `429` „Zu viele falsche Codes. Die Anmeldung für diese Adresse ist vorübergehend gesperrt.“
   - Mehr als 5 Einträge in `auth_codes` für diese Adresse in den letzten 15 Minuten → `429` „Zu viele Anfragen. Bitte in ein paar Minuten erneut versuchen.“
   - Berechtigt ist die Adresse, wenn ein Konto existiert, die Domain **exakt** in `settings['auth.allowed_domains']` steht (Subdomains werden einzeln eingetragen) oder die Adresse in `config.auth.admin_emails` steht.
   - Ältere, unverbrauchte Codes der Adresse werden ungültig (`consumed_at = now()`). Neuer 6-stelliger Zifferncode (`secrets.randbelow`), gespeichert nur als SHA-256 von `<email>:<code>` in `auth_codes`, gültig `code_ttl_minutes`. Für nicht berechtigte Adressen wird ebenfalls ein Eintrag angelegt, aber keine Mail gesendet (gleiches Verhalten bei Rate-Limit und Antwortzeit).
   - Die Mail geht im Hintergrund raus (begrenzter Thread-Pool, Zeitlimit 10 s); die Antwort wartet nie auf den Versand. Fehler landen nur im Server-Log.
   - Antwort immer `200` `{ "detail": "Falls die Adresse für Marathon freigegeben ist, ist eine E-Mail mit Code unterwegs. Keine E-Mail erhalten? Adresse prüfen oder an den Administrator wenden." }`.
3. **`POST /api/v1/auth/confirm`** mit `{ "email": "…", "code": "123456" }`:
   - Adresse gesperrt → `429` wie oben.
   - Verbraucht den passenden Eintrag atomar (`UPDATE auth_codes SET consumed_at = now() WHERE email = … AND code_hash = … AND consumed_at IS NULL AND expires_at > now()`).
   - Kein Treffer → Eintrag in `auth_failures`; liegen damit für die Adresse in den letzten 24 Stunden 3 Fehlversuche vor, wird sie für 24 Stunden gesperrt (`auth_locks.locked_until = now() + 24 h`, ihre `auth_failures` werden gelöscht) und die Antwort ist `429` wie oben; sonst `400` „Code ungültig oder abgelaufen“. Fehlversuche zählen für jede Adresse gleich, auch ohne Konto.
   - Treffer: Existiert kein `users`-Eintrag, wird er jetzt angelegt (`role = 'admin'` wenn in `admin_emails`, sonst `'user'`); `last_login_at = now()`; `auth_failures` der Adresse werden gelöscht.
   - Erstellt Session in `sessions` (Token `secrets.token_urlsafe(32)`, gespeichert als SHA-256), setzt Cookie `marathon_session` (`HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure`, `Max-Age = session_days * 86400`); Antwort wie `GET /api/v1/auth/me`.
4. **Sliding Expiration:** Bei authentifizierten Anfragen wird `sessions.expires_at` auf `now() + session_days` verlängert und das Cookie aufgefrischt, sobald weniger als 80 % der Laufzeit übrig sind; `last_seen_at` wird dabei mit aktualisiert.
5. **`POST /api/v1/auth/logout`** antwortet immer mit `204` und entfernt das Cookie, auch ohne gültige Session. **`GET /api/v1/auth/me`** → `{ "id", "email", "display_name", "role" }`, ohne gültige Session `401`.

### Zugriffsschutz der API

- Ohne gültige Session antworten alle Routen unter `/api/v1` mit `401` „Nicht angemeldet“, außer `GET /health`, `GET /auth/status`, `POST /auth/request-code`, `POST /auth/confirm` und der Worker-Schnittstelle `/worker/…` (Worker-Token). Der Live-Kanal `GET /events` verlangt ebenfalls eine Session.
- Admin-Routen antworten normalen Nutzern mit `403` „Nur für Admins“.
- Unbekannte API-Pfade unter `/api/` verlangen ebenfalls eine gültige Session.
- Die Oberfläche (statische Dateien) ist ohne Anmeldung erreichbar; sie leitet bei `401` auf `/login` um.

### Break-Glass-Admin

- Alle Adressen aus `config.auth.admin_emails` sind geschützte Bootstrap-Admins:
  - In der UI ist ihre Rolle gesperrt und sie sind nicht löschbar (Hinweis *„In Konfiguration festgelegt“*).
  - Die API lehnt Herabstufen oder Löschen geschützter Admins und des letzten Admins mit `409` ab: „In Konfiguration festgelegt“ bzw. „Der letzte Admin kann nicht herabgestuft oder gelöscht werden“.
- **Lokaler CLI-Notfallzugang (ohne SMTP):**
  - Während `python run.py` läuft, in dessen Konsole `admin-code <adresse>` und Enter eingeben. Alternativ `python run.py admin-code <adresse>` ohne laufenden Server ausführen.
  - Der Einzelbefehl startet die Datenbank bei Bedarf selbst, führt bei eigenem Datenbankstart das Upgrade aus und stoppt die Datenbank danach wieder; eine bereits laufende Datenbank lässt er weiterlaufen.
  - Nur für bestehende Admins oder Adressen aus `admin_emails`, sonst „Fehler: <adresse> ist kein Admin“ (Exit-Code 1). Hebt eine Sperre der Adresse auf, erzeugt einen Code (Gültigkeit `code_ttl_minutes`) und gibt ausschließlich lokal aus: „Anmeldecode für <adresse>: <code> (gültig <n> Minuten)“.

### Nutzerverwaltung (nur Admins, Seite „Nutzer“)

- `GET /api/v1/users` → Liste `{ id, email, display_name, role, protected, created_at, last_login_at, session_count, locked_until }` (`locked_until` nur bei aktiver Sperre, sonst `null`).
- `PUT /api/v1/users/{id}` mit `{ "role": "user" | "admin" }`.
- `DELETE /api/v1/users/{id}`: löscht Konto und Sessions; Workflows des Nutzers sind danach ohne Eigentümer (nur für Admins sichtbar). Nicht möglich für den eigenen Account (`409` „Du kannst Dein eigenes Konto nicht löschen“), geschützte Admins und den letzten Admin. Mit freigegebener Domain kann sich die Adresse neu registrieren (neues, leeres Konto). Ein Deaktivieren gibt es nicht.
- `GET /api/v1/users/{id}/sessions` → `{ id, user_agent, created_at, last_seen_at, expires_at, current }`; `DELETE /api/v1/users/{id}/sessions/{session_id}` und `DELETE /api/v1/users/{id}/sessions` (alle) widerrufen Sessions. Nur Admins widerrufen Sessions; Nutzer melden sich selbst nur ab.
- `DELETE /api/v1/users/{id}/lock` hebt eine Sperre wegen falscher Codes auf.
- `GET /api/v1/users/directory` (jeder angemeldete Nutzer) → `{ id, email, display_name }` aller Nutzer, für den Teilen-Dialog.

### Erlaubte E-Mail-Domains (Einstellungen)

- `GET /api/v1/settings` ist für alle angemeldeten Nutzer lesbar; `PUT /api/v1/settings` ist Admins vorbehalten und verlangt alle Abschnitte. Die Antwort enthält `"auth": { "allowed_domains": ["looks.film"] }`.
- Prüfregel: Einträge getrimmt, `casefold()`, führendes `@` entfernt, Doppelte entfernt (Reihenfolge bleibt), jede Domain mit mindestens einem Punkt aus Labels `a–z 0–9 -` (1–63 Zeichen, nicht mit `-` beginnend oder endend), insgesamt höchstens 253 Zeichen. Fehler `422` „E-Mail-Domains: nur Domains ohne Adresse, z. B. looks.film“.
- Vergleich exakt: `looks.film` erlaubt `max@looks.film`, nicht `max@berlin.looks.film`.
- Die Liste gilt nur für neue Konten; bestehende Konten melden sich weiter an.
- Oberfläche: Abschnitt „Anmeldung“, Feld „Erlaubte E-Mail-Domains“ (durch Komma getrennt, Platzhalter `z. B. looks.film`).

### Workflow-Freigaben (`workflow_shares`)

- Module sind bis zum Release Modul-Builder immer für alle Nutzer freigegeben; Modulsperren je Nutzer gibt es nicht.
- Jeder Nutzer legt Workflows an; der Ersteller wird Eigentümer (`owner_user_id`). Duplizieren setzt voraus, dass der Nutzer den Workflow sehen darf; Eigentümer der Kopie ist der Nutzer.
- Stufen je `(workflow_id, user_id)`:
  - Kein Eintrag = **privat** (kein Zugriff).
  - `view` = **Teilen zur Ansicht**: Workflow, Module, Assets, Logs und Status ansehen; kein Bearbeiten, Speichern, Starten, Stoppen, Zeitplan ändern oder erneutes Verarbeiten.
  - `run` = **Teilen zur Ausführung**: zusätzlich Starten, Stoppen und Assets erneut verarbeiten; kein Bearbeiten, Speichern oder Zeitplan ändern.
  - `edit` = **Teilen zur Bearbeitung und Ausführung**: zusätzlich Bearbeiten, Speichern und Zeitplan.
- Eigentümer hat implizit `edit` und darf zusätzlich löschen und teilen. Admins haben immer Vollzugriff, sehen auch Workflows ohne Eigentümer und setzen Eigentümer.
- Wirksame Stufe je Workflow `access`: `admin` | `owner` | `edit` | `run` | `view`; Workflows ohne Zugriff erscheinen nirgends (Listen, Dashboard, Assets, Reports) und antworten mit `404` „Workflow nicht gefunden“. Unzureichende Stufe → `403` „Keine Berechtigung für diesen Workflow“. Erneut verarbeiten in einen anderen Workflow verlangt dort ebenfalls mindestens `run`.
- `GET /api/v1/workflows/{id}/shares` liefert je Freigabe `user_id`, `access`, `email` und `display_name`. `PUT /api/v1/workflows/{id}/shares` mit `{ "shares": [{ "user_id": "…", "access": "view" | "run" | "edit" }] }` ersetzt alle Freigaben; nur Eigentümer und Admins dürfen teilen, der Eigentümer selbst steht nicht in der Liste.
- `PUT /api/v1/workflows/{id}/shares` antwortet mit `422` und „Unbekannter Nutzer: <id>“, „Der Eigentümer kann nicht freigegeben werden“ oder „Nutzer doppelt: <id>“, wenn Nutzer fehlen, der Eigentümer eingetragen oder ein Nutzer doppelt aufgeführt wird.
- `PUT /api/v1/workflows/{id}/owner` mit `{ "user_id": "…" }` (nur Admins); eine Freigabe für den neuen Eigentümer wird entfernt. Duplizieren übernimmt keine Freigaben. `POST /api/v1/workflows/{id}/graph/impact` verlangt die Stufe `edit`.
- Workflow-Datensätze enthalten `owner` (`{ id, email, display_name }` oder `null`) und `access`.

### Rollen-Abgrenzung in Oberfläche, API & Live-Kanal

- **Admin (eingeloggt):** Voller Zugriff auf alle Workflows, alle Worker (inkl. Server-Worker, Zulassung und Fähigkeiten-Rangliste), Servereinstellungen (`/settings`), Nutzerverwaltung (`/users`) und Server-Neustart.
- **Normaler Nutzer (eingeloggt):**
  - Menü ohne „Einstellungen“ und „Nutzer“; die Admin-Routen liefern `403`.
  - Sieht in Workflows, Dashboard, Assets und Reports ausschließlich eigene und für ihn freigegebene Workflows.
  - Sieht alle Worker nur lesend (keine Steuerung, kein Bearbeiten); `PUT /api/v1/workers/{id}`, `POST /api/v1/workers/{id}/start` und `POST /api/v1/workers/{id}/stop` sind Admins vorbehalten.
  - Ordner-Dialog und Modul-Palette stehen ihm zur Verfügung.
- **Live-Kanal:** Ereignisse enthalten nur die Namen geänderter Tabellen, keine Inhalte; die Oberfläche lädt daraufhin über die API, die nach Rechten filtert.
- **Person vor einem Worker OHNE Marathon-Login** *(Release Worker-Client & Steuerung)*:
  - Öffnet per Doppelklick / Rechtsklick im Tray-Icon die dedizierte Steuerungsansicht `/worker-control` (abgesichert über das lokale `marathon_worker_control`-Cookie für genau diese `worker_id`).
  - Darf dort ausschließlich **diesen einen Worker** starten, stoppen (sanft), sofort stoppen und dessen Verfügbarkeitsregeln pflegen; sieht keine Workflows, keine Assets, keine Nutzer, keine Servereinstellungen und keine fremden Worker.
  - Sitzt ein eingeloggter Nutzer vor einem Rechner mit laufendem Worker-Client, kann er genau diesen lokalen Worker im Browser steuern und für lokale Workflows aktivieren.

### Server-Neustart (nur Admins)

- Konsole von `python run.py` (auch `--dev`): `restart` und Enter beendet den Server sauber und startet ihn neu; Datenbank und Live-Vorschau laufen weiter. Ausgabe „Server wird neu gestartet …“.
- `POST /api/v1/server/restart` → `202` `{ "detail": "Server wird neu gestartet" }`; ohne registrierte Neustart-Möglichkeit (z. B. bei einem Start ohne `run.py`) antwortet der Endpunkt mit `503` „Neustart nicht verfügbar“. Der Neustart wird erst nach dem Senden der `202`-Antwort angestoßen; der Server beendet sich sauber mit Exit-Code `75`, `run.py` startet ihn daraufhin neu. Jeder andere Exit-Code beendet `run.py` wie bisher.
- Einstellungen: Schaltfläche „Server neu starten“ mit Bestätigung „Server jetzt neu starten? Laufende Jobs werden unterbrochen und neu verteilt.“; während des Neustarts zeigt die Oberfläche das Verbindungs-Overlay.

## 3.4 Worker-Client (Windows-Tray), Entkopplung & Verfügbarkeitsregeln

### Client-Aufbau, Registrierung & Systemtray

- Paketstruktur: `marathon/client/` (dünner Starter + Tray-Steuerung + Loopback-Bridge) nutzt die bestehende Laufzeit `marathon/worker/runtime.py`; eigene gepinnte `requirements/client.txt` (u. a. `pystray`, `Pillow`, `psutil` für CPU/RAM/Prozesse; Windows-API `GetLastInputInfo` via `ctypes`; GPU-Auslastung best-effort via `nvidia-smi`/NVML oder Windows-PDH, ohne Absturz wenn kein GPU-Sensor vorliegt).
- **Laufzeitkontext unter Windows:** Läuft im **Benutzerkontext** (Autostart / Benutzer-Aufgabenplanung), **nicht** als Windows-Dienst in Session 0 – nur so stehen Tray-Icon, Benutzereingaben (`GetLastInputInfo`) und lokale/gemappte Laufwerke zur Verfügung.
- **Geräte-Registrierung ohne Nutzerkonto (`POST /api/v1/worker/enroll`):**
  1. Startet der Client ohne `%LOCALAPPDATA%\MarathonWorker\worker.json`, sendet er `POST /api/v1/worker/enroll` mit `{ "hostname", "platform", "client_version" }`.
  2. Der Server erzeugt eine stabile `worker_id` und ein kryptografisches `worker_token` (`secrets.token_urlsafe(32)`), speichert nur `token_hash` (SHA-256) in `workers`, setzt `status = 'stopped'` sowie `approved = not config.worker_client.require_admin_approval` und gibt `{ "worker_id", "worker_token", "approved" }` genau einmal zurück.
  3. Der Client speichert `server_url`, `worker_id` und `worker_token` lokal in `%LOCALAPPDATA%\MarathonWorker\worker.json`.
  4. **Worker-Authentifizierung (`marathon/server/worker_auth.py`):** Jede Anfrage unter `/api/v1/worker/…` prüft `Authorization: Bearer <token>` per SHA-256-Hash gegen `workers.token_hash` und bindet den Request strikt an diese eine `worker_id` (der interne `server-worker` behält sein eigenes lokales Server-Token).
- **Kein eigenes Einstellungs-UI im Tray:**
  - Doppelklick oder Rechtsklick → **„Worker-Einstellungen öffnen“** ruft mit dem eigenen `worker_token` `POST /api/v1/worker/control-link` auf und öffnet die zurückgegebene Einmal-URL im Standardbrowser.
  - Rechtsklick-Menü minimal: *Worker-Einstellungen öffnen*, *Diesen Rechner im Browser verknüpfen* (Fallback für lokale Erkennung), *Status: <Zustand + Grund>* (Infozeile), *Beenden*.
- **Download aus Marathon:** `GET /api/v1/client/download` liefert das von `build.py` erzeugte Client-Paket mit vorausgefüllter `server_url` aus.

### Lokale Worker-Steuerung ohne Marathon-Login (`/worker-control`)

1. Client sendet `POST /api/v1/worker/control-link` (authentifiziert mit `worker_token`).
2. Server legt einen Eintrag in `worker_ephemeral_tokens` (`purpose = 'control_link'`, TTL 60 s) an und liefert `{ "url": "<public_url>/worker-control?token=<einmal_token>" }`.
3. Browser öffnet die URL → `GET /api/v1/worker-control/exchange?token=<einmal_token>` (bzw. Bestätigungs-POST auf der Route) konsumiert den Einmal-Token atomar, legt eine `worker_control_sessions`-Zeile an (TTL `control_session_hours`), setzt das `HttpOnly; SameSite=Lax`-Cookie `marathon_worker_control` und zeigt die fokussierte Einstellungsseite genau dieses Workers.
4. Über diese Session sind **ausschließlich** Lesen/Steuern/Verfügbarkeitsregeln dieser einen `worker_id` erlaubt (`GET/PUT /api/v1/worker-control/self`, `POST /api/v1/worker-control/self/{start|stop|stop-now}`), sowie optional ein Live-Event-Stream nur für diesen einen Worker. Fähigkeiten-Rangliste bleibt schreibgeschützt (nur Admin). Ist `worker.approved == false`, zeigt die Seite den Hinweis *„Dieser Worker wartet auf Freigabe durch einen Administrator“*.

### Lokale Rechner-Erkennung im Browser (Loopback-Bridge + Fallback)

1. **Loopback-Endpunkt im Tray-Client (`127.0.0.1:<local_bridge_port>`):**
   - Bindet strikt auf `127.0.0.1` (aus dem Netzwerk physisch nicht erreichbar).
   - Prüft bei jedem Request den `Origin`-Header exakt gegen `server_url` (Schema + Host + Port); abweichende oder fehlende Origins erhalten `403` ohne CORS-Header. Für die gültige Origin werden `Access-Control-Allow-Origin: <origin>` und `Access-Control-Allow-Private-Network: true` gesetzt.
   - `GET http://127.0.0.1:<port>/presence`:
     - Der Client hält ein kurzlebiges `presence_token` (TTL 5 Min., bezogen über `POST /api/v1/worker/presence-token` mit seinem `worker_token`, rechtzeitig erneuert).
     - Antwortet ausschließlich mit `{ "worker_id": "…", "worker_name": "…", "online": true, "status": "…", "presence_token": "…" }`.
     - Gibt **niemals** `worker_token` und **niemals** lokale Dateien direkt aus.
2. **Sicherheitsnachweis am Server:**
   - Sendet der Browser das `presence_token` im Header `X-Marathon-Worker-Presence` (zusammen mit einer gültigen User-Session `marathon_session`), verifiziert der Server den Hash gegen `worker_ephemeral_tokens` (`purpose = 'presence'`, nicht abgelaufen).
   - Nur damit darf ein normaler Nutzer diesen Worker als lokalen Ziel-Worker in einem Workflow aktivieren und im Datei-Dialog (`source=worker`) dessen lokale Laufwerke browsen. Fremde Workstations im Netz bleiben unzugänglich.
3. **Fallback (falls Browser Loopback blockiert):**
   - Über Tray → *„Diesen Rechner im Browser verknüpfen“* öffnet der Client `<public_url>/worker-presence?token=<einmal_token>`, der ein rein browser-lokales `HttpOnly`-Cookie `marathon_local_worker` für genau diese `worker_id` setzt (unabhängig vom User-Account, gebunden an die lokale Browser-Instanz).

### Steuerbefehle & Zustände (`ready`, `draining`, `stopping`, `stopped`)

| Aktion | Zielzustand | Verhalten |
|---|---|---|
| **Starten** | `ready` | Worker darf Jobs annehmen (sofern `approved = true`, `online = true` und `client_available = true`). |
| **Stoppen** (sanft / Drain) | `draining` | Server vergibt keine neuen Jobs mehr. Laufende Jobs laufen sauber zu Ende. Sobald `running_jobs == 0`, wechselt der Server automatisch auf `stopped`. |
| **Sofort stoppen** (hart) | `stopping` | Laufende Jobs werden über das Abbruchsignal hart abgebrochen; Assets gehen zurück auf den letzten fertigen Ausgang und werden neu verteilt (zählt nicht als Fehlversuch). Nach Abbruch → `stopped`. |

### Verfügbarkeitsregeln als Positivliste (`availability_config`) & Protokoll 3

Alle Einstellungen werden zentral in Marathon gepflegt (erreichbar über `/worker-control` und Admin-`/workers/<id>`) und beantworten durchgängig die Frage: **„Wann ist der Worker verfügbar?“**

```json
{
  "mode": "rules",
  "schedule": {
    "enabled": true,
    "timezone": "Europe/Berlin",
    "days": {
      "mon": [{"start": "19:00", "end": "24:00"}, {"start": "00:00", "end": "07:00"}],
      "tue": [{"start": "19:00", "end": "24:00"}, {"start": "00:00", "end": "07:00"}],
      "wed": [{"start": "19:00", "end": "24:00"}, {"start": "00:00", "end": "07:00"}],
      "thu": [{"start": "19:00", "end": "24:00"}, {"start": "00:00", "end": "07:00"}],
      "fri": [{"start": "19:00", "end": "24:00"}, {"start": "00:00", "end": "07:00"}],
      "sat": [{"start": "00:00", "end": "24:00"}],
      "sun": [{"start": "00:00", "end": "24:00"}]
    }
  },
  "idle": {
    "enabled": true,
    "minutes": 15,
    "max_cpu_percent": 25,
    "max_ram_percent": 75,
    "max_gpu_percent": 20
  },
  "blocked_processes": ["Resolve.exe", "Adobe Premiere Pro.exe"]
}
```

Auswertungslogik (im Worker-Client, alle 5 s):
1. **Programm-Bedingung (harte Voraussetzung):** Läuft einer der Prozesse aus `blocked_processes` (case-insensitiver Vergleich auf Prozessname) → `available = false`, Grund: *„Programm <Name> aktiv“*.
2. **Freigabe-Bedingung (Positivliste):**
   - `mode == "always"` → positiv erfüllt.
   - `mode == "rules"` → positiv erfüllt, wenn (`schedule.enabled` **und** aktuelle lokale Uhrzeit liegt in einem positiven Fenster für den heutigen Wochentag) **ODER** (`idle.enabled` **und** Inaktivitätsbedingung erfüllt).
3. **Inaktivitätsbedingung (Schutz vor Selbst-Deaktivierung):**
   - **Im Leerlauf (kein aktiver Marathon-Job):** Keine Maus-/Tastatureingabe seit `>= idle.minutes` (`GetLastInputInfo`) **UND** geglättete Systemwerte (30-Sekunden-Mittel) für CPU `< max_cpu_percent`, RAM `< max_ram_percent` und GPU `< max_gpu_percent` (falls GPU-Sensor vorhanden).
   - **Während ein Marathon-Job läuft:** CPU/RAM/GPU-Schwellenwerte werden ausgesetzt (da der eigene Job Last erzeugt); geprüft werden nur noch Maus-/Tastatureingabe und `blocked_processes`.
4. **Übergang `available: true → false` während laufendem Job:** Client meldet sofort `available: false` samt Grund an den Server; laufende Jobs laufen sauber zu Ende (Drain). Neue Jobs vergibt der Server erst wieder bei `available: true`.
5. **Worker-Protokoll Version 3 (`/api/v1/worker/…`):**
   - `POST /worker/register` und `POST /worker/poll` akzeptieren `protocol: 3`, `available: bool`, `availability_reason: str`.
   - `POST /worker/poll` liefert zusätzlich bei Konfigurationsänderung `availability_config` (mit Hash/Version) sowie ggf. ausstehende `fs_request`- und `path_check`-Aufträge.

## 3.5 Watchfolder auf Workern, lokale Pfade & Rechnerwechsel

### Strukturiertes Pfadmodell & Rechnerwechsel (Rechner 1 → Rechner 2)

```json
{
  "path_scope": "local",
  "worker_id": "wrk_01j…",
  "path": "D:\\Ingest"
}
```

- Bei Netzwerkpfaden (`\\server\freigabe\…`): `path_scope = "network"`, `worker_id = null`.
- Migration bestehender Einstellungen: fehlendes `path_scope` wird für `\\…` als `"network"` interpretiert; lokale Pfade ohne `worker_id` gelten als unvollständig (Workflow nicht startbar, bis ein Worker gewählt ist).
- **UI-Darstellung:** Netzwerkpfade normal; lokale Pfade mit Worker-Chip `[<aktueller Worker-Name>] D:\Ingest` (über `worker_id` live aufgelöst, robust gegen Umbenennung).
- **Erstellen an Rechner 1:** Wählt der Nutzer einen lokalen Pfad, schlägt Marathon automatisch den per Loopback-Bridge erkannten lokalen Worker vor bzw. übernimmt ihn aus dem Datei-Dialog (`Dieser Rechner`).
- **Wechseln zu Rechner 2 (1-Klick-Umstellung):**
  - Öffnet ein berechtigter Nutzer (`edit`) einen Workflow mit lokalem Pfad an Rechner 2 (erkannt via Loopback-Bridge) und der Workflow steht noch auf Rechner 1:
    - Im Workflow-Kopf und direkt am lokalen Modul erscheint ein deutlicher Hinweis samt Aktion **„Auf diesen Rechner (Rechner 2) umstellen“**.
    - Beim Klick auf **„Starten“** eines gestoppten Workflows an Rechner 2 bietet ein kurzer Dialog direkt: *„Auf diesem Rechner (Rechner 2) starten“* vs. *„Auf Rechner 1 starten“*.
    - Läuft der Workflow bereits aktiv auf Rechner 1, wird er nicht ungefragt umgezogen (Hinweis: erst stoppen, dann umstellen).
  - **Live-Pfadprüfung nach Umstellung:** Über `POST /api/v1/workflows/{id}/bind-local-worker` (abgesichert durch `presence_token` für Rechner 2 oder Admin-Rolle) werden alle lokalen Module des Workflows auf `worker_id = <Rechner 2>` umgestellt; gleichzeitig prüft der Server per kurzlebigem Check über Rechner 2, ob die konfigurierten lokalen Pfade dort existieren. Fehlt ein Ordner auf Rechner 2, erscheint sofort eine klare Meldung am Modul mit direktem Button zum Datei-/Ordner-Dialog auf Rechner 2.

### Watchfolder als Worker-Fähigkeit & Sticky Locality

- `watchfolder` erhält `execution = "worker"` im Modulkatalog; neue Worker erhalten `watchfolder` ab Werk an oberster Stelle ihrer `skills`-Liste (auch der `server-worker`).
- Der alte Hintergrund-Thread `marathon/server/watcher.py` entfällt; der Engine-Tick erzeugt fällige Watchfolder-Jobs:
  - `path_scope == "network"` → Zuteilung an den `server-worker` (bzw. passenden verfügbaren Worker nach Fähigkeiten-Rang).
  - `path_scope == "local"` → Zuteilung **ausschließlich** an `worker_id`.
- **Sticky Locality für Folgejobs:**
  - Jedes Asset aus einem lokalen Watchfolder wird mit `assets.locality_worker_id = watchfolder.worker_id` angelegt.
  - Im Scheduler (`marathon/server/engine/tick.py`): Hat ein Asset `locality_worker_id IS NOT NULL`, kommt **nur** dieser eine Worker für Folgejobs infrage.
  - Schreibt ein Modul (`copy_file`, `move_file`, künftig `transcode` etc.) die resultierende Datei auf einen **Netzwerkpfad** (`\\…`), setzt die Engine `assets.locality_worker_id = NULL`. Ab dem nächsten Modul greift wieder die normale Verteilung nach Fähigkeiten-Rang.

### Kontextsensitive Modul-Hinweise & Statusspalten

- **Direkte Hinweise am Watchfolder-Modul (Editor + Überwachen):**
  1. Lokaler Pfad ohne laufenden Worker-Client auf dem aktuellen Rechner (und keine gültige `worker_id` gesetzt) → Meldung am Modul mit Link **„Worker-Client herunterladen“**.
  2. Konfigurierte `worker_id` ist ein anderer Rechner als der lokal erkannte → Hinweis + Aktion **„Auf diesen Rechner umstellen“** (für Nutzer mit `edit`-Recht).
  3. Konfigurierter `worker_id` ist offline, `stopped`, `draining`/`stopping`, nicht zugelassen (`approved = false`) oder `client_available = false` → Meldung am Modul mit genauem Grund und Link zu den Worker-Einstellungen (beim lokal erkannten Rechner direkt über `/worker-control` bzw. Hinweis auf Tray-Icon, für Admins `/workers/<worker_id>`).
  4. Ist beim fälligen Scan kein passender Worker verfügbar, wartet der Scan und schreibt *„Kein Worker für den Watchfolder verfügbar“* (bzw. den konkreten Grund) ins Modul-Log.
- **Spalten & Status:**
  - Watchfolder: Spalten **neu** (links: Funde des laufenden Scans; gehen nach Scan-Abschluss in `erfasst` über) und **erfasst** (rechts); Spalte `wartet` entfällt; gefundene Assets gehen sofort weiter.
  - Warten-Modul: Assets stehen während der Wartezeit in Spalte **wartet**, danach `erledigt` über Ausgang `Erfolg`.

## 3.6 Datei- und Ordner-Dialog (`FileBrowserDialog`)

### Funktionsumfang & Bedienung (React 19 / TypeScript, `lucide-react`)

Ausbau des bestehenden `ui/src/components/FolderDialog.tsx` zu einer wiederverwendbaren Komponente `FileBrowserDialog` (ohne externe File-Manager-Library, ohne Favoritenspalte links):

- **Props / Modi:** `mode: "folder" | "file"`, `accept_extensions?: string[]`, `allow_local_worker?: boolean` (Standard `true`); Rückgabe strukturiert: `{ scope: "network" | "local", worker_id: string | null, path: string }`.
- **Quellen-Leiste oben:** Umschalter **`Server`** (Freigaben von `\\<server_address>` bzw. Server-Pfade) vs. **`Dieser Rechner`** (lokal erkannter Worker via Loopback-Bridge; ohne Client klare Meldung + Download-Link). Admins erhalten optional zusätzlich ein Dropdown für beliebige Online-Worker.
- **Navigations- und Pfadzeile:**
  - Buttons: `Zurück` (`←`), `Vor` (`→`) mit clientseitigem Verlauf-Stack, `Nach oben` (`↑`), `Aktualisieren` (`↻`).
  - **Pfadleiste:** standardmäßig klickbare Breadcrumb-Knoten; Klick auf freie Fläche (oder `Strg+L` / `Alt+D`) wechselt in Editiermodus; Vorschlagmenü nach 150 ms Debounce (clientseitiger Cache je Elternpfad): `↓`/`↑` markiert, `Tab` übernimmt zum Weitertippen, `Enter` betritt, `Esc` schließt.
  - **Filterfeld rechts:** clientseitige Filterung verzögerungsfrei (case-insensitive Teilstring, Endung wie `.mp4`, Wildcards `*` und `?`).
- **Ansichten (Auswahl in `localStorage`):**
  - **Liste:** mehrspaltig vertikal fließend (wie Windows „Liste“), Icon + Name, horizontales Scrollen.
  - **Details:** sortierbare Tabelle (`Name`, `Änderungsdatum`, `Typ`, `Größe`; Ordner immer vor Dateien).
  - **Icons:** über `lucide-react` nach Dateigruppe (Ordner, Video, Audio, Bild, Dokument/Text/CSV/JSON, Archiv, generische Datei).
  - **Ordner- vs. Datei-Modus:** Im Ordner-Modus sind Dateien sichtbar, aber gedimmt und nicht wählbar („Diesen Ordner wählen“); im Datei-Modus wählt Doppelklick oder „Öffnen“ die Datei.
- **Performance (5.000+ Einträge):** Virtualisiertes Rendering im Frontend für `Details` und `Liste`; Statuszeile unten zeigt Elementzahl (gesamt / gefiltert) und ggf. Kappungshinweis.

### Backend-API (`GET /api/v1/fs/entries`) & Remote-Listing (Long-Poll-RPC)

- Erweitert bzw. ersetzt `GET /api/v1/fs/dirs` (alter Pfad kann als kompatibler Alias bleiben).
- Query-Parameter: `source=server|worker`, `worker_id` (Pflicht bei `source=worker`), `path` (optional; leer = Laufwerke/Wurzeln bzw. `\\<server_address>`), `include_files=true|false` (Standard `true`).
- Antwort: `source`, `worker_id`, `path`, `parent`, `breadcrumbs`, `entries` (`name`, `path`, `is_dir`, `size`, `modified_at`, `ext`), `total_count`, `truncated`.
- `max_entries = 10_000`; Metadaten über `os.scandir` + `entry.stat(follow_symlinks=False)`.
- **Rechteprüfung:** `source=worker` nur erlaubt mit gültigem `X-Marathon-Worker-Presence`-Ticket (bzw. `marathon_local_worker`-Cookie) für genau diese `worker_id` **oder** `current_user.role == 'admin'`.
- **Long-Poll-RPC:** Bei `source=worker` prüft der Server Berechtigung und Online-Status (sonst `503`), legt kurzlebige Anfrage `{ request_id, path, include_files }` (Timeout 10 s) an, weckt den offenen Long-Poll des Workers, erhält das `scandir`-Ergebnis via `POST /api/v1/worker/fs-result` und liefert es an den Browser zurück (ausschließlich ausgehende Verbindungen vom Worker zum Server).

## 3.7 Abnahmekriterien (Releases Zugang & Nutzer bis Lokale Watchfolder & Rechnerwechsel)

- Login per 6-stelligem Code funktioniert; 3 falsche Codes in 24 h sperren die Adresse für 24 h; Antworten verraten nicht, ob ein Konto existiert; Session überlebt Browser-Neustart und verlängert sich bei Aktivität.
- Selbstregistrierung akzeptiert nur exakt konfigurierte Domains; Break-Glass-Admin aus `marathon.toml` und CLI-Notfallcode funktionieren auch ohne SMTP.
- Workflow-Freigaben (privat, `view`, `run`, `edit`) greifen in UI und API; Workflows ohne Zugriff erscheinen nirgends; Admins verwalten Nutzer, Sessions, Eigentümer und Freigaben.
- Nutzerkonten und Worker sind vollständig entkoppelt: Eine Person vor einem Worker kann diesen über das Tray-Icon im Browser steuern (Starten, Stoppen/Drain, Sofort stoppen, Positiv-Verfügbarkeit) – ganz ohne Marathon-Nutzerkonto und ohne Zugriff auf fremde Worker.
- Ein eingeloggter Nutzer kann einen Workflow mit lokalen Pfaden an Rechner 1 anlegen, zu Rechner 2 wechseln, per 1-Klick-Umschaltung dort aktivieren (inkl. Pfadprüfung) und laufen lassen; fremde Workstations lassen sich ohne lokalen Presence-Nachweis nicht browsen oder kapern.
- Lokale Watchfolder binden Folgejobs per Sticky Locality bis zum ersten Netzwerkpfad und zeigen fehlende/abweichende/inaktive Worker mit direktem Link am Modul an.
- `FileBrowserDialog` beherrscht `Server` und `Dieser Rechner`, `Liste` und `Details`, Breadcrumb + Autocomplete, Verlauf (`←`/`→`/`↑`/`↻`), Filterfeld und scrollt 5.000+ Einträge flüssig.
