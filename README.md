# Marathon 2.0 prototype server

## Start

From the repository root, install the project and start the server:

```bash
python -m pip install -e .
python -m m2.server
```

The API and static web UI are served at <http://localhost:8000>. The SQLite database defaults to `m2.sqlite3` in the current directory. Set `M2_DB_PATH` to choose another database file, and `M2_MODULES_DIR` to load manifests from another directory. By default manifests are loaded from `m2/modules/*/manifest.json`.

Start an agent in a second terminal from the repository root:

```bash
python -m m2.agent --server http://localhost:8000 --name local-agent --capabilities '' --config agent.example.json
```

The example config maps the `media` storage to `./media` relative to the current directory.

## Tests

```bash
python -m pytest -q -p no:cacheprovider
```
