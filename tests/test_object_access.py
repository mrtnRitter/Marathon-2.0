"""Tests for unified workflow and macro access helpers."""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import event, text

from auth_helpers import create_test_user
from marathon.server.auth.sessions import AuthUser
from marathon.server.db.object_access import (
    ShareValidationError,
    access_filter_sql,
    access_level_sql,
    actor_name,
    delete_object_access,
    list_events,
    list_shares,
    log_event,
    log_intervention,
    object_access,
    object_access_map,
    owner_display,
    owner_record,
    replace_shares,
    require_access,
    set_owner,
)
from records import insert_records


fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _user(user_id: str, role: str = "user", display_name: str = "", email: str = "") -> AuthUser:
    return AuthUser(user_id, email or f"{user_id}@access.test", display_name, role, "test-session")


@pytest.fixture
def access_context(db_engine):
    """Create isolated workflows, macros, owners, and direct/global shares."""
    insert_records(db_engine, fixed_now)
    with db_engine.begin() as connection:
        users = {
            name: create_test_user(connection, f"{name}@access.test", role)
            for name, role in (("owner", "user"), ("reader", "user"), ("editor", "user"), ("admin", "admin"))
        }
        display_names = {"owner": "Owner Name", "reader": "Reader Name", "editor": "", "admin": "Admin Name"}
        for name, user in users.items():
            connection.execute(
                text("UPDATE users SET display_name = :name WHERE id = :id"),
                {"name": display_names[name], "id": user.id},
            )
            users[name] = AuthUser(user.id, user.email, display_names[name], user.role, user.session_id)
        connection.execute(
            text("UPDATE workflows SET owner_user_id = :owner WHERE id = 'wf-a'"),
            {"owner": users["owner"].id},
        )
        connection.execute(
            text("UPDATE macros SET owner_user_id = :owner WHERE id IN ('source', 'step')"),
            {"owner": users["owner"].id},
        )
        connection.execute(
            text("UPDATE workflows SET owner_user_id = :owner WHERE id = 'wf-c'"),
            {"owner": users["owner"].id},
        )
        connection.execute(
            text(
                "INSERT INTO object_shares (object_type, object_id, user_id, access) "
                "VALUES (:object_type, :object_id, :user_id, :access)"
            ),
            [
                {"object_type": "workflow", "object_id": "wf-a", "user_id": users["reader"].id, "access": "view"},
                {"object_type": "workflow", "object_id": "wf-a", "user_id": None, "access": "use"},
                {"object_type": "workflow", "object_id": "wf-b", "user_id": users["reader"].id, "access": "edit"},
                {"object_type": "workflow", "object_id": "wf-b", "user_id": None, "access": "view"},
                {"object_type": "macro", "object_id": "source", "user_id": users["reader"].id, "access": "view"},
                {"object_type": "macro", "object_id": "source", "user_id": None, "access": "edit"},
            ],
        )
    return db_engine, users


def test_admin_emergency_and_object_owner_have_owner_access(access_context):
    engine, users = access_context
    admin = users["admin"]
    emergency = _user("emergency", role="admin")
    owner = users["owner"]
    with engine.connect() as connection:
        assert object_access(connection, admin, "workflow", "wf-b") == "owner"
        assert object_access(connection, emergency, "macro", "step") == "owner"
        assert object_access(connection, owner, "workflow", "wf-a") == "owner"
        assert object_access(connection, owner, "macro", "source") == "owner"


def test_effective_access_uses_highest_direct_or_all_share(access_context):
    engine, users = access_context
    reader = users["reader"]
    with engine.connect() as connection:
        assert object_access(connection, reader, "workflow", "wf-a") == "use"
        assert object_access(connection, reader, "workflow", "wf-b") == "edit"
        assert object_access(connection, reader, "macro", "source") == "edit"
        assert object_access(connection, users["editor"], "workflow", "wf-c") is None
        assert object_access(connection, reader, "workflow", "not-present") is None
        assert object_access(connection, None, "workflow", "wf-b") == "owner"
        assert object_access(connection, None, "macro", "not-present") is None


@pytest.mark.parametrize("object_type,object_id", [("workflow", "wf-c"), ("macro", "step")])
def test_deleted_user_loses_ownership_and_admin_retains_access(access_context, object_type, object_id):
    engine, users = access_context
    deleted_owner = users["owner"]
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": deleted_owner.id})
    with engine.connect() as connection:
        assert object_access(connection, deleted_owner, object_type, object_id) is None
        assert object_access(connection, users["admin"], object_type, object_id) == "owner"


def test_object_access_map_batches_ids_in_one_query_and_includes_missing(access_context):
    engine, users = access_context
    with engine.connect() as connection:
        statements = []

        def count_query(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(connection, "before_cursor_execute", count_query)
        try:
            result = object_access_map(connection, users["reader"], "workflow", ["wf-a", "wf-b", "missing"])
        finally:
            event.remove(connection, "before_cursor_execute", count_query)
    assert result == {"wf-a": "use", "wf-b": "edit", "missing": None}
    assert len(statements) == 1


@pytest.mark.parametrize(
    ("object_type", "object_id", "minimum", "user_name", "status", "detail"),
    [
        ("workflow", "missing", "view", "reader", 404, "Workflow nicht gefunden"),
        ("workflow", "wf-a", "owner", "reader", 403, "Keine Berechtigung für diesen Workflow"),
        ("macro", "missing", "view", "reader", 404, "Modul nicht gefunden"),
        ("macro", "step", "view", "reader", 404, "Modul nicht gefunden"),
        ("macro", "source", "owner", "reader", 403, "Keine Berechtigung für dieses Modul"),
    ],
)
def test_require_access_returns_expected_http_errors(access_context, object_type, object_id, minimum, user_name, status, detail):
    engine, users = access_context
    with engine.connect() as connection, pytest.raises(HTTPException) as exc_info:
        require_access(connection, users[user_name], object_type, object_id, minimum)
    assert exc_info.value.status_code == status
    assert exc_info.value.detail == detail


def test_require_access_and_sql_reject_unknown_types_or_levels(access_context):
    engine, users = access_context
    with engine.connect() as connection:
        with pytest.raises(ValueError, match="Unbekannte Mindeststufe: invalid"):
            require_access(connection, users["reader"], "workflow", "wf-a", "invalid")
        with pytest.raises(ValueError, match="Unbekannter Objekttyp: widget"):
            object_access(connection, users["reader"], "widget", "x")
        with pytest.raises(ValueError, match="Unbekannter Objekttyp: widget"):
            access_level_sql(users["reader"], "widget", "item")


@pytest.mark.parametrize(
    ("shares", "message"),
    [
        ([{"user_id": "missing-user", "access": "view"}], "Unbekannter Nutzer: missing-user"),
        ([{"user_id": "emergency", "access": "view"}], "Unbekannter Nutzer: emergency"),
        ([{"user_id": "OWNER_ID", "access": "view"}], "Der Besitzer kann nicht freigegeben werden"),
        ([{"user_id": "reader", "access": "view"}, {"user_id": "reader", "access": "use"}], "Nutzer doppelt: reader"),
        ([{"user_id": None, "access": "view"}, {"user_id": None, "access": "edit"}], "„Alle“ doppelt"),
        ([{"user_id": None, "access": "admin"}], "Ungültige Freigabestufe: admin"),
        ([{"user_id": "OWNER_ID", "access": "invalid"}], "Ungültige Freigabestufe: invalid"),
    ],
)
def test_replace_shares_validation_is_atomic(access_context, shares, message):
    engine, users = access_context
    old_shares = [{"user_id": users["reader"].id, "access": "view"}]
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM object_shares WHERE object_type = 'workflow' AND object_id = 'wf-a'"))
        connection.execute(
            text("INSERT INTO object_shares (object_type, object_id, user_id, access) VALUES ('workflow', 'wf-a', :user_id, 'view')"),
            {"user_id": users["reader"].id},
        )
    actual_shares = [dict(share) for share in shares]
    actual_shares = [
        {**share, "user_id": users["owner"].id if share.get("user_id") == "OWNER_ID" else users["reader"].id if share.get("user_id") == "reader" else share.get("user_id")}
        for share in actual_shares
    ]
    expected_message = message.replace("reader", users["reader"].id)
    with engine.begin() as connection:
        with pytest.raises(ShareValidationError, match=f"^{expected_message}$"):
            replace_shares(connection, "workflow", "wf-a", actual_shares)
    with engine.connect() as connection:
        assert list_shares(connection, "workflow", "wf-a") == [
            {"user_id": users["reader"].id, "email": "reader@access.test", "display_name": "Reader Name", "access": "view"}
        ]


def test_replace_shares_checks_all_duplicates_before_access_levels(access_context):
    engine, users = access_context
    with engine.begin() as connection:
        with pytest.raises(ShareValidationError, match=f"^Nutzer doppelt: {users['reader'].id}$"):
            replace_shares(
                connection,
                "workflow",
                "wf-a",
                [
                    {"user_id": users["reader"].id, "access": "invalid"},
                    {"user_id": users["reader"].id, "access": "view"},
                ],
            )


def test_replace_shares_validates_all_rows_then_replaces_with_all_first(access_context):
    engine, users = access_context
    with engine.begin() as connection:
        replace_shares(
            connection,
            "workflow",
            "wf-a",
            [
                {"user_id": users["editor"].id, "access": "edit"},
                {"user_id": None, "access": "use"},
            ],
        )
    with engine.connect() as connection:
        shares = list_shares(connection, "workflow", "wf-a")
        assert shares == [
            {"user_id": None, "email": "", "display_name": "Alle", "access": "use"},
            {"user_id": users["editor"].id, "email": "editor@access.test", "display_name": "", "access": "edit"},
        ]
        timestamps = connection.execute(
            text("SELECT updated_at FROM object_shares WHERE object_type = 'workflow' AND object_id = 'wf-a'")
        ).scalars().all()
        assert len(timestamps) == 2 and all(timestamp is not None for timestamp in timestamps)


@pytest.mark.parametrize("user_id", ["missing-user", "emergency"])
def test_set_owner_rejects_unknown_and_emergency_users_without_changes(access_context, user_id):
    engine, users = access_context
    with engine.connect() as connection:
        with pytest.raises(ShareValidationError, match=f"^Unbekannter Nutzer: {user_id}$"):
            set_owner(connection, "workflow", "wf-b", user_id)
        assert connection.execute(text("SELECT owner_user_id FROM workflows WHERE id = 'wf-b'")).scalar_one() is None


def test_set_owner_updates_object_and_removes_new_owner_share(access_context):
    engine, users = access_context
    with engine.begin() as connection:
        connection.execute(text("UPDATE workflows SET updated_at = :old WHERE id = 'wf-b'"), {"old": fixed_now})
        set_owner(connection, "workflow", "wf-b", users["reader"].id)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT owner_user_id FROM workflows WHERE id = 'wf-b'")).scalar_one() == users["reader"].id
        assert connection.execute(text("SELECT updated_at FROM workflows WHERE id = 'wf-b'")).scalar_one() > fixed_now
        assert connection.execute(text("SELECT count(*) FROM object_shares WHERE object_type = 'workflow' AND object_id = 'wf-b' AND user_id = :id"), {"id": users["reader"].id}).scalar_one() == 0
        assert object_access(connection, users["reader"], "workflow", "wf-b") == "owner"


def test_set_owner_none_means_admin_only(access_context):
    engine, users = access_context
    with engine.begin() as connection:
        set_owner(connection, "workflow", "wf-a", None)
    with engine.connect() as connection:
        assert object_access(connection, users["owner"], "workflow", "wf-a") == "use"
        assert object_access(connection, users["admin"], "workflow", "wf-a") == "owner"


def test_access_filter_sql_combines_workflow_and_macro_aliases_in_one_query(access_context):
    engine, users = access_context
    workflow_filter, workflow_params = access_filter_sql(users["reader"], "workflow", "wf", "view")
    macro_filter, macro_params = access_filter_sql(users["reader"], "macro", "mc", "view")
    query = text(
        f"SELECT 'workflow' AS kind, wf.id FROM workflows AS wf WHERE {workflow_filter} AND wf.id = 'wf-a' "
        f"UNION ALL SELECT 'macro' AS kind, mc.id FROM macros AS mc WHERE {macro_filter} AND mc.id = 'source'"
    )
    with engine.connect() as connection:
        rows = connection.execute(query, {**workflow_params, **macro_params}).all()
    assert rows == [("workflow", "wf-a"), ("macro", "source")]
    assert set(workflow_params).isdisjoint(macro_params)


def test_access_filter_minimum_levels_and_anonymous_filter(access_context):
    engine, users = access_context
    sql, params = access_filter_sql(users["reader"], "workflow", "wf", "edit")
    with engine.connect() as connection:
        ids = connection.execute(text(f"SELECT wf.id FROM workflows AS wf WHERE {sql}"), params).scalars().all()
    assert ids == ["wf-b"]
    assert access_filter_sql(None, "macro", "mc", "edit") == ("TRUE", {})
    assert access_filter_sql(users["admin"], "macro", "mc") == ("TRUE", {})


def test_owner_records_display_names_and_actor_names(access_context):
    engine, users = access_context
    with engine.connect() as connection:
        assert owner_record(connection, "workflow", "wf-a") == {
            "id": users["owner"].id,
            "email": "owner@access.test",
            "display_name": "Owner Name",
        }
        assert owner_record(connection, "workflow", "wf-b") is None
        assert owner_display(None) == "Admin"
        assert owner_display({"display_name": "Owner Name", "email": "owner@access.test"}) == "Owner Name"
        assert owner_display({"display_name": "", "email": "owner@access.test"}) == "owner@access.test"
        assert actor_name(users["owner"]) == "Owner Name"
        assert actor_name(users["editor"]) == "editor@access.test"
        assert actor_name(_user("emergency", role="admin")) == "Notfallzugang"


def test_log_intervention_only_records_admin_actions_on_another_users_object(access_context):
    engine, users = access_context
    with engine.begin() as connection:
        assert log_intervention(connection, users["admin"], "workflow", "wf-a", "Bearbeitet") is True
        assert log_intervention(connection, users["admin"], "workflow", "wf-b", "Bearbeitet") is False
        assert log_intervention(connection, users["owner"], "workflow", "wf-a", "Bearbeitet") is False
        assert log_intervention(connection, users["admin"], "workflow", "missing", "Bearbeitet") is False
    with engine.connect() as connection:
        rows = list_events(connection, "workflow", "wf-a")
    assert len(rows) == 1
    assert rows[0]["actor"] == "Admin Name"
    assert rows[0]["text"] == "Bearbeitet"


def test_event_listing_orders_by_timestamp_then_id_and_applies_limit(access_context):
    engine, users = access_context
    with engine.begin() as connection:
        for actor, message in ((users["reader"], "first"), (users["admin"], "second"), (users["owner"], "third")):
            log_event(connection, actor, "macro", "source", message)
        connection.execute(
            text("UPDATE object_events SET ts = :same WHERE object_type = 'macro' AND object_id = 'source'"),
            {"same": fixed_now},
        )
    with engine.connect() as connection:
        events = list_events(connection, "macro", "source", limit=2)
        assert [row["text"] for row in events] == ["third", "second"]
        assert [row["actor"] for row in events] == ["Owner Name", "Admin Name"]
        assert list_events(connection, "macro", "absent") == []


def test_event_listing_caps_results_at_200(access_context):
    engine, _users = access_context
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO object_events (object_type, object_id, actor_name, text) "
                "VALUES (:object_type, :object_id, :actor_name, :text)"
            ),
            [
                {"object_type": "workflow", "object_id": "wf-a", "actor_name": "Reader", "text": str(index)}
                for index in range(205)
            ],
        )
    with engine.connect() as connection:
        events = list_events(connection, "workflow", "wf-a", limit=500)
    assert len(events) == 200
    assert events[0]["text"] == "204"
    assert events[-1]["text"] == "5"


def test_delete_object_access_removes_shares_and_locks(access_context):
    engine, users = access_context
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO edit_locks (object_type, object_id, user_id, token_hash, expires_at) VALUES ('workflow', 'wf-a', :user_id, 'hash', now() + interval '1 hour')"),
            {"user_id": users["reader"].id},
        )
        delete_object_access(connection, "workflow", "wf-a")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM object_shares WHERE object_type = 'workflow' AND object_id = 'wf-a'")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM edit_locks WHERE object_type = 'workflow' AND object_id = 'wf-a'")).scalar_one() == 0
