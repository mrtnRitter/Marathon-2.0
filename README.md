# Marathon 2.0

Workflow-Engine als Webanwendung (interner Name „m2“, im Code `marathon`, Version 2.0.0).

**Aktueller Stand:** Die Oberfläche liest und speichert alle Inhalte in einer lokalen PostgreSQL-Datenbank. Workflows lassen sich anlegen, ändern, duplizieren, im Node-Editor bearbeiten, speichern und löschen. Workflows mit fehlerhaften Einstellungen lassen sich speichern, aber nicht starten (siehe [Workflows mit fehlerhaften Einstellungen](#workflows-mit-fehlerhaften-einstellungen)). Workflows lassen sich in der Workflow-Übersicht, auf dem Dashboard und auf ihrer Detailseite starten und stoppen und zeitgesteuert laufen lassen (alle X Minuten, Stunden, Tage, Wochen oder Monate); ein zeitgesteuerter Workflow bleibt aktiv und wartet zwischen den Läufen auf den nächsten Termin. Assets lassen sich erneut verarbeiten, auch in einem anderen Workflow. Reports lassen sich als CSV herunterladen. Über den Katzenkopf unten links lässt sich ein Bug-Report erfassen; er wird noch nicht versendet. Module liegen im Repo unter `marathon/modules/` und erscheinen beim Serverstart in der Modul-Palette: Watchfolder, Warten, Datei kopieren, Datei verschieben, Umbenennen und Prioliste (siehe [Module](#module)). Gestartete Workflows verarbeiten ihre Assets: Die Engine teilt Jobs an Worker zu; der Server bringt dafür einen eigenen Server-Worker mit (siehe [Engine und Worker](#engine-und-worker)). Alle Ansichten aktualisieren sich über einen Live-Kanal in Echtzeit; ist der Server nicht erreichbar, sperrt der Hinweis „Keine Verbindung zum Marathon-Server“ die Oberfläche, bis die Verbindung zurück ist. Worker lassen sich starten, stoppen (laufende Jobs laufen zu Ende) und sofort stoppen; ihre Fähigkeiten stehen in einer Rangliste. Windows-Rechner arbeiten über den Worker-Client als weitere Worker mit; er wird auf der Worker-Seite heruntergeladen, installiert sich selbst und wird ohne Marathon-Anmeldung über das Tray-Symbol eingestellt (siehe [Worker-Client](#worker-client)). Die Einstellungsseite speichert in die Datenbank (siehe [Einstellungen](#einstellungen)). Schaltflächen ohne echte Funktion zeigen den Hinweis „Noch nicht verfügbar“.

## Voraussetzungen

- **Python 3.11.4 oder neuer**
  - Windows: Installer von https://www.python.org, dabei „Add python.exe to PATH“ aktivieren.
  - Linux: Pakete `python3` und `python3-venv`.
- Windows oder Linux, jeweils 64 Bit (x64).
- Internetzugang **nur beim Einrichten**. Zur Laufzeit wird nichts nachgeladen.

Alles Weitere (Python-Pakete, Node.js, PostgreSQL, Caddy, UI-Bibliotheken, Schrift) installiert `build.py` lokal in den Projektordner. Es wird nichts global installiert.

## Einrichten

Im Projektordner ausführen:

```bash
python build.py      # Windows (alternativ: py build.py)
python3 build.py     # Linux
```

`build.py` erledigt:

1. Lokale Python-Umgebung `.venv` anlegen und `requirements/server.txt` installieren.
2. Node.js (feste Version, Prüfsumme wird kontrolliert) nach `.tools/node` laden.
3. PostgreSQL 18.6 (feste Version, Prüfsumme wird kontrolliert) nach `.tools/postgres` laden.
4. Caddy 2.11.9 (feste Version, Prüfsumme wird kontrolliert) nach `.tools/caddy` laden – der mitgelieferte HTTPS-Proxy.
5. Datenbank-Ordner `.data/postgres` anlegen. Das Passwort wird zufällig erzeugt und liegt in `.data/postgres.pass`.
6. UI-Bibliotheken aus `ui/package-lock.json` nach `ui/node_modules` installieren.
7. Die Oberfläche bauen; das Ergebnis liegt in `marathon/server/static`.
8. Nur unter Windows: den Worker-Client bauen; das Ergebnis liegt in `.data/client` (siehe [Worker-Client bauen und signieren](#worker-client-bauen-und-signieren)). Unter Linux wird der Schritt übersprungen.

Bereits erledigte Schritte werden übersprungen. `build.py` erneut ausführen nach jedem `git pull` und nach Änderungen am UI-Code.

### Mailversand einrichten

Admins richten den Versand von Anmelde-Mails unter **Einstellungen → Mailversand** ein. Die Felder heißen „SMTP-Server“, „Port“, „Benutzername“, „Verschlüsselung“, „Absenderadresse“, „Absendername“ und „Passwort“.

- „Verschlüsselung“ bietet „Keine“, „STARTTLS“ und „SSL/TLS“. Standard ist STARTTLS mit Port 587; SSL/TLS wird üblicherweise mit Port 465 verwendet.
- Ein leerer „SMTP-Server“ schaltet den Mailversand aus. Die SMTP-Einstellungen werden in der Datenbank gespeichert; das Passwort liegt ausschließlich in `.data/smtp.pass`, nicht im Repo. Ein leeres Passwortfeld lässt ein bereits gespeichertes Passwort unverändert.
- Mit eingetragenem Benutzernamen lässt sich nur speichern, wenn ein Passwort eingegeben oder bereits gespeichert ist. Ohne Passwort erscheint „Mailversand: Passwort fehlt für den Benutzernamen“.
- Änderungen gelten ohne Neustart. Das gesamte Senden hat ein Zeitlimit von 10 Sekunden („Zeitlimit von 10 s überschritten“).
- „Testmail senden“ schickt mit den gespeicherten Einstellungen eine Testmail an die Adresse unter „Testmail an“.

In `config/marathon.toml` gibt es keine Abschnitte `[auth]` oder `[smtp]`; ältere Abschnitte werden ignoriert. Erlaubte E-Mail-Domains starten leer und werden nur unter **Einstellungen → Anmeldung** gepflegt.

**Microsoft 365 mit Dienstpostfach (Anmeldung mit Passwort):**

- SMTP-Server: `smtp.office365.com`
- Port: `587`
- Benutzername: `marathon@looks.film`
- Verschlüsselung: `STARTTLS`
- Absenderadresse: `marathon@looks.film`
- Absendername: `Marathon`
- Passwort: Passwort des Dienstpostfachs

Voraussetzung: Für das Postfach ist „Authentifiziertes SMTP“ eingeschaltet. Microsoft schaltet die Anmeldung mit Passwort für SMTP schrittweise ab; dann die Variante ohne Anmeldung nutzen.

**Microsoft 365 ohne Anmeldung (Direct Send, nur an Adressen der eigenen Domains):**

- SMTP-Server: `looks-film.mail.protection.outlook.com`
- Port: `25`
- Benutzername: leer
- Verschlüsselung: `STARTTLS`
- Absenderadresse: `marathon@looks.film`
- Absendername: `Marathon`

Voraussetzung: Der Server erreicht Port 25 nach außen, Direct Send ist im Tenant nicht gesperrt, und die öffentliche IP-Adresse des Standorts steht im SPF-Eintrag der Domain (sonst landen die Mails im Junk-Ordner).

Testmail per Konsole: `python run.py mail-test <adresse>`. Der Befehl startet die Datenbank bei Bedarf und stoppt sie danach wieder, wenn er sie selbst gestartet hat.

Während `python run.py` läuft, auf dem Server-Rechner `admin-code` und Enter eingeben oder `python run.py admin-code` ausführen (ohne Adresse; der Einzelbefehl startet die Datenbank bei Bedarf und stoppt sie danach wieder). Ausgegeben wird `Admin-Code: <code> (gültig 15 Minuten) – auf der Anmeldeseite unter „Mit Admin-Code anmelden“ eingeben`.

Auf der Anmeldeseite „Mit Admin-Code anmelden“ wählen, den Code in „Admin-Code“ eingeben und „Anmelden“ wählen. Danach unter **Einstellungen → Mailversand** den Mailversand einrichten und unter **Einstellungen → Anmeldung** die erlaubten Domains eintragen. Anschließend das eigene Konto per E-Mail registrieren und auf der Seite „Nutzer“ zum Admin machen. Der Notfallzugang funktioniert auch ohne eingerichteten Mailversand.

## Starten

```bash
python run.py
```

`run.py` startet die Datenbank, bringt sie auf den neuesten Stand und startet den Server mit dem mitgelieferten HTTPS-Proxy. Beenden mit `stop` und Enter (Strg+C geht als Notfall-Abbruch); danach werden auch Proxy und Datenbank gestoppt. Neustart mit `restart` und Enter: Server beendet sich sauber und startet neu, die Datenbank läuft weiter.

Oberfläche im Browser öffnen: die öffentliche Adresse, z. B. https://laptop-martin-r (siehe [HTTPS und Zertifikate](#https-und-zertifikate)). Von anderen Rechnern nur über HTTPS erreichbar (Ausnahme auf dem Server-Rechner: Rettungszugang, siehe unten); Port 80 ist zu, daher immer mit `https://` aufrufen.

Beim ersten Start einmal das Stammzertifikat importieren (Server läuft dabei weiter, in einem zweiten Fenster):

```bash
python run.py --trust
```

Danach den Browser neu starten.

**Rettungszugang:** Auf dem Server-Rechner selbst bleibt http://127.0.0.1:8080 direkt erreichbar, z. B. wenn eine falsche öffentliche Adresse eingetragen wurde.

### Anmeldung

Die Anmeldeseite ist immer verfügbar. „Code senden“ verschickt einen sechsstelligen Code an eine berechtigte Adresse: ein bestehendes Konto oder eine Adresse aus einer erlaubten Domain. Der Code ist 15 Minuten gültig. Neue Konten erhalten immer die Rolle „Nutzer“; eine Anmeldung gilt bis zu 180 Tage und verlängert sich bei Aktivität.

Ist der Mailversand nicht eingerichtet, meldet erst die Auswahl von „Code senden“: „Anmeldung per E-Mail ist nicht eingerichtet. Bitte wende Dich an Deinen Administrator.“ Für den Notfallzugang den Link „Mit Admin-Code anmelden“ verwenden; die erste Einrichtung ist unter [Mailversand einrichten](#mailversand-einrichten) beschrieben.

### Nutzer und Rollen

Admins verwalten Marathon vollständig. Normale Nutzer sehen weder „Einstellungen“ noch die Seite „Nutzer“ und sehen nur eigene oder freigegebene Workflows. Sie können Worker ansehen, aber nicht steuern oder bearbeiten.

Auf der Seite „Nutzer“ kann ein Admin die Rolle „Nutzer“ oder „Admin“ ändern. Die Zahl unter „Sitzungen“ öffnet die angemeldeten Geräte; „Alle abmelden“ widerruft die Sitzungen. Bei einer gesperrten Adresse lässt sich „Sperre aufheben“ wählen. Das gilt auch für den Notfallzugang. Dieser ist nicht herabstufbar oder löschbar, trägt den Hinweis „Notfallzugang“ und erscheint nicht im Teilen-Dialog. Geschützt bleibt sonst nur der letzte Admin: „Der letzte Admin kann nicht herabgestuft oder gelöscht werden“. Workflows eines gelöschten Nutzers bleiben ohne Eigentümer und sind nur für Admins sichtbar.

### Workflows teilen

Eigentümer und Admins öffnen im Workflow den Dialog „Teilen“. Für jeden Nutzer gibt es diese Stufen:

- „Privat (kein Zugriff)“: kein Zugriff auf den Workflow.
- „Teilen zur Ansicht“: Workflow, Module, Assets, Logs und Status ansehen; nicht bearbeiten oder speichern, nicht starten oder stoppen, keinen Zeitplan ändern und nicht erneut verarbeiten.
- „Teilen zur Ausführung“: alles aus der Ansicht, zusätzlich starten, stoppen und erneut verarbeiten; nicht bearbeiten oder speichern und keinen Zeitplan ändern.
- „Teilen zur Bearbeitung und Ausführung“: zusätzlich bearbeiten, speichern und den Zeitplan ändern.

Nur Admins können im Teilen-Dialog den „Eigentümer“ setzen. Nach dem Löschen eines Eigentümers sind dessen Workflows ohne Eigentümer und nur für Admins sichtbar.

### Entwicklungsmodus (Live-Vorschau)

Änderungen unter `ui/src` erscheinen sofort im Browser, ohne neu zu bauen. Datenbank und Server laufen mit.

```bash
python run.py --dev
```

Oberfläche öffnen: http://127.0.0.1:5173

### Konsolenbefehle

| Befehl | Wirkung |
|---|---|
| `python build.py` | Alles installieren, Datenbank-Ordner anlegen, die Oberfläche und (unter Windows) den Worker-Client bauen |
| `python build.py --client` | Nur den Worker-Client bauen (nur Windows); am besten bei laufendem Server, damit die aktuelle öffentliche Adresse eingebettet wird |
| `python run.py` | Datenbank und Server starten |
| `python run.py --dev` | Datenbank, Server und Live-Vorschau starten |
| `python run.py --trust` | Stammzertifikat des mitgelieferten Proxys für den angemeldeten Benutzer importieren (Windows); unter Linux Pfad und Vorgehen anzeigen |
| `restart` + Enter (während `python run.py` läuft) | Server sauber neu starten; die Datenbank läuft weiter |
| `admin-code` + Enter (während `python run.py` läuft) | Admin-Code für den festen Notfallzugang ausgeben; keine Adresse angeben |
| `python run.py admin-code` | Admin-Code ausgeben; startet die Datenbank bei Bedarf und stoppt sie danach wieder, wenn dieser Befehl sie gestartet hat |
| `python run.py mail-test <adresse>` | Testmail senden; startet die Datenbank bei Bedarf und stoppt sie danach wieder, wenn dieser Befehl sie gestartet hat |
| `python build.py --help` / `python run.py --help` | Hilfe anzeigen |

### Datenbank zurücksetzen

Löscht alle Daten: Server beenden, Ordner `.data` löschen, `python build.py` ausführen, dann `python run.py`.

## Konfiguration

Datei `config/marathon.toml`:

| Eintrag | Bedeutung | Standard |
|---|---|---|
| `host` | Adresse, an der der Server erreichbar ist. `0.0.0.0` macht ihn im lokalen Netz erreichbar. | `127.0.0.1` |
| `port` | Port des Servers | `8080` |
| `[database] host` | Adresse, an der die Datenbank lauscht | `127.0.0.1` |
| `[database] port` | Port der Datenbank | `5433` |
| `[database] name` | Name der Datenbank | `marathon` |
| `[database] user` | Datenbank-Benutzer; wird beim ersten `build.py` angelegt | `marathon` |
| `[engine] tick_interval_s` | Takt der Engine in Sekunden (0.05–10) | `0.5` |
| `[engine] poll_timeout_s` | So lange hält der Server die Anfrage eines Workers offen (1–60 s) | `25` |
| `[engine] worker_timeout_s` | Ohne Meldung gilt ein Worker danach als ausgefallen (10–600 s) | `30` |
| `[engine] pickup_timeout_s` | Abhol-Frist für zugeteilte Jobs (1–600 s) | `10` |
| `[engine] max_attempts` | Unterbrochene Versuche je Asset und Modul (1–10) | `3` |
| `[server_worker] enabled` | Server-Worker mit dem Server starten | `true` |
| `[server_worker] skills` | Fähigkeiten des Server-Workers ab Werk, oben = höchste Prio (Modultyp-IDs, ohne Doppelte) | `["copy_file", "move_file", "rename_file"]` |
| `[folder_dialog] server_address` | Server-Adresse für den Ordner-Dialog ab Werk (Rechnername oder IP, leer = keine) | `""` |
| `[proxy] public_url` | Adresse, unter der Marathon erreichbar ist (nur `https://`, ohne Pfad); bestimmt Zertifikat und Port des mitgelieferten Proxys | `"https://localhost"` |
| `[proxy] builtin` | Mitgelieferten Proxy (Caddy) starten; `false` = externer Reverse-Proxy | `true` |
| `[proxy] trusted_proxies` | IP-Adressen externer Reverse-Proxys; bei `builtin = true` gilt `127.0.0.1` automatisch | `[]` |
| `[worker_client] require_admin_approval` | `true` = einen neu angemeldeten Worker-Client muss ein Admin einmal mit „Zulassen“ freigeben; `false` = sofort zugelassen | `true` |
| `[worker_client] control_session_hours` | So lange gilt die Worker-Einstellungsseite nach „Worker-Einstellungen öffnen“ (1–168 Stunden) | `12` |
| `[client_build] sign_command` | Befehl zum Signieren der Worker-Client-Exe als Liste, `{file}` = Pfad der Exe; leer = nicht signieren | `[]` |

`[server_worker] skills`, `[folder_dialog] server_address` und die `[proxy]`-Einträge sind Standardwerte: Sie gehen beim Start mit `python run.py` in die Datenbank, solange dort noch kein Wert steht. Danach gilt die Datenbank, geändert wird in der Oberfläche (Worker-Seite bzw. Einstellungen). `host` und `port` gelten immer aus der Datei, ebenso `[worker_client]` und `[client_build]`.

`name` und `user` erlauben nur Kleinbuchstaben, Ziffern und `_`. Den Benutzer nach dem Einrichten nicht mehr ändern, sonst die Datenbank zurücksetzen.

## HTTPS und Zertifikate

Marathon selbst spricht nur HTTP auf `host`/`port` und liegt immer hinter einem Proxy, der HTTPS übernimmt.

- **Mitgelieferter Proxy (Standard):** Der Server startet Caddy aus `.tools/caddy` und beendet ihn mit dem Server. Caddy lauscht nur auf dem Port der öffentlichen Adresse (ab Werk 443) und stellt das Zertifikat für deren Rechnernamen aus seiner eigenen Zertifizierungsstelle aus; die Zertifikate erneuert Caddy selbst. Andere Namen oder IP-Adressen erhalten keine Verbindung. Daten in `.cert/`, Ausgaben in `.data/caddy.log`.
- **Stammzertifikat:** `.cert/caddy/pki/authorities/local/root.crt` (entsteht beim ersten Start, gilt 10 Jahre). Jeder Rechner, der Marathon im Browser öffnet oder als Worker arbeitet, muss es einmal importieren:
  - Server-Rechner: `python run.py --trust`.
  - Rechner mit Worker-Client: übernimmt die Installation des Clients (siehe [Worker-Client](#worker-client)).
  - Andere Windows-Rechner: Datei kopieren, Doppelklick → „Zertifikat installieren“ → „Aktueller Benutzer“ → „Alle Zertifikate in folgendem Speicher speichern“ → „Vertrauenswürdige Stammzertifizierungsstellen“. Danach den Browser neu starten.
  - Linux: nach `/usr/local/share/ca-certificates/marathon.crt` kopieren und `update-ca-certificates` ausführen.
- **Externer Reverse-Proxy:** In den Einstellungen „Proxy verwenden“ einschalten, die IP-Adresse des Proxys unter „Proxy“ eintragen und ggf. die öffentliche Adresse anpassen; dann den Server neu starten. Läuft der Proxy auf einem anderen Rechner, in `config/marathon.toml` `host = "0.0.0.0"` setzen. Der Proxy muss `X-Forwarded-Proto` setzen, Anfragen länger als `poll_timeout_s` offen halten und den Live-Kanal ungepuffert durchreichen.
- **Umleitung:** Anfragen, die Marathon über HTTP oder einen anderen Rechnernamen erreichen, werden auf die öffentliche Adresse umgeleitet.
- **Worker** verbinden sich nur über `https://` und prüfen das Zertifikat gegen den Zertifikatsspeicher des Systems; mit `--ca-file <datei>` stattdessen nur gegen diese Datei. Die Prüfung lässt sich nicht abschalten. Der Worker-Client prüft gegen das beim Bauen eingebettete Stammzertifikat (ohne eingebettetes Zertifikat gegen den Zertifikatsspeicher des Systems). Der Server-Worker nutzt die öffentliche Adresse; mit dem mitgelieferten Proxy prüft er gegen dessen Stammzertifikat, sonst gegen den Zertifikatsspeicher des Systems.

## Engine und Worker

- **Server-Worker:** startet mit `python run.py` als eigener Prozess und meldet sich wie jeder Worker beim Server an (Worker-ID `server-worker`, Name „Server-Worker“; der Name gilt bei der ersten Anmeldung, danach auf der Worker-Seite änderbar). Er ist ab Werk gestartet und wird wie jeder Worker auf der Worker-Seite eingestellt. Stürzt er ab, startet der Server ihn neu. Ausgaben: `.data/server-worker.log`.
- **Zugang:** Jeder Worker-Client erhält bei seiner ersten Anmeldung ein eigenes Worker-Token; er ist an keinen Marathon-Nutzer gebunden. Der Server-Worker meldet sich mit dem Token aus `.data/worker.token` an; der Server legt die Datei beim ersten Bedarf an. Worker sprechen Worker-Protokoll 3; ältere Worker werden mit „Protokollversion nicht unterstützt“ abgelehnt.
- **Zuteilung:** Ein Worker macht höchstens einen Job gleichzeitig. Ein Job geht an den gestarteten, zugelassenen, erreichbaren und verfügbaren Worker ohne Job, bei dem der Modultyp in seiner Fähigkeiten-Liste am weitesten oben steht; bei gleichem Rang entscheidet der Name. Beispiel: Worker A [Datei kopieren, Datei verschieben], Worker B [Datei verschieben, Datei kopieren] → Kopieren vorrangig an A, Verschieben an B; ist A belegt, kopiert B. Laufende Workflows kommen reihum dran, im Workflow das oberste Asset zuerst. Ein Job bündelt bis zu so viele Assets desselben Moduls, wie dessen „Parallele Verarbeitung“ erlaubt; der Worker bearbeitet sie gleichzeitig und meldet das Ergebnis je Asset. Worker holen ihre Jobs über eine offen gehaltene Anfrage ab.
- **Worker-Seite:** „Starten“, „Stoppen“ und „Sofort-Aus“ in der Kopfleiste (für die Auswahl) oder direkt in der Zeile; Admins zusätzlich „Zulassen“, „Entfernen“ und „Bearbeiten“. Status „bereit“, „Wird gestoppt“, „Wird sofort gestoppt“ oder „gestoppt“; daneben „Online“ / „Offline“, die Spalte „Verfügbar“ zeigt die Verfügbarkeit samt Grund, die Spalte „Jobs“ die laufenden Jobs. Ein neuer Worker meldet sich „gestoppt“ an und bekommt erst nach „Starten“ Jobs. Bearbeiten: Name, Fähigkeiten als sortierbare Liste – weiter oben = höhere Priorität, nicht in der Liste = übernimmt diesen Modultyp nicht; wählbar sind nur Modultypen, die der Rechner meldet – und Verfügbarkeit (siehe [Verfügbarkeitsregeln](#verfügbarkeitsregeln)). Neue Worker-Clients starten mit leerer Fähigkeiten-Liste und bekommen erst Jobs, wenn ein Admin Fähigkeiten einträgt. Normale Nutzer sehen die Worker nur lesend; „Client herunterladen“ steht allen angemeldeten Nutzern zur Verfügung.
- **Stoppen:** Der Worker bekommt keine neuen Jobs, laufende Jobs laufen sauber zu Ende; zugeteilte, noch nicht gestartete Jobs gehen sofort zurück. Status „Wird gestoppt“, ohne laufende Jobs „gestoppt“. „Starten“ während „Wird gestoppt“ setzt den Worker sofort wieder auf „bereit“, laufende Jobs bleiben.
- **Sofort-Aus:** bricht alle laufenden Jobs des Workers ab; unfertige Ergebnisse werden verworfen, das Asset geht zurück an den Ausgang des letzten fertigen Moduls und wird neu verteilt. Der Abbruch zählt nicht als Versuch. Bis alle Jobs abgebrochen sind, zeigt der Worker „Wird sofort gestoppt“ und lässt sich nicht starten.
- **Zulassen und Entfernen:** Mit `[worker_client] require_admin_approval = true` (Standard) bekommt ein neuer Worker-Client erst nach „Zulassen“ Jobs. „Entfernen“ (mit Bestätigung im Seitenpanel) geht nur ohne aktive Jobs; der Server-Worker lässt sich nicht entfernen. Ein entfernter Client verwirft seine Anmeldung, zeigt das im Tray und meldet sich beim nächsten Start als neuer Worker an.
- **Workflow stoppen:** bricht laufende Jobs ab; das Asset geht zurück an den Ausgang des vorherigen Moduls. Bis alle Jobs abgebrochen sind, zeigt der Workflow „Wird gestoppt“ und lässt sich nicht starten. Status: „aktiv“ oder „gestoppt“.
- **Zeitgesteuert:** Ein gestarteter zeitgesteuerter Workflow bleibt „aktiv“. Er wartet auf den nächsten Termin, läuft dann, bis kein Asset mehr aussteht oder in Arbeit ist, und wartet wieder. In der Wartezeit entstehen keine Jobs und der Watchfolder scannt nicht. Ein Lauf endet erst, wenn jeder Watchfolder seit Laufbeginn gescannt hat und keine Datei mehr auf Stabilität wartet.
- **Ausfälle:** Meldet sich ein Worker 30 s nicht, gilt er als offline; startet er neu oder fällt er aus, wird sein Job erneut vergeben. Nach 3 unterbrochenen Versuchen am selben Modul ist das Asset fehlgeschlagen.
- **Workflow ändern:** jederzeit speichern. Betrifft die Änderung laufende Assets (z. B. gelöschtes Modul), zeigt die Workflow-Ansicht vorher eine Warnung und speichert erst nach Bestätigung. Die Warnung nennt, wie viele Assets ab dem vorherigen Modul neu verteilt, als erledigt abgeschlossen oder als fehlgeschlagen markiert werden und wie viele Jobs abgebrochen werden.
- **Überwachen:** Die Zähler in den Modulen aktualisieren sich bei Datenänderungen live. Ein Klick auf ein Modul zeigt Jobs, Assets, Worker und Logs. Im Reiter „Einstellungen“ sind die Moduleinstellungen nur lesend. Der Reiter „Logs“ zeigt die neuesten 500 Warnungen und Fehler mit Zeit, Stufe, Asset, Worker und Text; ohne Einträge steht dort „Keine Vorkommnisse“. Ein Doppelklick wechselt zu „Bearbeiten“, das Modul ist markiert und seine Einstellungen sind offen.
- **Verbinden (Bearbeiten):** Verbindungen von einem Ausgang („Erfolg“, „Fehler“ …) ziehen und über dem Ziel-Modul loslassen; die Linie rastet am Eingang ein. Anfang und Ende der Linie liegen am Rand der Kreise. Ziehen am verbundenen Eingang löst die zuletzt angelegte Verbindung: auf einem anderen Eingang loslassen verbindet neu, sonst wird sie entfernt.
- **Erneut verarbeiten:** Das Asset beginnt am Anfang des Workflows, hinter Watchfolder bzw. am Modul ohne eingehende Verbindung. Es gibt keine Obergrenze für die Zahl der gewählten Assets.

## Einstellungen

Die Seite „Einstellungen“ speichert in die Datenbank („Speichern“).

| Einstellung | Bedeutung | Standard |
|---|---|---|
| Ordner-Dialog: Server-Adresse | Rechnername oder IP-Adresse, z. B. `10.0.77.11`. „Server“ im Fenster „Ordner wählen“ zeigt die Freigaben von `\\<Adresse>`. | aus `[folder_dialog] server_address` |
| Proxy & HTTPS: Öffentliche Adresse | Adresse, unter der Marathon erreichbar ist (nur `https://`, ohne Pfad) | aus `[proxy] public_url` |
| Proxy & HTTPS: Proxy verwenden | An: Ein externer Reverse-Proxy übernimmt HTTPS; aus: mitgelieferter Proxy | aus `[proxy] builtin` (umgekehrt) |
| Proxy & HTTPS: Proxy | IP-Adressen externer Proxys, durch Komma getrennt | aus `[proxy] trusted_proxies` |
| Anmeldung: Erlaubte E-Mail-Domains | Domains für die Selbstregistrierung, exakt (Subdomains einzeln eintragen), durch Komma getrennt | leer; nur unter Einstellungen → Anmeldung gepflegt |
| Mailversand: SMTP-Server | SMTP-Rechnername oder IP-Adresse; leer = Mailversand aus | leer |
| Mailversand: Port | SMTP-Port (1–65535) | 587 |
| Mailversand: Benutzername | Anmeldename; leer = ohne Anmeldung | leer |
| Mailversand: Verschlüsselung | Keine, STARTTLS oder SSL/TLS | STARTTLS |
| Mailversand: Absenderadresse | Adresse, die als Absender verwendet wird | leer |
| Mailversand: Absendername | Anzeigename des Absenders | Marathon |
| Mailversand: Passwort | SMTP-Passwort; nur lokal gespeichert, nicht im Repo | `.data/smtp.pass` |
| Mailversand: Testmail an | Empfängeradresse für „Testmail senden“; verwendet die gespeicherten Einstellungen | kein Standard |

Änderungen unter „Proxy & HTTPS“ gelten nach einem Neustart des Servers. Unter „Einstellungen“ → „Server“ startet die Schaltfläche „Server neu starten“ den Server neu.

## Worker-Client

Der Worker-Client macht einen Windows-Rechner zum Worker. Er ist eine einzelne Exe, läuft im Benutzerkontext mit Tray-Symbol und baut nur ausgehende Verbindungen zum Server auf. Er braucht weder einen Marathon-Nutzer noch Adminrechte.

### Worker-Client bauen und signieren

- `python build.py --client` (nur unter Windows) baut `.data/client/MarathonWorker.exe`. Server-Adresse und Stammzertifikat des mitgelieferten Proxys werden in die Exe eingebettet. Läuft der Server, gilt die öffentliche Adresse aus der Datenbank, sonst der Startwert `[proxy] public_url` aus `config/marathon.toml`.
- Nach einer Änderung der öffentlichen Adresse den Client neu bauen: Ein Client für eine andere Adresse wird nicht zum Download angeboten.
- Signieren: In `config/marathon.toml` unter `[client_build] sign_command` den Signierbefehl als Liste eintragen, `{file}` steht für die Exe. Beispiel mit Azure Artifact Signing:

  ```toml
  [client_build]
  sign_command = ["signtool", "sign", "/v", "/fd", "SHA256", "/tr", "http://timestamp.acs.microsoft.com", "/td", "SHA256", "/dlib", "C:\\Tools\\Azure.CodeSigning.Dlib.dll", "/dmdf", "C:\\Tools\\metadata.json", "{file}"]
  ```

  Ist die Liste leer, bleibt die Exe unsigniert und Windows zeigt beim Start eine Warnung.
- Die Client-Pakete stehen in `requirements/client.txt` und `requirements/client-build.txt`; `build.py` installiert sie in `.build/client-venv`.

### Herunterladen und installieren

1. Auf der Worker-Seite „Client herunterladen“ wählen; die Datei heißt `MarathonWorker-Setup.exe`. Der Download unter `/api/v1/client/download` braucht keine Anmeldung.
2. Die Datei auf dem Worker-Rechner starten und die Frage „Marathon-Worker auf diesem Rechner installieren?“ bestätigen.
3. Der Client installiert sich nach `%LOCALAPPDATA%\MarathonWorker`, trägt sich in den Windows-Autostart ein, erscheint unter „Apps“ als „Marathon-Worker“, importiert das Stammzertifikat des mitgelieferten Proxys in den Zertifikatsspeicher des Benutzers und startet.
4. Beim ersten Start meldet er sich beim Server an und erscheint auf der Worker-Seite als „gestoppt“. Ein Admin wählt „Zulassen“, trägt Fähigkeiten ein und wählt „Starten“.

Der Client läuft je Benutzer nur einmal. Ein bereits laufender Client muss vor einer erneuten Installation oder Deinstallation über „Beenden“ im Tray-Menü geschlossen werden.

**Deinstallieren:** unter Windows „Apps“ → „Marathon-Worker“ → „Deinstallieren“. Das entfernt Programm, Autostart-Eintrag und lokale Anmeldung; das Stammzertifikat bleibt im Zertifikatsspeicher. Auf der Worker-Seite entfernt ein Admin den Worker anschließend mit „Entfernen“.

### Tray-Symbol

- Die Farbe zeigt den Zustand: grün = bereit und verfügbar, orange = nicht verfügbar oder wird gestoppt, grau = gestoppt oder wartet auf Freigabe, rot = keine Verbindung zum Server.
- Doppelklick oder „Worker-Einstellungen öffnen“ öffnet die Einstellungsseite dieses Workers im Browser.
- „Status: …“ zeigt Zustand und Grund, z. B. „Bereit · 1 Job läuft“ oder „Wartet auf Freigabe“.
- „Beenden“ schließt den Client; laufende Jobs werden abgebrochen und neu verteilt.
- Ausgaben: `%LOCALAPPDATA%\MarathonWorker\logs\client.log`.

### Steuerung am Rechner

„Worker-Einstellungen öffnen“ holt vom Server einen Einmal-Link (60 s gültig) und öffnet die Seite „Worker-Einstellungen“ für genau diesen Worker, ohne Marathon-Anmeldung. Die Seite gilt danach `[worker_client] control_session_hours` lang (Standard 12 Stunden) in diesem Browser.

- „Starten“, „Stoppen“ und „Sofort-Aus“ wie auf der Worker-Seite.
- Verfügbarkeit einstellen und „Speichern“.
- Fähigkeiten nur lesen; sie legt ein Admin fest.
- Solange der Worker nicht zugelassen ist, steht dort „Dieser Worker wartet auf Freigabe durch einen Administrator“.

### Verfügbarkeitsregeln

Die Regeln stehen auf dem Server und werden auf der Worker-Seite (Admins) oder auf der Seite „Worker-Einstellungen“ gepflegt. Der Client prüft sie alle 5 s und meldet das Ergebnis mit der nächsten Anfrage an den Server (bis ca. 25 s später). Ohne Regeln ist der Worker immer verfügbar (Standard). Der Server-Worker ist immer verfügbar.

| Regel | Bedeutung |
|---|---|
| Zeitplan „Nur zu festen Zeiten verfügbar“ | Zeitfenster je Wochentag nach der Uhrzeit des Rechners, `HH:MM`, Ende auch `24:00`; ein Fenster über Mitternacht (z. B. `19:00`–`07:00`) läuft in den Folgetag. Fenster eines Tages dürfen sich nicht überschneiden, auch nicht über Mitternacht; höchstens 10 Fenster je Tag. |
| Programme „Nicht verfügbar, solange eines dieser Programme läuft“ | Ein Programmname je Zeile, ohne Pfad, z. B. `Resolve.exe`; Groß-/Kleinschreibung egal, `.exe` optional; höchstens 50. |
| Leerlauf „Erst bei Leerlauf starten“ | Minuten ohne Tastatur und Maus sowie Höchstwerte für CPU, RAM und GPU (Mittelwert über diese Minuten). Ohne GPU-Sensor gilt die GPU-Bedingung als erfüllt. |

Reihenfolge: zuerst der Zeitplan, dann die Programme, dann der Leerlauf. Der Leerlauf wird nur geprüft, wenn der Zeitplan es erlaubt und kein gesperrtes Programm läuft. Ist der Rechner die eingestellten Minuten ohne Eingabe und unter den Höchstwerten, wird der Worker verfügbar; danach macht ihn nur eine Tastatur- oder Mauseingabe wieder unverfügbar (die Last der eigenen Jobs zählt nicht), dann beginnt die Leerlauf-Beobachtung neu.

Wird ein Worker unverfügbar, laufen seine Jobs sauber zu Ende; zugeteilte, noch nicht gestartete Jobs gehen zurück und zählen nicht als Versuch. Neue Jobs bekommt er erst wieder, wenn er verfügbar ist.

### Zusatzprogramme

Fachprogramme wie ffmpeg bezieht der Client vom Server, damit die Exe schlank bleibt.

- Auf dem Server je Programm einen Ordner `.data/client-tools/<name>/` anlegen und die Dateien hineinlegen, z. B. `.data/client-tools/ffmpeg/bin/ffmpeg.exe`. Erlaubt sind Namen aus Kleinbuchstaben, Ziffern, `-` und `_` (bis 64 Zeichen); Dateien mit der Endung `.part` und Verknüpfungen werden ignoriert. Die Version ergibt sich aus dem Inhalt; eine geänderte Datei ist eine neue Version.
- Der Client gleicht beim Start und alle 10 Minuten ab, lädt neue oder geänderte Programme nach `%LOCALAPPDATA%\MarathonWorker\tools\<name>\<version>\` und prüft jede Datei per SHA-256.
- Module finden ein Programm über `job.tool("<name>")`. Für den Server-Worker gibt `python -m marathon.worker --tools-dir <ordner>` optional den Ablageort vor.

### Updates

Der Client kann sich selbst aktualisieren: Er lädt eine vom Server angebotene Version, prüft die Prüfsumme, lässt laufende Jobs auslaufen, startet neu und fällt auf die vorherige Version zurück, wenn die neue nicht sauber startet. Der Server bietet noch keine Versionen an („Version ausrollen“ ist noch nicht verfügbar); bis dahin eine neue Version herunterladen und erneut installieren.

## Workflows mit fehlerhaften Einstellungen

- Ein Workflow mit fehlenden oder ungültigen Moduleinstellungen lässt sich speichern. In Workflow-Übersicht, Dashboard und Workflow-Ansicht steht dann ein gelbes Ausrufezeichen; beim Überfahren zeigt es die betroffenen Module.
- Starten ist gesperrt; ein Startversuch zeigt den Hinweis, die Einstellungen zu prüfen.
- Wird ein laufender Workflow mit Fehlern gespeichert, stoppt er sich; die betroffenen Module erhalten im Log „Workflow gestoppt: Einstellungen prüfen“.
- Ein zeitgesteuerter Lauf wird übersprungen, der Workflow wartet weiter; die betroffenen Module erhalten im Log „Zeitgesteuerter Lauf übersprungen: Einstellungen prüfen“.
- Fehler im Aufbau (z. B. Verbindung zu einem Modul ohne Eingang) verhindern weiterhin das Speichern.

## Module

### Watchfolder (Kategorie „Quelle“)

Überwacht einen Ordner und legt für jede neue Datei ein Asset im Workflow an (Status „Ausstehend“, aktuelles Modul = Watchfolder), sobald sich Größe und Zeitstempel eine Wartezeit lang nicht geändert haben. Gescannt wird nur, solange der Workflow gestartet ist. Kein Eingang, ein Ausgang „Erfolg“.

| Einstellung | Bedeutung | Standard |
|---|---|---|
| Ordner | Absoluter Pfad, z. B. `D:\Ingest`, verbundenes Laufwerk `Z:\Ingest` oder Netzpfad `\\server\freigabe\ordner`. Direkt eingeben oder über den Ordner-Button wählen. | – |
| Include-Endungen | Nur diese Endungen übernehmen, z. B. `.mxf, .mov`. Leer = alle Dateien. | leer |
| Exclude-Endungen | Diese Endungen nie übernehmen. Eine Endung darf nicht in beiden Listen stehen. | leer |
| Wartezeit | So lange müssen Größe und Zeitstempel unverändert sein; zugleich Abstand der Ordnerprüfung (5–86400 s). | 60 s |
| Unterordner einbeziehen | Auch Dateien in Unterordnern übernehmen. | aus |

- Pfade gelten aus Sicht des Servers. Das Konto, unter dem der Server läuft, braucht Lesezugriff auf den Ordner bzw. die Netzfreigabe.
- Das Fenster „Ordner wählen“ zeigt nur Ordnernamen des Servers, keine Dateien. Die Eingabe `\\rechner` (z. B. `\\10.0.77.11`) zeigt die Freigaben dieses Rechners, von dort lässt sich weiter durchblättern (nur auf einem Windows-Server). Den Rechner selbst kann man nicht als Ordner wählen. Der Umschalter „Laufwerke“ | „Server“ wechselt zwischen den Laufwerken und den Freigaben der eingestellten Server-Adresse (siehe [Einstellungen](#einstellungen)); ohne Adresse erscheint „Keine Server-Adresse eingestellt (Einstellungen)“.
- Jede Datei wird je Pfad einmal übernommen. Eine unter gleichem Namen ersetzte Datei wird derzeit nicht erneut übernommen. Wird ein Asset gelöscht und liegt die Datei noch im Ordner, wird sie erneut übernommen.
- Das Modul zeigt in „Überwachen“, wie viele Dateien es erfasst hat (Assets, die noch existieren) und wie viele warten.
- Im Fenster „Ordner wählen“ filtert das Weitertippen im Adressfeld die Liste auf passende Ordner.
- Ist der Ordner nicht erreichbar (oder antwortet er 60 s nicht), steht das einmal im Modul-Log; danach folgt „Ordner wieder erreichbar“.
- In zeitgesteuerten Workflows scannt der Watchfolder nur während eines Laufs; Dateien, die in der Wartezeit ankommen, werden beim nächsten Lauf erfasst.

Warten, Datei kopieren, Datei verschieben und Umbenennen lassen sich im Workflow platzieren und einstellen. Datei kopieren, Datei verschieben und Umbenennen führt ein Worker aus (zunächst der Server-Worker); Pfade gelten aus Sicht des Workers. Warten erledigt die Engine selbst.

### Warten (Kategorie „Steuerung“)

Hält jedes Asset eine einstellbare Zeit an und gibt es dann über „Erfolg“ weiter. Für kurze Zeitspannen gedacht. Die Wartezeit gilt je Asset und beginnt, sobald das vorherige Modul es übergibt; das Modul zeigt „läuft“, „erledigt“ und „Fehler“ und führt jede Wartezeit als Job (ohne Worker).

| Einstellung | Bedeutung | Standard |
|---|---|---|
| Dauer | Wartezeit in Sekunden (1–86400), z. B. `3600` für eine Stunde | 60 s |

### Datei kopieren (Kategorie „Dateien“)

Kopiert die Datei des Assets unter ihrem Originalnamen in den Zielordner. Ausgänge „Erfolg“ und „Fehler“.

| Einstellung | Bedeutung | Standard |
|---|---|---|
| Zielordner | Absoluter Pfad, Laufwerksbuchstabe oder Netzpfad | – |
| Checksumme | `Keine`, `MD5` oder `SHA-256` | Keine |
| Namenskonflikt lösen | Ist der Name im Ziel belegt, wird `_1`, `_2` … vor der Endung angehängt | aus |
| Parallele Verarbeitung | So viele Assets bearbeitet ein Job gleichzeitig, jedes in einem eigenen Strom (1–16) | 1 |

- Ohne Checksumme gilt die Kopie als erfolgreich, sobald das Betriebssystem den Schreibvorgang bestätigt. Mit Checksumme wird die Kopie nachgelesen; stimmen die Prüfsummen nicht, wird die Kopie gelöscht und das Asset geht auf „Fehler“.
- Erfolg: Das Original ist im Durchlauf beendet (Log „Kopiert nach …“). Die Kopie wird ein neues Asset (Log „Kopie von …“) und läuft über „Erfolg“ weiter.
- Ohne „Namenskonflikt lösen“ führt ein belegter Name zu „Fehler“. Vorhandene Dateien werden nie überschrieben.
- Bei Abbruch oder Fehler wird eine unvollständige Kopie entfernt.

### Datei verschieben (Kategorie „Dateien“)

Verschiebt die Datei des Assets unter ihrem Originalnamen in den Zielordner. Ausgänge „Erfolg“ und „Fehler“; danach zeigt das Asset auf den neuen Pfad.

| Einstellung | Bedeutung | Standard |
|---|---|---|
| Zielordner | Absoluter Pfad, Laufwerksbuchstabe oder Netzpfad | – |
| Parallele Verarbeitung | So viele Assets bearbeitet ein Job gleichzeitig, jedes in einem eigenen Strom (1–16) | 1 |

- Auf demselben Laufwerk bzw. derselben Freigabe verschiebt das Betriebssystem die Datei direkt.
- Liegt das Ziel auf einem anderen Laufwerk, wird die Datei kopiert und die Quelle gelöscht – ohne Checksumme. Das Log weist darauf hin.
- Ist der Name im Ziel belegt, geht das Asset auf „Fehler“.

### Umbenennen (Kategorie „Dateien“)

Setzt ein Präfix vor den Dateinamen und ein Suffix vor die Dateiendung, z. B. `clip.mxf` mit Präfix `P_` und Suffix `_v2` → `P_clip_v2.mxf`. Ausgänge „Erfolg“ und „Fehler“; danach zeigt das Asset auf den neuen Namen.

| Einstellung | Bedeutung | Standard |
|---|---|---|
| Präfix | Text vor dem Dateinamen | leer |
| Suffix | Text vor der Dateiendung | leer |
| Namenskonflikt lösen | Ist der neue Name belegt, wird `_1`, `_2` … angehängt; sonst „Fehler“ | aus |

Präfix oder Suffix muss gesetzt sein. Nicht erlaubt sind `/ \ : * ? " < > |` und Steuerzeichen.

### Prioliste (Kategorie „Daten“)

Liste von Einträgen mit Namen, z. B. „Kollektion Defa“, „Kollektion Cintec“. Die Position in der Liste ist die Priorität (Spalte „Prio“). Kein Eingang; der Ausgang „Prioliste“ wird zusätzlich zum Asset-Fluss mit dem Eingang des Moduls verbunden, das die Assets danach verarbeiten soll.

- In den Moduleinstellungen: „Eintrag hinzufügen“, Name direkt bearbeiten, Pfeile zum Hoch- und Runtersetzen.
- Drei-Punkte-Menü: „Bearbeiten“ (noch nicht verfügbar) und „Entfernen“.
- Namen sind Pflicht und eindeutig (Groß-/Kleinschreibung zählt nicht).

## Tests (optional)

```bash
.venv\Scripts\python -m pip install pytest httpx    # Windows
.venv\Scripts\python -m pytest -q
.venv/bin/python -m pip install pytest httpx         # Linux
.venv/bin/python -m pytest -q
```

Prüfregeln der Oberfläche:

```bash
.tools\node\node.exe --test "ui/tests/*.test.mjs"   # Windows
.tools/node/bin/node --test ui/tests/*.test.mjs       # Linux
```

Die Datenbank-Tests starten eine eigene, temporäre PostgreSQL-Instanz mit den Programmen aus `.tools/postgres` und räumen sie danach wieder ab.

## Projektstruktur

```
build.py               Einrichten und Bauen
run.py                 Starten
config/marathon.toml   Server-, Datenbank-, Engine-, Server-Worker-, Ordner-Dialog-, Proxy-, Worker-Client- und Client-Bau-Einstellungen
requirements/          Python-Abhängigkeiten (server.txt, client.txt, client-build.txt)
marathon/common/       Gemeinsame Grundlage (Version)
marathon/worker/       Worker-Laufzeit (python -m marathon.worker)
marathon/client/       Worker-Client für Windows (Tray, Installation, Verfügbarkeit, Zusatzprogramme, Update)
marathon/modules/      Module (je Ordner ein Modultyp, z. B. watchfolder/; _job.py Ausführungsvertrag, _files.py Datei-Helfer)
marathon/server/       Server; static/ enthält die gebaute Oberfläche
  engine/              Engine: Weiterleiten, Jobs, Takt, Worker-Protokoll
  api/                 API-Bereiche (/api/v1)
  db/                  Datenbank: Verbindung, Migrationen, Abfragen
ui/                    Quellcode der Oberfläche
  src/app/             Rahmen, Navigation, Routen
  src/theme/           Farben, Schrift, Status-Farben (zentral)
  src/components/      Wiederverwendbare Bausteine
  src/data/            Datenschicht (Zugriff auf die API)
  src/pages/           Seiten
  src/features/        Asset-Liste, Node-Editor
tests/                 Automatische Tests
docs/mission_log.md    Planung und Fortschritt der Releases
```

Lokal erzeugte Ordner (nicht im Repo): `.venv`, `.tools`, `.build`, `.data`, `.cert`, `ui/node_modules`, `marathon/server/static`.
