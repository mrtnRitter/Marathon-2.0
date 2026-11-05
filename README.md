# Marathon

## Was ist das?
Marathon verwaltet Workflows auf einem lokalen Server und verteilt Jobs an freigegebene Worker. Die Browseroberfläche zeigt Status, Verlauf und Logs.

## Installation
Benötigt werden Windows und Python 3.12 oder neuer. Im Projektverzeichnis ausführen:

```powershell
python build.py
```

Der Build lädt die benötigten Komponenten herunter, bereitet Server und Oberfläche vor und erstellt das Worker-Paket unter `dist/client/<version>.zip`. Dafür ist eine Internetverbindung nötig.

## Start
Im Projektverzeichnis starten:

```powershell
python -m marathon.server
```

Die Einstellungen stehen in `server.toml`. Unter `[server]` sind `host` und `port` konfigurierbar; die Standardwerte sind `127.0.0.1` und `8765`. Für HTTPS dienen `tls_certfile` und `tls_keyfile`.

## Aufruf
Bei Standardeinstellungen die Oberfläche unter [http://127.0.0.1:8765](http://127.0.0.1:8765) öffnen. Bei geänderten Einstellungen die konfigurierte Adresse und den Port verwenden.
