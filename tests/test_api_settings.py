"""Settings API integration tests."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.server.app import create_app
from records import insert_records


fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.fixture
def api_client(db_engine, tmp_path):
    """Insert baseline records and provide an API client."""
    insert_records(db_engine, fixed_now)
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        yield client


def test_get_settings_without_row_returns_empty_address(api_client):
    response = api_client.get("/api/v1/settings")

    assert response.status_code == 200
    assert response.json() == {"folder_dialog": {"server_address": ""}}


def test_put_settings_trims_and_persists_address(api_client, db_engine):
    response = api_client.put(
        "/api/v1/settings", json={"folder_dialog": {"server_address": " 10.0.77.11 "}}
    )

    expected = {"folder_dialog": {"server_address": "10.0.77.11"}}
    assert response.status_code == 200
    assert response.json() == expected
    assert api_client.get("/api/v1/settings").json() == expected
    with db_engine.connect() as connection:
        row = connection.execute(
            text("SELECT value FROM settings WHERE key = 'folder_dialog.server_address'")
        ).mappings().one()
    assert row["value"] == "10.0.77.11"


def test_put_empty_address(api_client):
    response = api_client.put(
        "/api/v1/settings", json={"folder_dialog": {"server_address": ""}}
    )

    assert response.status_code == 200
    assert response.json() == {"folder_dialog": {"server_address": ""}}


@pytest.mark.parametrize("server_address", ["\\\\server", "a b"])
def test_invalid_address_is_rejected_without_changing_value(api_client, server_address):
    api_client.put("/api/v1/settings", json={"folder_dialog": {"server_address": "host-1"}})

    response = api_client.put(
        "/api/v1/settings", json={"folder_dialog": {"server_address": server_address}}
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Server-Adresse: nur Rechnername oder IP-Adresse, z. B. 10.0.77.11"
    }
    assert api_client.get("/api/v1/settings").json() == {
        "folder_dialog": {"server_address": "host-1"}
    }


def test_put_updates_updated_at(api_client, db_engine):
    api_client.put("/api/v1/settings", json={"folder_dialog": {"server_address": "host-1"}})
    old_updated_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE settings SET updated_at = :updated_at "
                "WHERE key = 'folder_dialog.server_address'"
            ),
            {"updated_at": old_updated_at},
        )

    response = api_client.put(
        "/api/v1/settings", json={"folder_dialog": {"server_address": "host-1"}}
    )

    assert response.status_code == 200
    with db_engine.connect() as connection:
        updated_at = connection.execute(
            text("SELECT updated_at FROM settings WHERE key = 'folder_dialog.server_address'")
        ).scalar_one()
    assert updated_at != old_updated_at
