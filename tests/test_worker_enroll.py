"""Integration tests for worker-client enrollment and token authentication."""

import hashlib
import re
from datetime import datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from sqlalchemy import text

from marathon.server.app import create_app
from marathon.server.config import EngineConfig, WorkerClientConfig
from marathon.server.worker_auth import check_worker_id, hash_token, require_worker


@pytest.fixture
def api_client(db_engine, tmp_path):
    """Provide an enrollment app backed by a migrated test database."""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(
        static_dir=static_dir,
        engine=db_engine,
        worker_token="server-secret",
        engine_config=EngineConfig(poll_timeout_s=1),
    )
    with TestClient(app) as client:
        yield client


def _worker_row(db_engine, worker_id):
    with db_engine.connect() as connection:
        return dict(
            connection.execute(
                text("SELECT * FROM workers WHERE id = :worker_id"), {"worker_id": worker_id}
            ).mappings().one()
        )


def _request(app, token=None):
    headers = [] if token is None else [(b"authorization", f"Bearer {token}".encode())]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "app": app})


def test_enroll_creates_worker_and_returns_only_hashed_token(api_client, db_engine):
    response = api_client.post(
        "/api/v1/worker/enroll",
        json={"hostname": "  Render Node  ", "platform": "linux-x86_64", "client_version": "1.2.3"},
    )

    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"worker_id", "worker_token", "approved"}
    assert re.fullmatch(r"w_[0-9a-f]{16}", result["worker_id"])
    assert result["worker_token"]
    assert result["approved"] is False
    worker = _worker_row(db_engine, result["worker_id"])
    assert worker["name"] == "Render Node"
    assert worker["status"] == "stopped"
    assert worker["capabilities"] == []
    assert worker["skills"] == []
    assert worker["online"] is False
    assert worker["client_version"] == "1.2.3"
    assert worker["ip_address"] == "testclient"
    assert worker["last_seen"] is None
    assert isinstance(worker["created_at"], datetime)
    assert worker["created_at"].tzinfo is not None
    assert worker["token_hash"] == hashlib.sha256(result["worker_token"].encode()).hexdigest()
    assert hash_token(result["worker_token"]) == worker["token_hash"]
    assert result["worker_token"] not in repr(worker)


def test_enroll_can_skip_admin_approval(db_engine, tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(
        static_dir=static_dir,
        engine=db_engine,
        worker_client=WorkerClientConfig(require_admin_approval=False),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/worker/enroll",
            json={"hostname": "node", "platform": "linux", "client_version": "0.1.0"},
        )
    assert response.status_code == 200
    assert response.json()["approved"] is True


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"hostname": "  ", "platform": "linux", "client_version": "1.2.3"}, "Rechnername fehlt"),
        ({"hostname": "x" * 101, "platform": "linux", "client_version": "1.2.3"},
         "Rechnername ist zu lang (max. 100 Zeichen)"),
        ({"hostname": "node", "platform": "p" * 51, "client_version": "1.2.3"},
         "Plattform ist zu lang (max. 50 Zeichen)"),
        ({"hostname": "node", "platform": "linux", "client_version": "1.2"},
         "Ungültige Client-Version"),
    ],
)
def test_enroll_validation(api_client, payload, message):
    response = api_client.post("/api/v1/worker/enroll", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": message}


def test_require_worker_accepts_server_and_enrolled_tokens(api_client, db_engine):
    app = api_client.app
    server_request = _request(app, "server-secret")
    assert require_worker(server_request, db_engine) == "server-worker"

    response = api_client.post(
        "/api/v1/worker/enroll",
        json={"hostname": "node", "platform": "linux", "client_version": "1.2.3"},
    )
    worker = response.json()
    assert require_worker(_request(app, worker["worker_token"]), db_engine) == worker["worker_id"]


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_require_worker_rejects_invalid_tokens(api_client, db_engine, token):
    with pytest.raises(HTTPException) as error:
        require_worker(_request(api_client.app, token), db_engine)
    assert getattr(error.value, "status_code", None) == 401
    assert getattr(error.value, "detail", None) == "Worker-Token ungültig"


def test_check_worker_id_rejects_mismatch():
    check_worker_id("worker-a", "worker-a")
    with pytest.raises(HTTPException) as error:
        check_worker_id("worker-a", "worker-b")
    assert getattr(error.value, "status_code", None) == 403
    assert getattr(error.value, "detail", None) == "Worker-Token gehört zu einem anderen Worker"
