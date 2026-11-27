"""Access-management API tests backed by local PostgreSQL."""

# IMPORTS
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from auth_helpers import create_test_user, sign_in
from marathon.server.api.deps import current_user
from marathon.server.app import create_app
from marathon.server.auth.sessions import AuthUser
from marathon.server.config import EditLockConfig
from records import insert_records


# FIXTURES
@pytest.fixture
def access_api(db_engine):
    """Create an isolated API with owned workflow and macro records."""
    insert_records(db_engine, datetime.now(timezone.utc))
    with db_engine.begin() as connection:
        admin_account = create_test_user(connection, "admin@test.local", "admin")
        owner = create_test_user(connection, "owner@test.local")
        guest = create_test_user(connection, "guest@test.local")
        second_user = create_test_user(connection, "second@test.local")
        connection.execute(
            text("UPDATE workflows SET owner_user_id = :owner_id WHERE id = 'wf-a'"),
            {"owner_id": owner.id},
        )
        connection.execute(
            text("UPDATE macros SET owner_user_id = :owner_id WHERE id = 'step'"),
            {"owner_id": owner.id},
        )

    app = create_app(engine=db_engine)
    admin = sign_in(app, admin_account)
    with TestClient(app) as client:
        yield app, client, admin, owner, guest, second_user


def _as_user(app, user: AuthUser) -> None:
    app.dependency_overrides.clear()
    app.dependency_overrides.update({current_user: lambda: user})


def _event_rows(db_engine, object_type="workflow", object_id="wf-a"):
    with db_engine.begin() as connection:
        return connection.execute(
            text(
                "SELECT actor_name, text FROM object_events "
                "WHERE object_type = :object_type AND object_id = :object_id ORDER BY id"
            ),
            {"object_type": object_type, "object_id": object_id},
        ).mappings().all()


# TESTS
@pytest.mark.parametrize("object_type,object_id", [("workflow", "wf-a"), ("macro", "step")])
def test_get_and_replace_shares_for_workflows_and_macros(access_api, object_type, object_id):
    app, client, admin, owner, guest, _ = access_api
    response = client.get(f"/api/v1/access/{object_type}/{object_id}/shares")
    assert response.status_code == 200
    assert response.json() == {"owner": {"id": owner.id, "email": owner.email, "display_name": ""}, "shares": []}

    response = client.put(
        f"/api/v1/access/{object_type}/{object_id}/shares",
        json={"shares": [{"user_id": guest.id, "access": "view"}, {"user_id": None, "access": "use"}]},
    )
    assert response.status_code == 200
    assert response.json()["shares"] == [
        {"user_id": None, "email": "", "display_name": "Alle", "access": "use"},
        {"user_id": guest.id, "email": guest.email, "display_name": "", "access": "view"},
    ]
    assert client.put(f"/api/v1/access/{object_type}/{object_id}/shares", json={"shares": []}).json()["shares"] == []


@pytest.mark.parametrize(
    "shares,detail",
    [
        ([{"user_id": "missing-user", "access": "view"}], "Unbekannter Nutzer: missing-user"),
        ([{"user_id": "emergency", "access": "view"}], "Unbekannter Nutzer: emergency"),
        ([{"user_id": "owner", "access": "view"}], "Der Besitzer kann nicht freigegeben werden"),
        ([{"user_id": None, "access": "view"}, {"user_id": None, "access": "edit"}], "„Alle“ doppelt"),
        ([{"user_id": "guest", "access": "view"}, {"user_id": "guest", "access": "use"}], "Nutzer doppelt: guest"),
        ([{"user_id": None, "access": "run"}], "Ungültige Freigabestufe: run"),
    ],
)
def test_put_shares_returns_validation_errors_without_changes(access_api, db_engine, shares, detail):
    app, client, _, owner, guest, _ = access_api
    _as_user(app, owner)
    resolved = []
    for share in shares:
        user_id = share["user_id"]
        if user_id == "owner":
            share["user_id"] = owner.id
        elif user_id == "guest":
            share["user_id"] = guest.id
        resolved.append(share)
    response = client.put("/api/v1/access/workflow/wf-a/shares", json={"shares": resolved})
    assert response.status_code == 422
    expected_detail = detail.replace(": guest", f": {guest.id}")
    assert response.json() == {"detail": expected_detail}
    with db_engine.begin() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM object_shares WHERE object_type = 'workflow' AND object_id = 'wf-a'")
        ).scalar_one() == 0


def test_access_order_unknown_type_hidden_and_insufficient_level(access_api, db_engine):
    app, client, _, owner, guest, _ = access_api
    assert client.get("/api/v1/access/nope/wf-a/shares").json() == {"detail": "Unbekannter Objekttyp"}

    _as_user(app, guest)
    assert client.get("/api/v1/access/workflow/wf-a/shares").status_code == 404
    for method, suffix in (("get", "events"), ("post", "lock"), ("get", "lock"), ("delete", "lock")):
        assert getattr(client, method)(f"/api/v1/access/workflow/wf-a/{suffix}").status_code == 404
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO object_shares (object_type, object_id, user_id, access) VALUES ('workflow', 'wf-a', :id, 'view')"),
            {"id": guest.id},
        )
    assert client.get("/api/v1/access/workflow/wf-a/shares").status_code == 403
    assert client.get("/api/v1/access/workflow/wf-a/events").status_code == 403
    assert client.put("/api/v1/access/workflow/wf-a/shares", json={"shares": []}).status_code == 403
    assert client.put("/api/v1/access/workflow/wf-a/owner", json={"user_id": None}).status_code == 403
    assert client.post("/api/v1/access/workflow/wf-a/lock").status_code == 403
    assert client.delete("/api/v1/access/workflow/wf-a/lock?force=true").status_code == 403

    with db_engine.begin() as connection:
        connection.execute(text("UPDATE macros SET archived_at = now() WHERE id = 'step'"))
    assert client.get("/api/v1/access/macro/step/shares").status_code == 404
    assert client.get("/api/v1/access/macro/step/shares").json() == {"detail": "Modul nicht gefunden"}
    assert owner.id != guest.id


def test_admin_share_intervention_events_and_owner_change(access_api, db_engine):
    app, client, admin, owner, _, second_user = access_api
    admin_action = client.put("/api/v1/access/workflow/wf-a/shares", json={"shares": []})
    assert admin_action.status_code == 200
    assert [(row["text"]) for row in _event_rows(db_engine)] == ["Freigaben geändert"]
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE workflows SET owner_user_id = :owner_id WHERE id = 'wf-b'"),
            {"owner_id": admin.id},
        )
    assert client.put("/api/v1/access/workflow/wf-b/shares", json={"shares": []}).status_code == 200
    assert _event_rows(db_engine, object_id="wf-b") == []

    _as_user(app, owner)
    assert client.get("/api/v1/access/workflow/wf-a/events").json()["events"][0]["text"] == "Freigaben geändert"
    assert client.put("/api/v1/access/workflow/wf-a/shares", json={"shares": []}).status_code == 200
    assert len(_event_rows(db_engine)) == 1
    assert client.put("/api/v1/access/workflow/wf-a/owner", json={"user_id": None}).status_code == 403

    _as_user(app, admin)
    response = client.put("/api/v1/access/workflow/wf-a/owner", json={"user_id": second_user.id})
    assert response.status_code == 200
    assert response.json()["owner"]["id"] == second_user.id
    assert [row["text"] for row in _event_rows(db_engine)] == [
        "Freigaben geändert", "Besitzer geändert: " + (owner.display_name or owner.email) + " → " + (second_user.display_name or second_user.email)
    ]
    invalid = client.put("/api/v1/access/workflow/wf-a/owner", json={"user_id": "missing-user"})
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "Unbekannter Nutzer: missing-user"}
    ownerless = client.put("/api/v1/access/workflow/wf-a/owner", json={"user_id": None})
    assert ownerless.status_code == 200
    assert ownerless.json()["owner"] is None
    assert _event_rows(db_engine)[-1]["text"] == "Besitzer geändert: " + (second_user.display_name or second_user.email) + " → Admin"


def test_owner_admin_guard_and_archived_module_guard_order(access_api, db_engine):
    app, client, _, owner, _, _ = access_api
    _as_user(app, owner)
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE macros SET archived_at = now() WHERE id = 'step'"))
    denied = client.put("/api/v1/access/macro/step/owner", json={"user_id": None})
    assert denied.status_code == 403
    assert denied.json() == {"detail": "Nur für Admins"}


def test_lock_acquire_renew_conflict_status_release_and_admin_break(access_api, db_engine):
    app, client, admin, owner, guest, _ = access_api
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO object_shares (object_type, object_id, user_id, access) VALUES ('workflow', 'wf-a', :id, 'edit')"),
            {"id": guest.id},
        )
    _as_user(app, owner)
    path = "/api/v1/access/workflow/wf-a/lock"
    first = client.post(path)
    assert first.status_code == 200
    first_data = first.json()
    assert first_data["heartbeat_s"] == 30
    renewed = client.post(path, headers={"X-Edit-Lock": first_data["token"]})
    assert renewed.status_code == 200
    assert renewed.json()["token"] == first_data["token"]
    assert client.post(path).status_code == 409

    status_response = client.get(path)
    assert status_response.status_code == 200
    assert status_response.json()["holder"]["id"] == owner.id
    _as_user(app, guest)
    assert client.get(path).status_code == 200
    assert client.post(path).status_code == 409
    _as_user(app, owner)
    assert client.delete(path, headers={"X-Edit-Lock": first_data["token"]}).status_code == 204
    _as_user(app, guest)
    assert client.get(path).json() == {"holder": None, "expires_at": None}

    _as_user(app, owner)
    held = client.post(path).json()
    _as_user(app, admin)
    assert client.delete(path + "?force=true").status_code == 204
    assert [row["text"] for row in _event_rows(db_engine)] == ["Bearbeitungssperre von " + (owner.display_name or owner.email) + " aufgehoben"]
    _as_user(app, owner)
    replacement = client.post(path, headers={"X-Edit-Lock": held["token"]})
    assert replacement.status_code == 200
    assert replacement.json()["token"] != held["token"]


def test_lock_force_admin_guard_and_macro_routes(access_api):
    app, client, _, owner, _, _ = access_api
    _as_user(app, owner)
    path = "/api/v1/access/macro/step/lock"
    assert client.post(path).status_code == 200
    assert client.delete(path + "?force=true").status_code == 403
    assert client.get(path).status_code == 200
    assert client.delete(path, headers={"X-Edit-Lock": "wrong"}).status_code == 204


def test_create_app_uses_default_edit_lock_ttl():
    app = create_app()
    assert app.state.edit_lock == EditLockConfig()
    assert app.state.edit_lock.ttl_s == 120
