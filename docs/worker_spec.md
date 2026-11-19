# Release Worker-Steuerung – verbindlicher Umsetzungsvertrag

Grundlage: `docs/mission_log.md`, Abschnitte „Einstellungen“, „Worker“, „Warten“, „Module“ (je „- next“) und „unsortiert Martin“. Bezeichner im Code englisch, Texte für Nutzer deutsch und wörtlich wie hier. Wo dieser Vertrag `docs/engine_spec.md` widerspricht, gilt dieser Vertrag.

## 1. Konfiguration `config/marathon.toml`

```toml
[folder_dialog]
server_address = ""     # Standard für die Server-Adresse im Ordner-Dialog (Rechnername oder IP, z. B. 10.0.77.11); danach gilt der Wert aus der Oberfläche

[server_worker]
enabled = true                                      # Server-Worker mit dem Server starten
skills = ["copy_file", "move_file", "rename_file"]  # Fähigkeiten ab Werk, oben = höchste Prio; danach gilt die Worker-Seite
```

`[server_worker] max_parallel` entfällt; ein vorhandener Eintrag wird ignoriert. `host` bleibt die Adresse, an der Marathon erreichbar ist, und ist nicht in der Oberfläche änderbar.

```python
@dataclass(frozen=True)
class FolderDialogConfig:
    server_address: str = ""

@dataclass(frozen=True)
class ServerWorkerConfig:
    enabled: bool = True
    skills: tuple[str, ...] = ("copy_file", "move_file", "rename_file")

# ServerConfig bekommt zusätzlich:
#   folder_dialog: FolderDialogConfig = field(default_factory=FolderDialogConfig)
```

- `server_address`: Text, leer oder passend zu `^[A-Za-z0-9._-]{1,253}$`; sonst `ConfigError` „[folder_dialog] server_address in {datei} muss ein Rechnername oder eine IP-Adresse sein“.
- `skills`: Liste von Texten, je `^[a-z0-9_]{1,64}$`, ohne Doppelte; sonst `ConfigError` „[server_worker] skills in {datei} muss eine Liste von Modultyp-IDs ohne Doppelte sein“.

## 2. Datenbank

### 2.1 Migration `0008` (nur ergänzend)

| Tabelle | Änderung |
|---|---|
| `settings` (neu) | `key TEXT PRIMARY KEY`, `value JSONB NOT NULL`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` |
| `workers` | `skills JSONB NOT NULL DEFAULT '[]'`; `online BOOLEAN NOT NULL DEFAULT false`; `ck_workers_status` erweitert auf `('new','ready','busy','offline','locked','updating','stopping','stopped')` |
| `job_assets` | `outcome TEXT NULL` (Check `outcome IS NULL OR outcome IN ('result','error','cancelled','interrupted')`), `finished_at TIMESTAMPTZ NULL`, `error TEXT NULL` |
| `log_lines` | `asset_id TEXT NULL` (ohne Fremdschlüssel, damit der Log das Löschen eines Assets überlebt), Index `ix_log_lines_asset_id` |

Datenübernahme beim Upgrade:
- `workers.skills` = `capabilities` des Workers, sortiert nach Vorrang aus `priorities` (`preferred` 0, `normal` 1 bzw. fehlend, `last` 2), innerhalb gleichen Vorrangs in der Reihenfolge von `capabilities`; Einträge mit `off` entfallen.
- `workers.online` = `status <> 'offline'`.
- `job_assets.outcome`/`finished_at` für Jobs im Endzustand: `succeeded` → `result`, `failed` → `error`, `cancelled` → `cancelled`, `interrupted` → `interrupted`; `finished_at` = `jobs.finished_at`.

Downgrade: `stopping`, `stopped` → `locked`; alten Check wiederherstellen; neue Spalten, Index und Tabelle entfernen.

### 2.2 Migration `0009` (Aufräumen, erst nach Umstellung von Engine und Protokoll)

- `workers.status`: `new`, `locked` → `stopped`; `busy`, `offline`, `updating` → `ready`. Check `ck_workers_status` = `('ready','stopping','stopped')`.
- Spalten `workers.priorities`, `workers.max_parallel`, `workers.client_slots` samt Checks `ck_workers_max_parallel`, `ck_workers_client_slots` entfernen.
- Downgrade: Spalten mit alten Standardwerten zurück (`priorities '{}'`, `max_parallel 1`, `client_slots 1`), `stopping`/`stopped` → `locked`, Check aus `0008` wiederherstellen.

### 2.3 Standardwerte `marathon/server/db/seed.py`

```python
def seed_defaults(connection: Connection, config: ServerConfig) -> None
```

Aufruf in `python -m marathon.server.db upgrade` nach `upgrade_database()` in eigener Transaktion (also bei jedem `run.py`-Start). Schreibt nur, was fehlt; vorhandene Werte bleiben unverändert:
- `settings`: Schlüssel `folder_dialog.server_address`, Wert `config.folder_dialog.server_address` (JSON-Text).
- `workers`: Zeile `server-worker`, Name „Server-Worker“, `status 'ready'`, `online false`, `skills` = `config.server_worker.skills`, `capabilities '[]'`, `client_version ''` (`ON CONFLICT (id) DO NOTHING`).

## 3. Worker

### 3.1 Zustände

`status`: `ready` („bereit“), `stopping` („Wird gestoppt“), `stopped` („gestoppt“, rot). Davon getrennt `online` (bool, Anzeige „online“ / „offline“). Bis `0009` können alte Werte noch in der Datenbank stehen; neuer Code schreibt nur die drei neuen.

| Ereignis | Wirkung |
|---|---|
| erste Anmeldung | `status 'stopped'` (Worker-ID `server-worker`: `'ready'`), `skills` = gemeldete `capabilities` in gemeldeter Reihenfolge, `online true` |
| erneute Anmeldung | `capabilities`, `client_version`, `ip_address`, `last_seen`, `online true`; `status`, `name`, `skills` bleiben; aktive Jobs wie bisher (`assigned` → `drop_unstarted_job`, sonst `interrupt_job("Worker neu gestartet")`) |
| Anfrage (Long-Poll) | `last_seen`, `online true` |
| Engine: `COALESCE(last_seen, created_at) < now - worker_timeout_s` | aktive Jobs wie bisher; `online false`; `status` bleibt |
| `POST /workers/{id}/start` | `stopped` → `ready`; `ready` bleibt; `stopping` → 409 „Worker wird gerade gestoppt“ |
| `POST /workers/{id}/stop` | `ready` → `stopping`; jeder aktive Job des Workers → `cancel_job(reason="Worker gestoppt")`; hat der Worker danach keinen aktiven Job → sofort `stopped`; `stopped`/`stopping` bleiben |
| Engine-Takt | `stopping` ohne aktiven Job → `stopped` |

Stoppen zählt nicht als Versuch: bestätigter Abbruch → `return_asset` (bestehende Regel). Ein fertiges Ergebnis, das während `cancelling` eintrifft, wird wie bisher übernommen; unfertige Kopien entfernen die Module selbst.

### 3.2 Verwaltung

- `PUT /workers/{id}` Eingabe `{"name": str, "skills": list[str]}`: `name` 1–100 Zeichen nach `strip()` („Name fehlt“, „Name ist zu lang (max. 100 Zeichen)“); jede Fähigkeit `^[a-z0-9_]{1,64}$` („Ungültiger Modultyp: {id}“), ohne Doppelte („Fähigkeit doppelt: {id}“), in `capabilities` des Workers („Fähigkeit wird vom Rechner nicht gemeldet: {id}“) → sonst 422. 404 „Worker nicht gefunden“.
- `POST /workers/{id}/approve` und `/lock` entfallen.
- Worker-Datensatz (`list_workers`, `get_worker`, Dashboard): `id`, `name`, `status`, `online`, `capabilities`, `skills`, `running_jobs` (Anzahl aktiver Jobs), `client_version`, `last_seen` (`COALESCE(last_seen, created_at)`), `ip_address`. `max_parallel`, `priorities`, `client_slots` entfallen.

### 3.3 Zuteilung (`marathon/server/engine/tick.py`)

- Ein Worker hat höchstens **einen** aktiven Job. Kandidaten: `status = 'ready' AND online` und kein aktiver Job.
- Angebot = `skills`, gefiltert auf `capabilities`, Reihenfolge von `skills`. Rang für Modultyp T = Position von T im Angebot (0 = oben); nicht enthalten = kein Kandidat.
- Fairness wie bisher (Durchgänge über laufende Workflows nach `last_assigned_at NULLS FIRST, id`, je Durchgang höchstens ein neuer Job je Workflow). Im Workflow das erste `waiting`-Asset (nach `list_position, id`, höchstens 200 geprüft) an einem Worker-Modul, für dessen Modultyp es einen Kandidaten gibt. Bester Kandidat = kleinster (Rang, Name, ID).
- Der Job bündelt dieses Asset und weitere `waiting`-Assets **desselben Moduls** in Listenreihenfolge bis zur Losgröße. Losgröße = Moduleinstellung `parallel` (ganze Zahl 1–16), sonst 1. Assets ohne Dateipfad → `fail_asset("Asset hat keine Datei")`, sie kommen nicht in den Job.
- Beispiel: A [copy_file, move_file], B [move_file, copy_file] → Kopieren vorrangig an A, Verschieben an B; ist A belegt, kopiert B.
- Schritte je Takt: Ausfälle (`online false` statt `offline`), Abhol-Frist, Pausieren abschließen, Worker-Stopp abschließen (`stopping` → `stopped`), Assets bewegen, Zuteilen. Der Schritt „Beschäftigt“ (`ready` ⇄ `busy`) entfällt.

## 4. Jobs mit mehreren Assets (`marathon/server/engine/jobs.py`)

```python
def create_batch_job(connection, asset_ids: Sequence[str], graph: Graph, worker_id: str, now: datetime, pickup_timeout_s: float) -> str
def create_job(connection, asset_id: str, graph: Graph, worker_id: str, now: datetime, pickup_timeout_s: float) -> str  # = create_batch_job([asset_id], ...)
def apply_asset_result(connection, job_id: str, asset_id: str, worker_id: str | None, result: Mapping[str, Any], now: datetime, max_attempts: int) -> bool
def apply_job_result(connection, job_id: str, worker_id: str | None, result: Mapping[str, Any], now: datetime, max_attempts: int) -> bool
def create_engine_job(connection, asset_id: str, graph: Graph, now: datetime) -> str
```

- `create_batch_job`: alle Assets `in_progress`/`waiting` am selben Modul des Graphen, keines mit aktivem Job, mindestens eins (sonst `ValueError`). Job wie bisher (`job-<12 hex>`, `assigned`, `pickup_deadline`, `type_id`, `version`, `settings`), `attempt` = größtes `attempts` der Assets + 1; je Asset eine Zeile in `job_assets`, Asset `step_status 'running'`, Log (info, mit `asset_id`) „Zugeteilt an {workername}“.
- `apply_asset_result`: Schlüssel von `result` wie bisher (`outcome`, `output`, `log`, `asset_path`, `spawned`, `error`).
  - Job unbekannt oder Asset nicht im Job → `JobNotFound`; `worker_id` ≠ Worker des Jobs → `JobWorkerMismatch`.
  - Job im Endzustand oder `job_assets.outcome` dieses Assets schon gesetzt → Log (warn, `asset_id`) „Spätes Ergebnis verworfen“, `False`.
  - Asset steht nicht `running` am Modul des Jobs → nur `job_assets` abschließen, Log (warn) „Asset passt nicht zum Job“.
  - `cancelled`: bei Job `cancelling` → `outcome 'cancelled'`, `return_asset`; sonst Unterbrechungsregel für dieses Asset mit Grund „Worker beendet“, `outcome 'interrupted'`.
  - `error`: `outcome 'error'`, `job_assets.error`; weiter wie bisher (`error`-Ausgang oder Fehlgeschlagen).
  - `result`: ungültiger Ausgang → wie `error` mit „Ungültiger Ausgang: {output}“; sonst `outcome 'result'`, `job_assets.output_id`, Ergebnis wie bisher anwenden.
  - Immer `job_assets.finished_at = now`, danach Log (info bzw. error, `asset_id`) „Ergebnis: {label}“ mit `label` = Beschriftung des Ausgangs aus `module_types.outputs`, „Erledigt“ bei `output` None, „Abgebrochen“ bei `cancelled`, „Fehler“ bei `error`. Rückgabe `True`.
  - Abschluss: Hat jedes Asset des Jobs ein `outcome`, endet der Job: `cancelled`, wenn er `cancelling` war und ein Asset `cancelled` ist; sonst `interrupted`, wenn ein Asset `interrupted` ist; sonst `failed` (`error` = erster Fehlertext), wenn ein Asset `error` ist; sonst `succeeded`. `output_id` = gemeinsamer Ausgang aller Assets, sonst NULL; `progress 100`; `finished_at`. Solange Assets offen sind: `progress` = gerundeter Anteil abgeschlossener Assets in %.
- `apply_job_result`: wendet dasselbe Ergebnis in `asset_id`-Reihenfolge auf jedes Asset mit offenem `outcome` an; Job im Endzustand → Log (warn) „Spätes Ergebnis verworfen“, `False`. Für Jobs mit einem Asset bleibt das Verhalten unverändert.
- `interrupt_job`: alle offenen Assets → `outcome 'interrupted'` und Unterbrechungsregel. `drop_unstarted_job`: alle Assets → `outcome 'cancelled'`, Asset `waiting` am selben Modul. `cancel_job`: `assigned` → alle `cancelled` + `return_asset`; `running` → `cancelling`. Engine-Job (`worker_id` NULL): sofort `cancelled`, alle offenen Assets `outcome 'cancelled'`, `return_asset` (setzt `wait_until` NULL).
- `fail_asset` und jede Log-Zeile, die ein einzelnes Asset betrifft, schreiben `log_lines.asset_id`.

### 4.1 Warten als Engine-Job

- `create_engine_job`: Asset `waiting` an einem Modul vom Typ `wait` → Job `running`, `worker_id` NULL, `started_at = now`, `attempt` = `attempts + 1`, `type_id 'wait'`, `version`, `settings`; Zeile in `job_assets`; Asset `step_status 'running'`, `wait_until = now + wait_s` (Standard 60); Log (info, `asset_id`) „Wartezeit gestartet: {wait_s:g} s“.
- Engine-Takt „Assets bewegen“: `waiting` am Warten-Modul → `create_engine_job` (statt nur `wait_until` zu setzen). Die Wartezeit gilt je Asset ab seiner Übergabe. `running` mit `wait_until <= now`: aktiver Engine-Job des Assets → `apply_asset_result(..., worker_id=None, result={"outcome": "result", "output": "success", "log": ["Wartezeit abgelaufen"]})`; ohne Job (Altbestand) wie bisher.
- Pausieren und entfernte Module brechen Engine-Jobs sofort ab (Regel in `cancel_job`).

## 5. Worker-Schnittstelle, Protokoll 2

- `POST /worker/register`: `protocol` muss 2 sein, sonst 409 „Protokollversion nicht unterstützt“. Eingabe `worker_id`, `name`, `client_version`, `capabilities`, `protocol`; unbekannte Felder (auch `max_parallel`) werden ignoriert. Antwort `{"worker_id", "status", "protocol": 2, "poll_timeout_s"}`.
- `POST /worker/poll`: `jobs` = `[{"job_id", "type_id", "settings", "assets": [{"id", "name", "path"}, …]}]` (Assets nach `id`); `cancel` wie bisher.
- `POST /worker/jobs/{job_id}/start`, `/progress` unverändert.
- `POST /worker/jobs/{job_id}/assets/{asset_id}/result`: Eingabe wie `/result` → `apply_asset_result` → `{"accepted": bool}`; 404 „Job nicht gefunden“ (auch Asset nicht im Job); 403 „Job gehört einem anderen Worker“.
- `POST /worker/jobs/{job_id}/result` bleibt (`apply_job_result`).
- Server-Stopp: `app.state.shutdown_event` (`threading.Event`) wird gesetzt, sobald Uvicorn das Beenden beginnt, und spätestens im Lifespan-Ende. Eine offene Anfrage antwortet dann innerhalb von 0,5 s mit `{"jobs": [], "cancel": []}`; keine Ausnahme und kein Traceback in der Konsole.

## 6. Worker-Laufzeit `marathon/worker/`

```
python -m marathon.worker --server URL --worker-id ID --name NAME [--exit-on-stdin-eof]
```

- `--max-parallel` entfällt; `protocol` = 2; Anmeldung ohne `max_parallel`.
- Höchstens ein Job gleichzeitig: Ein angebotener Job wird nur gestartet, wenn keiner läuft. Nach `start` (200) läuft je Asset ein eigener Thread mit `catalog_module(type_id).run(ModuleJob(asset_path, settings, stop))`; alle Assets des Jobs teilen ein `threading.Event`. Jedes Asset meldet sein Ergebnis einzeln über `/worker/jobs/{job_id}/assets/{asset_id}/result` (Wiederholung wie bisher alle 2 s bis Erfolg, 403/404 oder 120 s; beim Beenden höchstens ein Versuch). Der Job gilt lokal als beendet, wenn alle Asset-Threads fertig sind.
- `cancel` setzt das Event des Jobs. Beenden: Event setzen, höchstens 10 s auf alle Asset-Threads warten, Exit 0.
- Server-Worker-Kommando ohne `--max-parallel`.

## 7. Einstellungen

- `GET /api/v1/settings` → `{"folder_dialog": {"server_address": str}}` (fehlt der Datensatz: `""`).
- `PUT /api/v1/settings` mit derselben Form: `server_address` nach `strip()` leer oder `^[A-Za-z0-9._-]{1,253}$`, sonst 422 „Server-Adresse: nur Rechnername oder IP-Adresse, z. B. 10.0.77.11“; speichert (Upsert, `updated_at`) und antwortet mit dem gespeicherten Stand.
- Einstellungsseite: nur echte Einstellungen. Abschnitt „Ordner-Dialog“, Feld „Server-Adresse“ (Hilfe: „Rechnername oder IP-Adresse; „Server“ im Ordner-Dialog zeigt die Freigaben von \\\\<Adresse>“). „Speichern“ speichert, Meldung „Gespeichert“. Die bisherigen Platzhalter (Server, Rollout, Aufräumen) entfallen.
- Ordner-Dialog: Statt „Laufwerke“ ein Umschalter „Laufwerke“ | „Server“. „Laufwerke“ zeigt die Laufwerke wie bisher, „Server“ lädt `\\<Server-Adresse>`. Aktiv ist „Server“, solange der angezeigte Pfad mit `\\` beginnt. Ohne Adresse: Hinweis „Keine Server-Adresse eingestellt (Einstellungen)“.

## 8. Module und Überwachen

- Kopieren und Verschieben bekommen die Einstellung `{"key": "parallel", "label": "Parallele Verarbeitung", "type": "number", "default": 1, "min": 1, "max": 16, "help": "So viele Assets bearbeitet ein Job gleichzeitig, jedes in einem eigenen Strom"}`; `validate_settings`: ganze Zahl (kein bool) 1–16, sonst „Ganze Zahl von 1 bis 16“.
- Zähler „erledigt“ je Modul = Anzahl `job_assets` von Jobs dieses Moduls mit `outcome 'result'` und `output_id IS DISTINCT FROM 'error'`. Das Warten-Modul zeigt nur „läuft“, „erledigt“, „Fehler“.
- Modul-Log (Reiter „Logs“): alle Log-Zeilen des Moduls (auch die von Jobs), die neuesten 500, aufsteigend nach Zeit; je Zeile Zeit, Stufe, Asset (Name oder –), Worker (Name oder –) und Text. Asset über `log_lines.asset_id`, Worker über den Job.
- Asset-Ansicht, Zeitleiste: Ausgang je Asset aus `job_assets.output_id`.
- Prioliste: Menüpunkt „Metadaten bearbeiten“ heißt „Bearbeiten“.
- Überwachen → Einstellungen: Die Werte sind ausgegraut (nur lesen).
- Verbindungen: Anfang und Ende der Linie liegen am äußeren Rand der Kreise von Ausgang und Eingang, auch beim Ziehen.
- Erneut verarbeiten: keine Obergrenze für die Zahl der Assets.

## 9. Oberfläche Worker

- Liste: Schaltfläche Starten/Stoppen je Zeile (wie bei Workflows), Name, Status (bereit / Wird gestoppt / gestoppt), Verbindung (online / offline; ohne Server-Verbindung immer offline), Fähigkeiten (Modulnamen in Rangfolge), Jobs (laufende Jobs), Client-Version, Zuletzt gesehen. Kacheln: bereit, Wird gestoppt, gestoppt, offline.
- Kopfleiste: „Bearbeiten“, „Starten“ / „Stoppen“ je nach Auswahl (wie bei Workflows), „Version ausrollen“ (weiter „Noch nicht verfügbar“).
- Bearbeiten (auch Server-Worker): Name und Fähigkeiten als sortierbare Liste im Stil der Prioliste (Hoch/Runter, Entfernen, „Fähigkeit hinzufügen“ aus den gemeldeten, noch nicht gelisteten Modultypen). Hilfetext: „Weiter oben = höhere Priorität. Nicht in der Liste = übernimmt diesen Modultyp nicht.“
