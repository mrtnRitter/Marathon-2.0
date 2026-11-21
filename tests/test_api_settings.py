"""Settings API integration tests."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marathon.server.app import create_app
from marathon.server.config import (
    ProxyConfig,
    public_url_error,
    trusted_proxies_error,
    trusted_proxies_required_error,
)
from records import insert_records


fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
default_proxy = {"public_url": "https://localhost", "builtin": True, "trusted_proxies": []}


@pytest.fixture
def api_client(db_engine, tmp_path):
    """Insert baseline records and provide an API client."""
    insert_records(db_engine, fixed_now)
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with TestClient(create_app(static_dir=tmp_path, engine=db_engine)) as client:
        yield client


def settings(folder_address="", proxy=None):
    return {
        "folder_dialog": {"server_address": folder_address},
        "proxy": default_proxy if proxy is None else proxy,
    }


def put_settings(api_client, folder_address="", proxy=None):
    return api_client.put(
        "/api/v1/settings",
        json={"folder_dialog": {"server_address": folder_address}, "proxy": default_proxy if proxy is None else proxy},
    )


def test_get_settings_without_row_returns_empty_address(api_client):
    response = api_client.get("/api/v1/settings")

    assert response.status_code == 200
    assert response.json() == settings()


def test_put_settings_trims_and_persists_address(api_client, db_engine):
    response = put_settings(api_client, " 10.0.77.11 ")

    expected = settings("10.0.77.11")
    assert response.status_code == 200
    assert response.json() == expected
    assert api_client.get("/api/v1/settings").json() == expected
    with db_engine.connect() as connection:
        row = connection.execute(
            text("SELECT value FROM settings WHERE key = 'folder_dialog.server_address'")
        ).mappings().one()
    assert row["value"] == "10.0.77.11"


def test_put_empty_address(api_client):
    response = put_settings(api_client)

    assert response.status_code == 200
    assert response.json() == settings()


@pytest.mark.parametrize("server_address", ["\\\\server", "a b"])
def test_invalid_address_is_rejected_without_changing_value(api_client, server_address):
    put_settings(api_client, "host-1")

    response = put_settings(api_client, server_address)

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Server-Adresse: nur Rechnername oder IP-Adresse, z. B. 10.0.77.11"
    }
    assert api_client.get("/api/v1/settings").json() == settings("host-1")


def test_put_updates_updated_at(api_client, db_engine):
    put_settings(api_client, "host-1")
    old_updated_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE settings SET updated_at = :updated_at "
                "WHERE key = 'folder_dialog.server_address'"
            ),
            {"updated_at": old_updated_at},
        )

    response = put_settings(api_client, "host-1")

    assert response.status_code == 200
    with db_engine.connect() as connection:
        updated_at = connection.execute(
            text("SELECT updated_at FROM settings WHERE key = 'folder_dialog.server_address'")
        ).scalar_one()
    assert updated_at != old_updated_at


def test_put_proxy_normalizes_and_persists(api_client, db_engine):
    response = put_settings(
        api_client,
        proxy={
            "public_url": " HTTPS://Laptop-Martin-R/ ",
            "builtin": False,
            "trusted_proxies": [" 10.0.77.20 ", "10.0.77.20"],
        },
    )
    expected_proxy = {
        "public_url": "https://laptop-martin-r",
        "builtin": False,
        "trusted_proxies": ["10.0.77.20"],
    }

    assert response.status_code == 200
    assert response.json()["proxy"] == expected_proxy
    assert api_client.get("/api/v1/settings").json()["proxy"] == expected_proxy
    with db_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT key, value FROM settings WHERE key LIKE 'proxy.%' ORDER BY key")
        ).mappings().all()
    assert {row["key"]: row["value"] for row in rows} == {
        "proxy.public_url": "https://laptop-martin-r",
        "proxy.builtin": False,
        "proxy.trusted_proxies": ["10.0.77.20"],
    }


@pytest.mark.parametrize(
    ("proxy", "error"),
    [
        ({"public_url": "http://x", "builtin": True, "trusted_proxies": []}, public_url_error),
        ({"public_url": "https://host", "builtin": True, "trusted_proxies": ["host"]}, trusted_proxies_error),
        ({"public_url": "https://host", "builtin": False, "trusted_proxies": []}, trusted_proxies_required_error),
    ],
)
def test_put_invalid_proxy_rejected_without_changes(api_client, proxy, error):
    put_settings(api_client, "host-1")
    before = api_client.get("/api/v1/settings").json()

    response = put_settings(api_client, "changed", proxy)

    assert response.status_code == 422
    assert response.json() == {"detail": error}
    assert api_client.get("/api/v1/settings").json() == before


def test_put_without_proxy_section_is_rejected(api_client):
    before = api_client.get("/api/v1/settings").json()

    response = api_client.put("/api/v1/settings", json={"folder_dialog": {"server_address": "changed"}})

    assert response.status_code == 422
    assert api_client.get("/api/v1/settings").json() == before


def test_put_builtin_must_be_boolean(api_client):
    response = put_settings(
        api_client,
        proxy={"public_url": "https://host", "builtin": "yes", "trusted_proxies": []},
    )

    assert response.status_code == 422


def test_get_proxy_config(db_engine):
    from marathon.server.db.settings_writes import get_proxy_config, save_settings

    with db_engine.begin() as connection:
        assert get_proxy_config(connection) == ProxyConfig()
        assert save_settings(
            connection,
            "",
            public_url="https://laptop-martin-r",
            builtin=False,
            trusted_proxies=["10.0.77.20"],
        )["proxy"] == {
            "public_url": "https://laptop-martin-r",
            "builtin": False,
            "trusted_proxies": ["10.0.77.20"],
        }
        assert get_proxy_config(connection) == ProxyConfig(
            "https://laptop-martin-r", False, ("10.0.77.20",)
        )
