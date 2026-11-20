# Release Infrastruktur – verbindlicher Architektur- und Umsetzungsvertrag (v2)

Grundlage: `docs/mission_log.md` (Abschnitt „Nächster Release: Infrastruktur – Nutzer, Worker-Client, lokale Watchfolder & Datei-Dialog“) sowie bestehende Verträge `docs/worker_spec.md` und `docs/engine_spec.md`. Bezeichner im Code englisch (`snake_case`), Texte für Nutzer deutsch. Wo dieser Vertrag älteren Specs widerspricht, gilt dieser Vertrag.

**Architektur-Leitprinzip (Entkopplung):** Nutzerkonten (`users`) und Worker-Rechner (`workers`) sind **streng voneinander getrennt**. Nutzer erhalten Zugriff auf Workflows über ihren Account; Worker authentifizieren sich als eigenständige Geräte über ihr individuelles Worker-Token. Wer physisch vor einem Worker sitzt, steuert diesen über den Tray-Client im Browser – auch ohne Marathon-Nutzerkonto. Wer als eingeloggter Nutzer lokale Workflows baut oder startet, nutzt den Rechner, an dem der Browser gerade läuft, und kann Workflows beim Wechsel auf einen anderen Rechner einfach dort weiterbetreiben.

---

## 1. Konfiguration `config/marathon.toml` und Geheimnisse

```toml
[auth]
public_url = "http://127.0.0.1:8080"   # Basis-URL für Magic-Links und Worker-Links (ohne abschließenden Slash)
admin_emails = []                      # Break-Glass-Admins (immer Admin, in UI nicht herabstufbar/löschbar)
allowed_domains = []                   # Startwert für erlaubte Mail-Domains (danach gilt DB-Einstellung)
session_days = 180                     # Sliding-Expiration-Dauer einer Nutzer-Session in Tagen (1–365)
magic_link_ttl_minutes = 15            # Gültigkeit von Login-Link und Einmalcode in Minuten (1–60)
cookie_secure = false                  # true bei HTTPS / Reverse-Proxy; false für reines LAN-HTTP

[smtp]
host = ""                              # SMTP-Server (leer = Mailversand nicht konfiguriert)
port = 587                             # 1–65535
username = ""                          # Optionaler Login-Name
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
  - `admin_emails` und `allowed_domains` werden auf Kleinbuchstaben normalisiert und auf gültige Syntax geprüft; ungültige Einträge liefern `ConfigError`.
  - `session_days` (1–365), `magic_link_ttl_minutes` (1–60), `smtp.port` (1–65535), `worker_client.local_bridge_port` (1024–65535), `worker_client.control_session_hours` (1–168).
- **Seed (`marathon/server/db/seed.py`):**
  - `settings`: Schlüssel `auth.allowed_domains` wird beim Start mit `config.auth.allowed_domains` angelegt, sofern noch kein Eintrag existiert; danach gilt die Datenbank.
  - `users`: Jede Adresse aus `config.auth.admin_emails` wird beim Start angelegt oder auf `role = 'admin', active = true` synchronisiert.

---

## 2. Datenbank (Migration `0010_infrastructure.py`)

Ausschließlich ergänzende Migration auf Basis von `0009_worker_cleanup.py`, mit vollständigem Downgrade.

### 2.1 Neue Tabellen

| Tabelle | Spalten & Constraints |
|---|---|
| `users` | `id TEXT PRIMARY KEY`, `email TEXT NOT NULL UNIQUE`, `display_name TEXT NOT NULL DEFAULT ''`, `role TEXT NOT NULL CHECK (role IN ('user','admin'))`, `active BOOLEAN NOT NULL DEFAULT true`, `external_subject TEXT NULL UNIQUE`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `last_login_at TIMESTAMPTZ NULL` |
| `auth_tokens` | `id TEXT PRIMARY KEY`, `email TEXT NOT NULL`, `token_hash TEXT NOT NULL UNIQUE`, `code_hash TEXT NOT NULL`, `expires_at TIMESTAMPTZ NOT NULL`, `consumed_at TIMESTAMPTZ NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` · Index `(email, expires_at)` |
| `sessions` | `id TEXT PRIMARY KEY`, `user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE`, `token_hash TEXT NOT NULL UNIQUE`, `expires_at TIMESTAMPTZ NOT NULL`, `last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `user_agent TEXT NOT NULL DEFAULT ''`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` · Index `(user_id)` |
| `user_module_permissions` | `user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE`, `module_type TEXT NOT NULL`, `allowed BOOLEAN NOT NULL`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `PRIMARY KEY (user_id, module_type)` |
| `workflow_shares` | `workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE`, `user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE`, `access TEXT NOT NULL CHECK (access IN ('view','edit'))`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `PRIMARY KEY (workflow_id, user_id)` |
| `worker_ephemeral_tokens` | `token_hash TEXT PRIMARY KEY`, `worker_id TEXT NOT NULL REFERENCES workers(id) ON DELETE CASCADE`, `purpose TEXT NOT NULL CHECK (purpose IN ('control_link','presence'))`, `expires_at TIMESTAMPTZ NOT NULL`, `consumed_at TIMESTAMPTZ NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` · Index `(worker_id, expires_at)` |
| `worker_control_sessions` | `id TEXT PRIMARY KEY`, `worker_id TEXT NOT NULL REFERENCES workers(id) ON DELETE CASCADE`, `token_hash TEXT NOT NULL UNIQUE`, `expires_at TIMESTAMPTZ NOT NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` · Index `(worker_id)` |

### 2.2 Erweiterungen bestehender Tabellen

| Tabelle | Änderung |
|---|---|
| `workflows` | `owner_user_id TEXT NULL REFERENCES users(id) ON DELETE SET NULL` · Index `(owner_user_id)` |
| `workers` | `token_hash TEXT NULL UNIQUE`; `approved BOOLEAN NOT NULL DEFAULT true`; `availability_config JSONB NOT NULL DEFAULT '{}'`; `client_available BOOLEAN NOT NULL DEFAULT true`; `availability_reason TEXT NOT NULL DEFAULT ''`; `ck_workers_status` erweitern auf `('ready','draining','stopping','stopped')` |
| `assets` | `locality_worker_id TEXT NULL REFERENCES workers(id) ON DELETE SET NULL` · Index `(locality_worker_id)` |

*(Bewusst **kein** `owner_user_id` auf `workers` – Worker gehören Rechnern, nicht Nutzerkonten.)*

---

## 3. Zugang, Authentifizierung & Berechtigungen

### 3.1 Auth-Schnittstelle (Andockstelle fürs spätere Ökosystem)

- `marathon/server/auth/provider.py` definiert eine klare Schnittstelle (`resolve_session`, `start_login`, `complete_login`).
- Standard-Implementierung: `MagicLinkProvider`.
- Durch `users.external_subject` und normalisierte `users.email` kann später ein OIDC- oder Reverse-Proxy-Header-Provider eingehängt werden, ohne `users`, `workflow_shares` oder `user_module_permissions` umzubauen.

### 3.2 Magic-Link- & Code-Anmeldung (SafeLinks-sicher)

1. **`POST /api/v1/auth/request-link`** mit `{ "email": "…" }`:
   - E-Mail trimmen & `casefold()`.
   - Prüfen: Existiert der Nutzer und ist `active = true`? Falls nein: Liegt die Domain exakt in `settings['auth.allowed_domains']` oder steht die Adresse in `config.auth.admin_emails`? Sonst `403` mit *„Diese E-Mail-Domain ist nicht freigegeben“*. Ist ein bestehendes Konto deaktiviert: `403` mit *„Dieses Konto ist deaktiviert“*.
   - Erzeugt kryptografisch starken Token (`secrets.token_urlsafe(32)`) und 6-stelligen Zifferncode; speichert nur SHA-256-Hashes in `auth_tokens` (gültig für `magic_link_ttl_minutes`).
   - Sendet E-Mail über den zentralen SMTP-Dienst mit Link `<public_url>/login/verify?token=<token>` **und** dem 6-stelligen Code.
   - Rate-Limit: max. 5 Anfragen pro E-Mail / 15 Minuten.
2. **`GET /api/v1/auth/verify?token=<token>`**:
   - Prüft ausschließlich, ob der Token existiert, nicht abgelaufen und nicht verbraucht ist. **Verbraucht den Token nicht** (Schutz gegen automatische Firmen-Linkscanner).
3. **`POST /api/v1/auth/confirm`** mit `{ "token": "…" }` **oder** `{ "email": "…", "code": "123456" }`:
   - Konsumiert den Eintrag atomar (`UPDATE auth_tokens SET consumed_at = now() WHERE … AND consumed_at IS NULL AND expires_at > now()`).
   - Existiert kein `users`-Eintrag, wird er jetzt angelegt (`role = 'admin'` wenn in `admin_emails`, sonst `'user'`).
   - Erstellt Session in `sessions`, setzt Cookie `marathon_session` (`HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` gemäß `cookie_secure`, `Max-Age = session_days * 86400`).
4. **Sliding Expiration:** Bei authentifizierten Anfragen wird `sessions.expires_at` automatisch verlängert, sobald weniger als 80 % der Laufzeit übrig sind (und das Cookie im Response aufgefrischt).
5. **`POST /api/v1/auth/logout`**, **`GET /api/v1/auth/me`**: Session serverseitig löschen bzw. aktuellen Nutzer inkl. Rolle und erlaubter Modultypen liefern.

### 3.3 Break-Glass-Admin

- Alle Adressen aus `config.auth.admin_emails` sind geschützte Bootstrap-Admins:
  - In der UI sind Rolle und Aktiv-Status gesperrt (Hinweis *„In Konfiguration festgelegt“*).
  - API lehnt Herabstufen/Deaktivieren geschützter Admins sowie des letzten aktiven Admins mit `409 Conflict` ab.
- **Lokaler CLI-Notfallzugang (ohne SMTP):**
  - `python run.py admin-link <email>` (ruft `python -m marathon.server.auth admin-link <email>` auf).
  - Erzeugt für einen aktiven Admin (oder eine Adresse aus `admin_emails`) einen 15-Minuten-Eintrag in `auth_tokens` und gibt den fertigen Link + 6-stelligen Code ausschließlich lokal auf stdout aus.

### 3.4 Freigaben für Workflows und Module

#### Modultyp-Freigaben
- Standard für normale Nutzer: **alle Modultypen freigegeben** (Opt-out über `user_module_permissions.allowed = false`).
- Admins haben immer alle Modultypen freigegeben und können je Nutzer einzelne Modultypen sperren/freigeben (`PUT /api/v1/users/{user_id}/modules`).
- Im Workflow-Editor zeigt die Modul-Palette nur für den Nutzer freigegebene Modultypen.

#### Workflow-Freigaben (`workflow_shares`)
- Stufen je `(workflow_id, user_id)`:
  - Kein Eintrag = **nicht geteilt** (kein Zugriff).
  - `view` = **Teilen zur Ansicht**: Workflow, Module, Assets, Logs und Status ansehen; **kein** Bearbeiten, **kein** Speichern, **kein** Starten/Stoppen/Zeitplan ändern, **kein** erneutes Verarbeiten.
  - `edit` = **Teilen zur Bearbeitung und Ausführung**: zusätzlich Bearbeiten, Speichern, Starten, Stoppen, Zeitplan, Assets erneut verarbeiten.
- Eigentümer (`owner_user_id`) hat implizit `edit` (solange kein verwendetes Modul gesperrt wurde); Admins haben immer Vollzugriff und können Eigentümer/Freigaben verwalten.

#### Verbindliche Konsistenzregeln (Modul- × Workflow-Freigabe)
1. **Freigabeversuch auf `edit` (`PUT /api/v1/workflows/{id}/shares`):**
   - Server ermittelt alle im Workflow verwendeten Modultypen und prüft sie gegen die gesperrten Modultypen des Zielnutzers.
   - Ist mindestens ein Modultyp gesperrt → `409 Conflict` mit `detail`: *„Freigabe zur Bearbeitung nicht möglich: Für <Nutzer> sind folgende Module gesperrt: <Modulnamen>. Es ist nur die Freigabe zur Ansicht möglich.“* und `blocked_modules: [...]`.
2. **Nachträgliche Modulsperrung (`effective_access`):**
   - Effektive Stufe wird bei jedem API-Aufruf live berechnet: Hat ein Nutzer `edit` (oder Eigentümerschaft), enthält der Workflow aber einen für ihn gesperrten Modultyp, fällt die **effektive** Stufe automatisch auf `view` zurück (`effective_access = 'view'`, `downgraded_reason = 'blocked_modules'`, `blocked_modules = [...]`).
   - UI zeigt ein klares Hinweisbanner und schaltet Editor/Steuerung auf schreibgeschützte Ansicht um. Wird das Modul später wieder freigegeben (oder aus dem Workflow entfernt), greift wieder `edit`.
3. **Save-Guard bei laufender Bearbeitung (`PUT /api/v1/workflows/{id}`):**
   - Beim Speichern prüft der Server sowohl die effektive Berechtigung am bestehenden Workflow **als auch** alle Modultypen im eingereichten neuen Graphen gegen die aktuellen Modulfreigaben des Nutzers.
   - Ist mindestens ein Modultyp gesperrt → `403 Forbidden` mit *„Speichern nicht möglich: Folgende Module sind für Dich gesperrt: <Modulnamen>.“*; der Workflow bleibt unverändert.
   - Ändert ein Admin Modulfreigaben eines Nutzers, sendet der Server das SSE-Event `permissions.changed` für diesen Nutzer, sodass geöffnete Editor-Ansichten sofort warnen.

### 3.5 Rollen-Abgrenzung in Oberfläche, API & Live-Kanal

- **Admin (eingeloggt):** Voller Zugriff auf alle Workflows, alle Worker (inkl. Server-Worker, Zulassung und Fähigkeiten-Rangliste), Servereinstellungen (`/settings`), Nutzer- und Modulfreigaben (`/users`).
- **Normaler Nutzer (eingeloggt):**
  - Sieht keine Servereinstellungen (`/settings`), keine Nutzerverwaltung (`/users`) und keine Gesamtliste fremder Worker (entsprechende Admin-Routen liefern `403`).
  - Sieht in Workflows/Dashboard/Reports/SSE-Events ausschließlich eigene und für ihn freigegebene Workflows.
  - Sitzt er vor einem Rechner mit laufendem Worker-Client, kann er genau diesen lokalen Worker im Browser steuern (über Tray-Link oder den lokalen Worker-Link in der UI) und für lokale Workflows aktivieren.
- **Person vor einem Worker OHNE Marathon-Login:**
  - Öffnet per Doppelklick / Rechtsklick im Tray-Icon die dedizierte Steuerungsansicht `/worker-control` (abgesichert über das lokale `marathon_worker_control`-Cookie für genau diese `worker_id`).
  - Darf dort ausschließlich **diesen einen Worker** starten, stoppen (sanft), sofort stoppen und dessen Verfügbarkeitsregeln pflegen; sieht keine Workflows, keine Assets, keine Nutzer, keine Servereinstellungen und keine fremden Worker.

---

## 4. Worker-Client (Windows-Tray), Entkopplung & Verfügbarkeitsregeln

### 4.1 Client-Aufbau, Registrierung & Systemtray

- Paketstruktur: `marathon/client/` (dünner Starter + Tray-Steuerung + Loopback-Bridge) nutzt die bestehende Laufzeit `marathon/worker/runtime.py`; eigene gepinnte `requirements/client.txt` (u. a. `pystray`, `Pillow`, `psutil` für CPU/RAM/Prozesse; Windows-API `GetLastInputInfo` via `ctypes`; GPU-Auslastung best-effort via `nvidia-smi`/NVML oder Windows-PDH, ohne Absturz wenn kein GPU-Sensor vorliegt).
- **Laufzeitkontext unter Windows:** Läuft im **Benutzerkontext** (Autostart / Benutzer-Aufgabenplanung), **nicht** als Windows-Dienst in Session 0 – nur so stehen Tray-Icon, Benutzereingaben (`GetLastInputInfo`) und lokale/gemappte Laufwerke zur Verfügung.
- **Geräte-Registrierung ohne Nutzerkonto (`POST /api/v1/worker/enroll`):**
  1. Startet der Client ohne `%LOCALAPPDATA%\MarathonWorker\worker.json`, sendet er `POST /api/v1/worker/enroll` mit `{ "hostname", "platform", "client_version" }`.
  2. Der Server erzeugt eine stabile `worker_id` und ein kryptografisches `worker_token` (`secrets.token_urlsafe(32)`), speichert nur `token_hash` (SHA-256) in `workers`, setzt `status = 'stopped'` sowie `approved = not config.worker_client.require_admin_approval` und gibt `{ "worker_id", "worker_token", "approved" }` genau einmal zurück.
  3. Der Client speichert `server_url`, `worker_id` und `worker_token` lokal in `%LOCALAPPDATA%\MarathonWorker\worker.json`.
  4. **Worker-Authentifizierung (`marathon/server/worker_auth.py`):** Jede Anfrage unter `/api/v1/worker/…` prüft `Authorization: Bearer <token>` per SHA-256-Hash gegen `workers.token_hash` und bindet den Request strikt an diese eine `worker_id` (der interne `server-worker` behält sein eigenes lokales Server-Token).
- **Kein eigenes Einstellungs-UI im Tray:**
  - Doppelklick oder Rechtsklick → **„Worker-Einstellungen öffnen“** ruft mit dem eigenen `worker_token` `POST /api/v1/worker/control-link` auf und öffnet die zurückgegebene Einmal-URL im Standardbrowser (siehe 4.2).
  - Rechtsklick-Menü minimal: *Worker-Einstellungen öffnen*, *Diesen Rechner im Browser verknüpfen* (Fallback für lokale Erkennung), *Status: <Zustand + Grund>* (Infozeile), *Beenden*.
- **Download aus Marathon:** `GET /api/v1/client/download` liefert das von `build.py` erzeugte Client-Paket mit vorausgefüllter `server_url` aus.

### 4.2 Lokale Worker-Steuerung ohne Marathon-Login (`/worker-control`)

1. Client sendet `POST /api/v1/worker/control-link` (authentifiziert mit `worker_token`).
2. Server legt einen Eintrag in `worker_ephemeral_tokens` (`purpose = 'control_link'`, TTL 60 s) an und liefert `{ "url": "<public_url>/worker-control?token=<einmal_token>" }`.
3. Browser öffnet die URL → `GET /api/v1/worker-control/exchange?token=<einmal_token>` (bzw. Bestätigungs-POST auf der Route) konsumiert den Einmal-Token atomar, legt eine `worker_control_sessions`-Zeile an (TTL `control_session_hours`), setzt das `HttpOnly; SameSite=Lax`-Cookie `marathon_worker_control` und zeigt die fokussierte Einstellungsseite genau dieses Workers.
4. Über diese Session sind **ausschließlich** Lesen/Steuern/Verfügbarkeitsregeln dieser einen `worker_id` erlaubt (`GET/PUT /api/v1/worker-control/self`, `POST /api/v1/worker-control/self/{start|stop|stop-now}`), sowie optional ein Live-Event-Stream nur für diesen einen Worker. Fähigkeiten-Rangliste bleibt schreibgeschützt (nur Admin). Ist `worker.approved == false`, zeigt die Seite den Hinweis *„Dieser Worker wartet auf Freigabe durch einen Administrator“*.

### 4.3 Lokale Rechner-Erkennung im Browser (Loopback-Bridge + Fallback)

Damit eine eingeloggte Marathon-Webseite weiß, an welchem Rechner der Nutzer gerade sitzt (für lokale Watchfolder, Rechnerwechsel und den Datei-Dialog `Dieser Rechner`), ohne User und Worker dauerhaft zu verknüpfen:

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

### 4.4 Steuerbefehle & Zustände (`ready`, `draining`, `stopping`, `stopped`)

| Aktion | Zielzustand | Verhalten |
|---|---|---|
| **Starten** | `ready` | Worker darf Jobs annehmen (sofern `approved = true`, `online = true` und `client_available = true`). |
| **Stoppen** (sanft / Drain) | `draining` | Server vergibt keine neuen Jobs mehr. Laufende Jobs laufen sauber zu Ende. Sobald `running_jobs == 0`, wechselt der Server automatisch auf `stopped`. |
| **Sofort stoppen** (hart) | `stopping` | Laufende Jobs werden über das Abbruchsignal hart abgebrochen; Assets gehen zurück auf den letzten fertigen Ausgang und werden neu verteilt (zählt nicht als Fehlversuch). Nach Abbruch → `stopped`. |

### 4.5 Verfügbarkeitsregeln als Positivliste (`availability_config`)

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
   - `mode == "rules"` → positiv erfüllt, wenn:
     - (`schedule.enabled` **und** aktuelle lokale Uhrzeit liegt in einem positiven Fenster für den heutigen Wochentag) **ODER**
     - (`idle.enabled` **und** Inaktivitätsbedingung erfüllt).
3. **Inaktivitätsbedingung (Schutz vor Selbst-Deaktivierung):**
   - **Im Leerlauf (kein aktiver Marathon-Job):** Keine Maus-/Tastatureingabe seit `>= idle.minutes` (`GetLastInputInfo`) **UND** geglättete Systemwerte (30-Sekunden-Mittel) für CPU `< max_cpu_percent`, RAM `< max_ram_percent` und GPU `< max_gpu_percent` (falls GPU-Sensor vorhanden).
   - **Während ein Marathon-Job läuft:** CPU/RAM/GPU-Schwellenwerte werden ausgesetzt (da der eigene Job Last erzeugt); geprüft werden nur noch Maus-/Tastatureingabe und `blocked_processes`.
4. **Übergang `available: true → false` während laufendem Job:**
   - Client meldet sofort `available: false` samt Grund an den Server; laufende Jobs laufen sauber zu Ende (Drain). Neue Jobs vergibt der Server erst wieder bei `available: true`.
5. **Worker-Protokoll Version 3 (`/api/v1/worker/…`):**
   - `POST /worker/register` und `POST /worker/poll` akzeptieren `protocol: 3`, `available: bool`, `availability_reason: str`.
   - `POST /worker/poll` liefert zusätzlich bei Konfigurationsänderung `availability_config` (mit Hash/Version) sowie ggf. ausstehende `fs_request`- und `path_check`-Aufträge.

---

## 5. Watchfolder auf Workern, lokale Pfade & Rechnerwechsel

### 5.1 Strukturiertes Pfadmodell & Rechnerwechsel (Rechner 1 → Rechner 2)

In den Moduleinstellungen des Watchfolders (und künftigen Pfadfeldern) wird strukturiert gespeichert:

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
- **Erstellen an Rechner 1:** Wählt der Nutzer einen lokalen Pfad, schlägt Marathon automatisch den per Loopback-Bridge erkannten lokalen Worker (`Rechner 1`) vor bzw. übernimmt ihn aus dem Datei-Dialog (`Dieser Rechner`).
- **Wechseln zu Rechner 2 (1-Klick-Umstellung):**
  - Öffnet ein berechtigter Nutzer (`edit`) einen Workflow mit lokalem Pfad an **Rechner 2** (erkannt via Loopback-Bridge) und der Workflow steht noch auf `Rechner 1`:
    - Im Workflow-Kopf und direkt am lokalen Modul erscheint ein deutlicher Hinweis samt Aktion **„Auf diesen Rechner (Rechner 2) umstellen“**.
    - Beim Klick auf **„Starten“** eines gestoppten Workflows an Rechner 2 bietet ein kurzer Dialog direkt: *„Auf diesem Rechner (Rechner 2) starten“* vs. *„Auf Rechner 1 starten“*.
    - Läuft der Workflow bereits aktiv auf Rechner 1, wird er nicht ungefragt umgezogen (Hinweis: erst stoppen, dann umstellen).
  - **Live-Pfadprüfung nach Umstellung:** Über `POST /api/v1/workflows/{id}/bind-local-worker` (abgesichert durch `presence_token` für Rechner 2 oder Admin-Rolle) werden alle lokalen Module des Workflows auf `worker_id = <Rechner 2>` umgestellt; gleichzeitig prüft der Server per kurzlebigem Check über Rechner 2, ob die konfigurierten lokalen Pfade dort existieren. Fehlt ein Ordner auf Rechner 2, erscheint sofort eine klare Meldung am Modul mit direktem Button zum Datei-/Ordner-Dialog auf Rechner 2.

### 5.2 Watchfolder als Worker-Fähigkeit & Sticky Locality

- `watchfolder` erhält `execution = "worker"` im Modulkatalog; neue Worker erhalten `watchfolder` ab Werk an oberster Stelle ihrer `skills`-Liste (auch der `server-worker`).
- Der alte Hintergrund-Thread `marathon/server/watcher.py` entfällt; der Engine-Tick erzeugt fällige Watchfolder-Jobs:
  - `path_scope == "network"` → Zuteilung an den `server-worker` (bzw. passenden verfügbaren Worker nach Fähigkeiten-Rang).
  - `path_scope == "local"` → Zuteilung **ausschließlich** an `worker_id`.
- **Sticky Locality für Folgejobs:**
  - Jedes Asset aus einem lokalen Watchfolder wird mit `assets.locality_worker_id = watchfolder.worker_id` angelegt.
  - Im Scheduler (`marathon/server/engine/tick.py`): Hat ein Asset `locality_worker_id IS NOT NULL`, kommt **nur** dieser eine Worker für Folgejobs infrage.
  - Schreibt ein Modul (`copy_file`, `move_file`, künftig `transcode` etc.) die resultierende Datei auf einen **Netzwerkpfad** (`\\…`), setzt die Engine `assets.locality_worker_id = NULL`. Ab dem nächsten Modul greift wieder die normale Verteilung nach Fähigkeiten-Rang.

### 5.3 Kontextsensitive Modul-Hinweise & Statusspalten

- **Direkte Hinweise am Watchfolder-Modul (Editor + Überwachen):**
  1. Lokaler Pfad ohne laufenden Worker-Client auf dem aktuellen Rechner (und keine gültige `worker_id` gesetzt) → Meldung am Modul mit Link **„Worker-Client herunterladen“**.
  2. Konfigurierte `worker_id` ist ein anderer Rechner als der lokal erkannte → Hinweis + Aktion **„Auf diesen Rechner umstellen“** (für Nutzer mit `edit`-Recht).
  3. Konfigurierter `worker_id` ist offline, `stopped`, `draining`/`stopping`, nicht zugelassen (`approved = false`) oder `client_available = false` → Meldung am Modul mit genauem Grund und Link zu den Worker-Einstellungen (beim lokal erkannten Rechner direkt über `/worker-control` bzw. Hinweis auf Tray-Icon, für Admins `/workers/<worker_id>`).
  4. Ist beim fälligen Scan kein passender Worker verfügbar, wartet der Scan und schreibt *„Kein Worker für den Watchfolder verfügbar“* (bzw. den konkreten Grund) ins Modul-Log.
- **Spalten & Status (aus `Geplant` übernommen):**
  - Watchfolder: Spalten **neu** (links: Funde des laufenden Scans; gehen nach Scan-Abschluss in `erfasst` über) und **erfasst** (rechts); Spalte `wartet` entfällt; gefundene Assets gehen sofort weiter.
  - Warten-Modul: Assets stehen während der Wartezeit in Spalte **wartet**, danach `erledigt` über Ausgang `Erfolg`.

---

## 6. Datei- und Ordner-Dialog (`FileBrowserDialog`)

### 6.1 Funktionsumfang & Bedienung (React 19 / TypeScript, `lucide-react`)

Ausbau des bestehenden `ui/src/components/FolderDialog.tsx` zu einer wiederverwendbaren Komponente `FileBrowserDialog` (ohne externe File-Manager-Library, ohne Favoritenspalte links):

- **Props / Modi:**
  - `mode: "folder" | "file"`
  - `accept_extensions?: string[]` (optional für Datei-Modus)
  - `allow_local_worker?: boolean` (Standard `true`)
  - Rückgabe strukturiert: `{ scope: "network" | "local", worker_id: string | null, path: string }`.
- **Quellen-Leiste oben:**
  - Umschalter **`Server`** (Freigaben von `\\<server_address>` bzw. Server-Pfade) vs. **`Dieser Rechner`** (nutzt den per Loopback-Bridge erkannten lokalen Worker; ist lokal kein Client aktiv, zeigt der Bereich eine klare Meldung mit Link zum Client-Download). Admins erhalten optional zusätzlich ein Dropdown für beliebige Online-Worker.
- **Navigations- und Pfadzeile:**
  - Buttons: `Zurück` (`←`), `Vor` (`→`) mit clientseitigem Verlauf-Stack, `Nach oben` (`↑`), `Aktualisieren` (`↻`).
  - **Pfadleiste:**
    - Standardmäßig klickbare **Breadcrumb-Knoten** (z. B. `10.0.77.11 > Ablage KI Proxy_1 > Proxy 10 Mbit > DEFA`).
    - Klick auf freie Fläche in der Pfadleiste (oder `Strg+L` / `Alt+D`) wechselt in den **Editiermodus**.
    - Eingabe zeigt nach kurzem Debounce (150 ms) ein **Vorschlagmenü** passender Unterordner des Elternpfads (mit clientseitigem Cache je Elternpfad): `↓`/`↑` markiert, `Tab` übernimmt zum Weitertippen, `Enter` betritt den markierten Ordner, `Esc` schließt.
  - **Filterfeld rechts:** Filtert den geladenen Ordnerinhalt verzögerungsfrei clientseitig (case-insensitive Teilstring, Endung wie `.mp4`, Wildcards `*` und `?`).
- **Ansichten (Umschalter in der Toolbar, Auswahl in `localStorage` gespeichert):**
  - **Liste:** Mehrspaltig vertikal fließend (wie Windows „Liste“), Icon + Name, horizontales Scrollen bei großen Ordnern.
  - **Details:** Sortierbare Tabelle (Klick auf Spaltenkopf: auf-/absteigend) mit `Name`, `Änderungsdatum`, `Typ` (z. B. *Dateiordner*, *MP4-Datei*), `Größe` (formatiert in B/KB/MB/GB, bei Ordnern leer). Ordner stehen immer vor Dateien.
  - **Icons:** Über `lucide-react` nach Dateigruppe (Ordner, Video, Audio, Bild, Dokument/Text/CSV/JSON, Archiv, generische Datei).
  - **Ordner- vs. Datei-Modus:** Im Ordner-Modus werden Dateien sichtbar, aber leicht gedimmt und nicht als Ziel wählbar dargestellt; „Diesen Ordner wählen“ wählt den aktuellen (oder markierten) Ordner. Im Datei-Modus wählt Doppelklick oder „Öffnen“ die Datei.
- **Performance für große Ordner (5.000+ Einträge):**
  - Virtualisiertes Rendering im Frontend für `Details` und `Liste` (nur sichtbare Zeilen/Spalten im DOM).
  - Statuszeile unten zeigt Elementzahl (gesamt / gefiltert) und ggf. Kappungshinweis.

### 6.2 Backend-API (`GET /api/v1/fs/entries`)

Erweitert bzw. ersetzt `GET /api/v1/fs/dirs` (alter Pfad kann als kompatibler Alias bleiben):

- Query-Parameter: `source=server|worker`, `worker_id` (Pflicht bei `source=worker`), `path` (optional; leer = Laufwerke/Wurzeln bzw. `\\<server_address>`), `include_files=true|false` (Standard `true`).
- Antwort: `source`, `worker_id`, `path`, `parent`, `breadcrumbs`, `entries` (`name`, `path`, `is_dir`, `size`, `modified_at`, `ext`), `total_count`, `truncated`.
- `max_entries = 10_000`; Metadaten über `os.scandir` + `entry.stat(follow_symlinks=False)` (unter Windows ohne zusätzlichen SMB-Roundtrip).
- **Rechteprüfung:** `source=worker` nur erlaubt, wenn der Aufrufer ein gültiges `X-Marathon-Worker-Presence`-Ticket (bzw. `marathon_local_worker`-Cookie) für genau diese `worker_id` besitzt **oder** `current_user.role == 'admin'`.

### 6.3 Remote-Listing über den Worker-Client (Long-Poll-RPC)

- Ruft die UI `GET /api/v1/fs/entries?source=worker&worker_id=…&path=…` auf:
  1. Server prüft Berechtigung (Presence-Nachweis oder Admin) und Online-Status von `worker_id` (sonst `503` mit Hinweis).
  2. Server legt eine kurzlebige Anfrage `{ request_id, path, include_files }` (Timeout 10 s) an und weckt den offenen Long-Poll des Workers.
  3. Der Worker-Client führt dasselbe `scandir`-Listing lokal aus und sendet das Ergebnis per `POST /api/v1/worker/fs-result` zurück.
  4. Der wartende HTTP-Request der UI liefert das Ergebnis direkt an den Browser aus.
- **Vorteil:** Weiterhin ausschließlich ausgehende Verbindungen vom Worker zum Server; keine offenen LAN-Ports oder Firewall-Regeln auf Workstations nötig.

---

## 7. Abnahmekriterien

- Login per Magic-Link (inkl. SafeLinks-Schutz per Bestätigungs-POST) und 6-stelligem Code funktioniert; Session überlebt Browser-Neustart und verlängert sich bei Aktivität.
- Selbstregistrierung akzeptiert nur konfigurierte Domains; Break-Glass-Admin aus `marathon.toml` und CLI-Notfall-Link funktionieren auch ohne SMTP.
- Workflow-Freigaben (`none`, `view`, `edit`) und Modulsperren greifen in UI, API und SSE-Live-Kanal; alle drei Konsistenzregeln (Sperre bei Freigabe, automatischer Rückfall auf `view`, Save-Guard `403` bei laufender Bearbeitung) sind durch Tests abgedeckt.
- Nutzerkonten und Worker sind vollständig entkoppelt: Eine Person vor einem Worker kann diesen über das Tray-Icon im Browser steuern (Starten, Stoppen/Drain, Sofort stoppen, Positiv-Verfügbarkeit) – ganz ohne Marathon-Nutzerkonto und ohne Zugriff auf fremde Worker.
- Ein eingeloggter Nutzer kann einen Workflow mit lokalen Pfaden an Rechner 1 anlegen, zu Rechner 2 wechseln, per 1-Klick-Umschaltung dort aktivieren (inkl. Pfadprüfung) und laufen lassen; fremde Workstations lassen sich ohne lokalen Presence-Nachweis nicht browsen oder kapern.
- Lokale Watchfolder binden Folgejobs per Sticky Locality bis zum ersten Netzwerkpfad und zeigen fehlende/abweichende/inaktive Worker mit direktem Link am Modul an.
- `FileBrowserDialog` beherrscht `Server` und `Dieser Rechner`, `Liste` und `Details`, Breadcrumb + Autocomplete, Verlauf (`←`/`→`/`↑`/`↻`), Filterfeld und scrollt 5.000+ Einträge flüssig.
