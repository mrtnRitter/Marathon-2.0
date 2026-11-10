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

## Release 2.0.0-db.1 – PostgreSQL-Grundlage · in Arbeit

**Ziel:** Alles, was die Oberfläche zeigt, kommt aus der Datenbank. Die bisherigen Beispieldaten werden zu Demo-Daten in der Datenbank. Nur lesend; Speichern bleibt im Backlog.

- [ ] `build.py`: PostgreSQL 18.6 (zonky, Maven Central, feste Prüfsumme) nach `.tools/postgres`, Datenordner `.data/postgres` mit erzeugtem Passwort
- [ ] Datenbank-Schicht mit Migrationen: Modultypen, Workflows mit Versionen, Modulen und Verbindungen, Assets, Jobs, Historie, Logs, Worker
- [ ] Demo-Daten: `python run.py --demo-data` füllt eine leere Datenbank
- [ ] Lese-API für alle Ansichten, Oberfläche liest nur noch über die API
- [ ] `run.py`: lokale Datenbank starten und stoppen, auch mit `--dev`
- [ ] Hilfe-Button entfällt
- [ ] Dashboard in zwei Sektionen: Workflows (Übersicht und Liste) und Worker (online/offline, Name, IP-Adresse)
- [ ] Favicon gleich dem Logo in der Seitenleiste
- [ ] Editor: Module links, Einstellungen rechts bei genau einem ausgewählten Modul
- [ ] Editor: Schloss je Modul, Auswahlrahmen mit linker Maustaste, Verschieben/Löschen im Batch, Verschieben der Ansicht mit mittlerer Maustaste

---

## Backlog (aus dem Architekturkonzept, Reihenfolge offen)

- [ ] Engine: Jobs veröffentlichen, zuteilen, Ergebnisse auswerten
- [ ] Modulkatalog und Modul-Schnittstelle, Testmodul „Warten“
- [ ] Workflows speichern (Versionen, Module, Verbindungen)
- [ ] Assets hinzufügen: manuell und einfacher Listenimport
- [ ] Live-Kanal zum Browser
- [ ] Worker-Client: Starter, Client-Laufzeit, Auto-Update mit Rückfall
- [ ] Rollout von Client-Versionen
- [ ] Andockstelle Login
