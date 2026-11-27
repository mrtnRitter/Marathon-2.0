"""Read queries honor unified workflow access shares."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from auth_helpers import create_test_user
from marathon.server.db.queries import asset_totals, get_workflow, list_assets, list_workflows
from marathon.server.db.report_queries import job_status_report
from records import insert_records


fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.fixture
def read_access_context(db_engine):
    """Create workflows with user, global, and owner access cases."""
    insert_records(db_engine, fixed_now)
    with db_engine.begin() as connection:
        connection.execute(text("INSERT INTO workflows (id, name) VALUES ('wf-d', 'Private')"))
        users = {
            name: create_test_user(connection, f"{name}@read-access.test", role)
            for name, role in (
                ("owner", "user"),
                ("reader", "user"),
                ("viewer", "user"),
                ("unshared", "user"),
                ("admin", "admin"),
            )
        }
        connection.execute(
            text("UPDATE workflows SET owner_user_id = :owner_id WHERE id = 'wf-a'"),
            {"owner_id": users["owner"].id},
        )
        connection.execute(
            text(
                "INSERT INTO object_shares (object_type, object_id, user_id, access) "
                "VALUES ('workflow', :object_id, :user_id, :access)"
            ),
            [
                {"object_id": "wf-b", "user_id": users["reader"].id, "access": "edit"},
                {"object_id": "wf-b", "user_id": users["viewer"].id, "access": "view"},
                {"object_id": "wf-c", "user_id": None, "access": "use"},
            ],
        )
    return users


def _assert_visible_workflows(connection, user, expected_access):
    """Check workflow, asset, total, and report reads against one visibility set."""
    expected_ids = set(expected_access)
    workflows = list_workflows(connection, user)
    assert {workflow["id"] for workflow in workflows} == expected_ids
    assert {workflow["id"]: workflow["access"] for workflow in workflows} == expected_access

    for workflow_id in ("wf-a", "wf-b", "wf-c", "wf-d"):
        workflow = get_workflow(connection, workflow_id, user)
        assert (workflow is not None) == (workflow_id in expected_ids)
        if workflow is not None:
            assert workflow["access"] == expected_access[workflow_id]
    assets = list_assets(connection, user=user)
    assert {asset["workflow_id"] for asset in assets} <= expected_ids
    totals = asset_totals(connection, user)
    assert sum(totals.values()) == len(assets)

    report = job_status_report(
        connection,
        datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime(2025, 1, 3, tzinfo=timezone.utc),
        user=user,
    )
    assert {row["workflow_id"] for row in report["rows"]} == expected_ids
    assert {row["workflow_id"] for row in report["rows"]} <= expected_ids


def test_read_queries_use_unified_workflow_access(read_access_context, db_engine):
    users = read_access_context
    with db_engine.connect() as connection:
        _assert_visible_workflows(connection, users["reader"], {"wf-b": "edit", "wf-c": "use"})
        _assert_visible_workflows(connection, users["viewer"], {"wf-b": "view", "wf-c": "use"})
        _assert_visible_workflows(connection, users["owner"], {"wf-a": "owner", "wf-c": "use"})
        _assert_visible_workflows(connection, users["unshared"], {"wf-c": "use"})
        _assert_visible_workflows(
            connection,
            users["admin"],
            {"wf-a": "owner", "wf-b": "owner", "wf-c": "owner", "wf-d": "owner"},
        )
        _assert_visible_workflows(
            connection,
            None,
            {"wf-a": "owner", "wf-b": "owner", "wf-c": "owner", "wf-d": "owner"},
        )
        assert get_workflow(connection, "wf-a", users["reader"]) is None
        owner_workflow = get_workflow(connection, "wf-a", users["owner"])
        assert owner_workflow["owner"] == {
            "id": users["owner"].id,
            "email": users["owner"].email,
            "display_name": users["owner"].display_name,
        }
        assert get_workflow(connection, "wf-c", users["viewer"])["owner"] is None
