"""Tests for the server-side directory browser API."""

# IMPORTS
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

import pytest
from fastapi.testclient import TestClient

from marathon.server.api import fs
from marathon.server.app import create_app


# FIXTURES
@pytest.fixture
def api_client():
    """Provide an API client without opening a database connection."""
    with TestClient(create_app()) as client:
        yield client


# TESTS
def test_lists_only_dirs_sorted(api_client, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "B").mkdir()
    (tmp_path / "c.txt").write_text("file")

    response = api_client.get("/api/v1/fs/dirs", params={"path": str(tmp_path)})

    assert response.status_code == 200
    body = response.json()
    assert [entry["name"] for entry in body["dirs"]] == ["a", "B"]
    assert [entry["path"] for entry in body["dirs"]] == [
        os.path.join(str(tmp_path), "a"),
        os.path.join(str(tmp_path), "B"),
    ]
    assert body["parent"] == str(tmp_path.parent)
    assert body["truncated"] is False


def test_missing_dir_404(api_client, tmp_path):
    path = tmp_path / "missing"

    response = api_client.get("/api/v1/fs/dirs", params={"path": str(path)})

    assert response.status_code == 404
    assert response.json()["detail"] == f"Ordner nicht gefunden: {path}"


def test_relative_path_422(api_client):
    response = api_client.get("/api/v1/fs/dirs", params={"path": "Ingest"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Pfad muss absolut sein, z. B. D:\\Ingest oder \\\\server\\freigabe"


def test_roots_posix(api_client, monkeypatch):
    monkeypatch.setattr(fs, "os_name", "posix")

    response = api_client.get("/api/v1/fs/dirs")

    assert response.status_code == 200
    assert response.json() == {
        "path": None,
        "parent": None,
        "dirs": [{"name": "/", "path": "/"}],
        "truncated": False,
    }


def test_roots_windows(api_client, monkeypatch):
    monkeypatch.setattr(fs, "os_name", "nt")
    monkeypatch.setattr(fs, "_drive_exists", lambda drive: drive in {"C", "Z"})

    response = api_client.get("/api/v1/fs/dirs")

    assert response.status_code == 200
    assert [entry["name"] for entry in response.json()["dirs"]] == ["C:", "Z:"]
    assert [entry["path"] for entry in response.json()["dirs"]] == ["C:\\", "Z:\\"]


def test_roots_windows_skips_hanging_drive(api_client, monkeypatch):
    release = Event()
    monkeypatch.setattr(fs, "os_name", "nt")

    def drive_exists(drive):
        if drive == "Z":
            release.wait()
            return True
        return drive == "C"

    monkeypatch.setattr(fs, "_drive_exists", drive_exists)
    monkeypatch.setattr(fs, "list_timeout_s", 0.2)
    started = monotonic()
    try:
        response = api_client.get("/api/v1/fs/dirs")
        elapsed = monotonic() - started
        assert response.status_code == 200
        assert [entry["name"] for entry in response.json()["dirs"]] == ["C:"]
        assert elapsed < 2
    finally:
        release.set()


def test_root_has_no_parent(api_client, monkeypatch):
    monkeypatch.setattr(fs, "_list_dirs", lambda path: ([], False))

    response = api_client.get("/api/v1/fs/dirs", params={"path": "/"})

    assert response.status_code == 200
    assert response.json()["parent"] is None


def test_permission_error_403(api_client, monkeypatch, tmp_path):
    def raise_permission_error(path):
        raise PermissionError(path)

    monkeypatch.setattr(fs, "_list_dirs", raise_permission_error)

    response = api_client.get("/api/v1/fs/dirs", params={"path": str(tmp_path)})

    assert response.status_code == 403
    assert response.json()["detail"] == f"Kein Zugriff auf {tmp_path}"


def test_truncated(api_client, monkeypatch, tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(fs, "max_entries", 2)

    response = api_client.get("/api/v1/fs/dirs", params={"path": str(tmp_path)})

    assert response.status_code == 200
    assert [entry["name"] for entry in response.json()["dirs"]] == ["a", "b"]
    assert response.json()["truncated"] is True


def test_hanging_listing_returns_504_quickly(api_client, monkeypatch, tmp_path):
    release = Event()
    monkeypatch.setattr(fs, "list_timeout_s", 0.2)
    monkeypatch.setattr(fs, "_list_dirs", lambda path: (release.wait(), False))
    started = monotonic()
    try:
        response = api_client.get("/api/v1/fs/dirs", params={"path": str(tmp_path)})
        assert response.status_code == 504
        assert monotonic() - started < 2
    finally:
        release.set()


def test_saturated_pool_still_times_out(api_client, monkeypatch, tmp_path):
    release = Event()
    monkeypatch.setattr(fs, "list_timeout_s", 0.2)
    monkeypatch.setattr(fs, "_list_dirs", lambda path: (release.wait(), False))
    started = monotonic()
    try:
        with ThreadPoolExecutor(max_workers=5) as callers:
            responses = list(
                callers.map(
                    lambda _: api_client.get("/api/v1/fs/dirs", params={"path": str(tmp_path)}),
                    range(5),
                )
            )
        assert all(response.status_code == 504 for response in responses)
        assert monotonic() - started < 2
    finally:
        release.set()


def test_visible_shares_filters_and_sorts():
    result = fs._visible_shares(
        [("Media", 0), ("C$", 0x80000000), ("print", 1), ("IPC$", 0x80000003), ("archiv", 0)],
        "srv",
    )

    assert result == [
        {"name": "archiv", "path": r"\\srv\archiv"},
        {"name": "Media", "path": r"\\srv\Media"},
    ]


def test_host_lists_shares(api_client, monkeypatch):
    expected_dirs = [{"name": "Media", "path": r"\\srv\Media"}]
    monkeypatch.setattr(fs, "os_name", "nt")
    monkeypatch.setattr(fs, "_list_shares", lambda host: (expected_dirs, False), raising=False)

    for path in (r"\\srv", "\\\\srv\\"):
        response = api_client.get("/api/v1/fs/dirs", params={"path": path})
        assert response.status_code == 200
        assert response.json() == {
            "path": r"\\srv",
            "parent": None,
            "dirs": expected_dirs,
            "truncated": False,
        }


def test_host_on_posix_422(api_client, monkeypatch):
    monkeypatch.setattr(fs, "os_name", "posix")
    monkeypatch.setattr(fs._executor, "submit", lambda *args, **kwargs: pytest.fail("executor must not start"))

    response = api_client.get("/api/v1/fs/dirs", params={"path": r"\\srv"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Freigaben eines Rechners lassen sich nur auf einem Windows-Server anzeigen"


def test_host_hanging_returns_504_quickly(api_client, monkeypatch):
    release = Event()
    monkeypatch.setattr(fs, "os_name", "nt")
    monkeypatch.setattr(fs, "list_timeout_s", 0.2)
    monkeypatch.setattr(fs, "_list_shares", lambda host: (release.wait(), False), raising=False)
    started = monotonic()
    try:
        response = api_client.get("/api/v1/fs/dirs", params={"path": r"\\srv"})
        assert response.status_code == 504
        assert response.json()["detail"] == r"Rechner antwortet nicht: \\srv"
        assert monotonic() - started < 2
    finally:
        release.set()


def test_host_errors_map_status(api_client, monkeypatch):
    monkeypatch.setattr(fs, "os_name", "nt")
    cases = (
        (PermissionError("denied"), 403, r"Kein Zugriff auf \\srv"),
        (FileNotFoundError("missing"), 404, r"Rechner nicht gefunden: \\srv"),
        (OSError("failure"), 502, r"Freigaben nicht lesbar: \\srv"),
    )
    for error, status_code, detail in cases:
        def raise_error(host, error=error):
            raise error

        monkeypatch.setattr(fs, "_list_shares", raise_error, raising=False)
        response = api_client.get("/api/v1/fs/dirs", params={"path": r"\\srv"})
        assert response.status_code == status_code
        assert response.json()["detail"] == detail


def test_share_root_parent_is_host_on_windows(api_client, monkeypatch):
    monkeypatch.setattr(fs, "os_name", "nt")
    monkeypatch.setattr(fs, "_list_dirs", lambda path: ([], False))

    response = api_client.get("/api/v1/fs/dirs", params={"path": r"\\srv\Media"})

    assert response.status_code == 200
    assert response.json()["path"] == r"\\srv\Media"
    assert response.json()["parent"] == r"\\srv"
