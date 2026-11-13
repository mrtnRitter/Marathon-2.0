"""Tests for repository module catalog discovery and database synchronization."""

# IMPORTS
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.modules import CatalogError, build_catalog, catalog_module, load_catalog, required_keys
from marathon.modules import watchfolder
from marathon.server.app import create_app
from marathon.server.db.catalog_writes import sync_module_types


# FUNC
def _spec(type_id: str, sort_order: int = 0) -> dict[str, Any]:
    return {
        "id": type_id,
        "name": type_id,
        "category": "test",
        "icon": "test",
        "description": "",
        "has_input": False,
        "outputs": [],
        "settings": [],
        "sort_order": sort_order,
    }


def test_load_catalog_contains_watchfolder():
    spec = next(spec for spec in load_catalog() if spec["id"] == "watchfolder")

    assert spec == {key: watchfolder.MODULE[key] for key in required_keys}
    assert [setting["key"] for setting in spec["settings"]] == [
        "path", "include", "exclude", "wait_s", "recursive",
    ]
    assert spec["has_input"] is False
    assert [output["id"] for output in spec["outputs"]] == ["success"]


def test_build_catalog_rejects_missing_key():
    module = SimpleNamespace(__name__="x", MODULE={"id": "a"})

    with pytest.raises(CatalogError, match="Modul x: Schlüssel fehlt: name"):
        build_catalog([module])


def test_build_catalog_rejects_duplicate_id():
    modules = [SimpleNamespace(__name__=name, MODULE=_spec("a")) for name in ("one", "two")]

    with pytest.raises(CatalogError, match="Modultyp-ID doppelt: a"):
        build_catalog(modules)


def test_build_catalog_sorts_and_skips_modules_without_descriptor():
    modules = [
        SimpleNamespace(__name__="b", MODULE=_spec("b", 2)),
        SimpleNamespace(__name__="z", MODULE=_spec("z", 1)),
        SimpleNamespace(__name__="a", MODULE=_spec("a", 1)),
        SimpleNamespace(__name__="empty"),
    ]

    catalog = build_catalog(modules)

    assert [spec["id"] for spec in catalog] == ["a", "z", "b"]
    catalog[0]["name"] = "changed"
    assert modules[2].MODULE["name"] == "a"


def test_catalog_module_lookup():
    assert catalog_module("watchfolder") is watchfolder
    assert catalog_module("nope") is None


def test_sync_module_types_upserts(db_engine):
    spec = next(spec for spec in load_catalog() if spec["id"] == "watchfolder")
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO module_types "
                "(id, name, category, icon, description, has_input, outputs, settings, sort_order) "
                "VALUES ('source', 'Quelle', 'test', 'test', '', false, '[]'::jsonb, '[]'::jsonb, 0)"
            )
        )
        assert sync_module_types(connection, [spec]) == 1
        changed_spec = {**deepcopy(spec), "name": "Geändert"}
        assert sync_module_types(connection, [changed_spec]) == 1
        row = connection.execute(
            text("SELECT name, settings FROM module_types WHERE id = 'watchfolder'")
        ).mappings().one()
        source_name = connection.execute(
            text("SELECT name FROM module_types WHERE id = 'source'")
        ).scalar_one()
        count = connection.execute(text("SELECT COUNT(*) FROM module_types")).scalar_one()

    assert row["name"] == "Geändert"
    assert row["settings"] == spec["settings"]
    assert source_name == "Quelle"
    assert count == 2


def test_app_syncs_catalog_only_when_enabled(db_engine, tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")

    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        existing = client.get("/api/v1/module-types")
    assert existing.status_code == 200
    assert all(spec["id"] != "watchfolder" for spec in existing.json())

    with TestClient(create_app(static_dir=tmp_path, engine=db_engine, sync_catalog=True)) as client:
        synced = client.get("/api/v1/module-types")
    assert synced.status_code == 200
    watchfolder_spec = next(spec for spec in synced.json() if spec["id"] == "watchfolder")
    assert len(watchfolder_spec["settings"]) == 5
