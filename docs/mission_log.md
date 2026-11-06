# Mission-Log Marathon 2.0

Laufendes Protokoll: Hier planen wir den nächsten Release und haken ab, was eingebaut und abgenommen wurde.

- Ein Release ist ein kleiner, lauffähiger Schritt.
- `[ ]` geplant · `[x]` eingebaut und abgenommen
- Grundlage: `docs/m2_architektur_vorueberlegungen.md`

---

## Release 2.0.0-ui.1 – UI-Prototyp Look & Feel · in Arbeit

**Ziel:** Oberfläche im Stil von FileFlows mit Beispieldaten, ohne echte Funktion.

- [ ] `build.py`: lokale Python-Umgebung, Node.js lokal, UI-Bibliotheken, UI-Build
- [ ] `run.py`: Server-Start und Live-Vorschau (`--dev`)
- [ ] Server: liefert die gebaute Oberfläche aus, `GET /api/v1/health`
- [ ] Rahmen: feste Seitenleiste, nur Dark Theme, JetBrains Mono lokal, zentrale Farben und Status-Farben
- [ ] Dashboard: je Workflow Name, aktiv/pausiert, Assets je Gesamtstatus, Sprung zu Module/Assets
- [ ] Workflows: Liste und Workflow-Ansicht mit Reitern Module/Assets
- [ ] Node-Editor: Modus Überwachen/Bearbeiten, Modul-Palette, Einstellungsformular, Modulsicht
- [ ] Assets: globale Liste mit Workflow-Filter, Status-Reiter, Aktionsleiste, Asset-Detail mit Zeitstrahl und Log
- [ ] Worker-Seite mit Aktionsleiste
- [ ] Einstellungen als Platzhalterseite
- [ ] Beispieldaten hinter einer austauschbaren Datenschicht

---

## Nächster Release (Vorschlag) 2.0.0-db.1 – PostgreSQL-Grundlage

- [ ] `build.py`: PostgreSQL-Programmdateien nach `.tools`, Datenordner nach `.data`
- [ ] `run.py`: lokale Datenbank starten und stoppen
- [ ] Datenbank-Schicht mit Migrationen, erste Tabellen (Workflows, Assets)
- [ ] Erste Lese-API für die Oberfläche, Dashboard liest echte Daten

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
