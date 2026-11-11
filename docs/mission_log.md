# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` geplant · `[x]` eingebaut und abgenommen
- Grundlage: `docs/m2_architektur_vorueberlegungen.md`

---

## Release 2.0.0-ui.1 – UI-Prototyp Look & Feel · abgeschlossen

**Ziel:** Oberfläche im Stil von FileFlows mit Beispieldaten, ohne echte Funktion.

- [x] `build.py`: lokale Python-Umgebung, Node.js lokal, UI-Bibliotheken, UI-Build
- [x] `run.py`: Server-Start und Live-Vorschau (`--dev`)
- [x] Server: liefert die gebaute Oberfläche aus, `GET /api/v1/health`
- [x] Rahmen: feste Seitenleiste, nur Dark Theme, JetBrains Mono lokal, zentrale Farben und Status-Farben
- [x] Dashboard: je Workflow Name, aktiv/pausiert, Assets je Gesamtstatus, Sprung zu Module/Assets
- [x] Workflows: Liste und Workflow-Ansicht mit Reitern Module/Assets
- [x] Node-Editor: Modus Überwachen/Bearbeiten, Modul-Palette, Einstellungsformular, Modulsicht
- [x] Assets: globale Liste mit Workflow-Filter, Status-Reiter, Aktionsleiste, Asset-Detail mit Zeitstrahl und Log
- [x] Worker-Seite mit Aktionsleiste
- [x] Einstellungen als Platzhalterseite
- [x] Beispieldaten hinter einer austauschbaren Datenschicht

---

## Release 2.0.0-db.1 – PostgreSQL-Grundlage · gebaut, Abnahme durch Martin offen

**Ziel:** Alles, was die Oberfläche zeigt, kommt aus der Datenbank. Die bisherigen Beispieldaten werden zu Demo-Daten in der Datenbank. Nur lesend; Speichern bleibt im Backlog.

- [x] `build.py`: PostgreSQL 18.6 (zonky, Maven Central, feste Prüfsumme) nach `.tools/postgres`, Datenordner `.data/postgres` mit erzeugtem Passwort
- [x] Datenbank-Schicht mit Migrationen: Modultypen, Workflows mit Versionen, Modulen und Verbindungen, Assets, Jobs, Historie, Logs, Worker
- [x] Demo-Daten: `python run.py --demo-data` füllt eine leere Datenbank
- [x] Lese-API für alle Ansichten, Oberfläche liest nur noch über die API
- [x] `run.py`: lokale Datenbank starten und stoppen, auch mit `--dev`
- [x] Hilfe-Button entfällt
- [x] Dashboard in zwei Sektionen: Workflows (Übersicht und Liste) und Worker (online/offline, Name, IP-Adresse)
- [x] Favicon gleich dem Logo in der Seitenleiste
- [x] Editor: Module links, Einstellungen rechts bei genau einem ausgewählten Modul
- [x] Editor: Schloss je Modul, Auswahlrahmen mit linker Maustaste, Verschieben/Löschen im Batch, Verschieben der Ansicht mit mittlerer Maustaste

---

## Backlog (Reihenfolge offen)

### Engine und Worker

- [ ] Engine: Jobs veröffentlichen, zuteilen, Ergebnisse auswerten
- [ ] Live-Kanal zum Browser
- [ ] Worker-Client: Starter, Client-Laufzeit, Auto-Update mit Rückfall
- [ ] Rollout von Client-Versionen

### Module

- [ ] Modulkatalog und Modul-Schnittstelle, Testmodul „Warten“
- [ ] Module liegen immer im Repo (anders als bei m1)
- [ ] Module für den ersten Release: Watchfolder, Transcoder, Mover, Renamer, QCer
- [ ] Module für den nächsten Release: API-Abfrage ES und VT, Restorer
- [ ] Teilworkflows als Modul neben den Basis-Modulen; dafür einzelne Einstellungen als „public“ markieren, damit sie gleich oben erscheinen

### Workflows, Assets und Daten

- [ ] Workflows speichern (Versionen, Module, Verbindungen)
- [ ] Assets hinzufügen: manuell und einfacher Listenimport
- [ ] Dummy-Daten überall löschen
- [ ] Aufbewahrung der Daten
  - Offen: Daten pro Workflow oder global aufheben?

### Reports

- [ ] Eigene Ansicht Reports mit Datumsauswahl, Workflowauswahl usw.
- [ ] Export von Reports als CSV (nächster Release)

### Zugang und Sicherheit

- [ ] Andockstelle Login
- [ ] HTTPS-Verbindung
  - Offen: Braucht m2 das Zertifikat selbst, oder übernimmt das der übergeordnete Server/Reverse-Proxy?
- [ ] Workflows freigeben/sperren
  - Offen: Wie werden Workflows freigegeben und gesperrt?
  - Idee Ökosystem: Login auf einer Landingpage mit Übersicht der Tools, u. a. m2 → in m2 sind bestimmte Workflows gesperrt; ungern ein zweiter Login in m2

### UI-Feinschliff

- [ ] Icon: „m“ größer, besonders im Favicon
- [ ] Dashboard: mehr Abstand zwischen der Workflow-Übersicht (obere Tabelle) und den Zeilen darunter
- [ ] Workflows → Bearbeiten: Auswahlrahmen nach der Auswahl ausblenden
- [ ] Workflows → Nodes: zusätzlich zum Namen eine Beschreibung, unter dem Typ des Nodes; ohne Beschreibung kein freier Platz
- [ ] Workflows → Nodes: Seitenleiste mit den Einstellungen in der Breite verschiebbar, Anfasser ist die Trennlinie
- [ ] In Übersicht Workflows oberer Tabelle: jeden jobstatus anklickbar machen, wenn man drauf klickt, kommt am gleich zu den Assets, die diesen Status haben
