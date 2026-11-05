import json
from datetime import datetime, timezone

import pytest

from m2.server.app import create_app


@pytest.fixture
def setup_server(tmp_path):
    modules = tmp_path / "modules"
    definitions = {
        "wait": {"name": "wait", "version": "1.0.0", "kind": "process",
                 "inputs": ["in"], "outputs": ["success", "error"],
                 "capabilities": ["cpu"], "batch": False, "concurrency": 1},
        "copy_file": {"name": "copy_file", "version": "1.0.0", "kind": "action",
                       "inputs": ["in"], "outputs": ["success", "error"],
                       "capabilities": ["io"], "batch": False, "concurrency": 1},
    }
    for name, manifest in definitions.items():
        folder = modules / name
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    clock_value = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    app = create_app(tmp_path / "server.sqlite3", modules, lease_seconds=30,
                     clock=lambda: clock_value[0])
    return app, clock_value
