"""Access-aware workflow and asset detail tests."""

# IMPORTS
from copy import deepcopy
from datetime import datetime, timezone
import json

from sqlalchemy import text

from auth_helpers import create_test_user
from marathon.server.db.detail_queries import get_asset_detail, get_graph, get_module_activity
from marathon.server.db.macro_writes import create_macro
from marathon.server.db.queries import list_assets
from records import insert_records


# CONFIG
fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
blind_name = "Nicht freigegebenes Modul"
blind_message = "Meldung eines nicht freigegebenen Moduls"


# FUNC
def _setup(db_engine):
    """Create a private test macro and users with workflow visibility."""
    insert_records(db_engine, fixed_now)
    with db_engine.begin() as connection:
        admin = create_test_user(connection, "detail-admin@test.local", role="admin")
        viewer = create_test_user(connection, "detail-viewer@test.local")
        inaccessible = create_test_user(connection, "detail-hidden@test.local")
        definition = deepcopy(connection.execute(text(
            "SELECT definition FROM macro_versions WHERE macro_id = 'step' AND version = 1"
        )).scalar_one())
        definition.update(name="Private Internal Name", icon="private-icon", description="Private description")
        definition["outputs"][0]["label"] = "Private success label"
        definition["outputs"][1]["label"] = "Private failure label"
        macro_id = create_macro(connection, definition, admin)
        connection.execute(text(
            "UPDATE workflow_instances SET macro_id = :macro_id, macro_version = 1 "
            "WHERE workflow_id = 'wf-a' AND version = 2 AND id = 'm2'"
        ), {"macro_id": macro_id})
        connection.execute(text(
            "INSERT INTO object_shares (object_type, object_id, user_id, access) "
            "VALUES ('workflow', 'wf-a', :user_id, 'view')"
        ), {"user_id": viewer.id})
    return admin, viewer, inaccessible, macro_id


def _set_macro_share(connection, macro_id: str, user_id: str | None, access: str | None) -> None:
    connection.execute(text(
        "DELETE FROM object_shares WHERE object_type = 'macro' AND object_id = :macro_id"
    ), {"macro_id": macro_id})
    if access is not None:
        connection.execute(text(
            "INSERT INTO object_shares (object_type, object_id, user_id, access) "
            "VALUES ('macro', :macro_id, :user_id, :access)"
        ), {"macro_id": macro_id, "user_id": user_id, "access": access})


def _insert_instance(connection, macro_id: str, instance_id: str, runtime_id: str, version: int = 2) -> None:
    """Insert a one-block workflow instance and its runtime module."""
    instance_name = f"Visible {instance_id}"
    connection.execute(text(
        "INSERT INTO workflow_instances "
        "(workflow_id, version, id, macro_id, macro_version, name, description, position_x, position_y, settings, locked) "
        "VALUES ('wf-a', :version, :instance_id, :macro_id, 1, :name, 'instance description', 600, 0, '{}'::jsonb, false)"
    ), {"version": version, "instance_id": instance_id, "macro_id": macro_id, "name": instance_name})
    connection.execute(text(
        "INSERT INTO workflow_modules "
        "(workflow_id, version, id, type_id, name, description, position_x, position_y, settings, locked, instance_id, block_id, exits) "
        "VALUES ('wf-a', :version, :runtime_id, 'step', :name, '', 600, 0, '{}'::jsonb, false, :instance_id, 'main', CAST(:exits AS jsonb))"
    ), {
        "version": version, "runtime_id": runtime_id, "name": instance_name,
        "instance_id": instance_id, "exits": json.dumps({"success": "success", "failure": "failure"}),
    })


def _insert_job(connection, asset_id: str, runtime_id: str, job_id: str, output_id: str | None) -> None:
    connection.execute(text(
        "INSERT INTO jobs (id, workflow_id, module_id, status, worker_id, attempt, output_id, created_at, started_at, finished_at) "
        "VALUES (:job_id, 'wf-a', :runtime_id, 'succeeded', NULL, 1, :output_id, :now, :now, :now)"
    ), {"job_id": job_id, "runtime_id": runtime_id, "output_id": output_id, "now": fixed_now})
    connection.execute(text(
        "INSERT INTO job_assets (job_id, asset_id, outcome, output_id) "
        "VALUES (:job_id, :asset_id, 'result', :output_id)"
    ), {"job_id": job_id, "asset_id": asset_id, "output_id": output_id})


# TESTS
def test_graph_shows_access_levels_and_exact_blind_module_mask(db_engine):
    admin, viewer, _, macro_id = _setup(db_engine)
    with db_engine.begin() as connection:
        def module_for(user):
            return next(module for module in get_graph(connection, "wf-a", user)["modules"] if module["id"] == "m2")

        owner_module = module_for(admin)
        internal_module = module_for(None)
        blind_module = module_for(viewer)
        assert owner_module["access"] == "owner" and owner_module["blind"] is False
        assert internal_module["access"] == "owner" and internal_module["blind"] is False
        assert owner_module["type_id"] == macro_id and owner_module["name"] == "Prüfen"
        assert owner_module["hull"]["name"] == "Private Internal Name"
        assert "can_edit" not in owner_module
        outputs = [
            {"id": output["id"], "label": output["label"], "description": "",
             **({"data": True} if output.get("data") is True else {})}
            for output in owner_module["hull"]["outputs"]
        ]
        expected_blind = deepcopy(owner_module)
        expected_blind.update(
            type_id="", name="", description="", settings={}, access=None, blind=True,
            hull={
                "id": "", "version": owner_module["macro_version"], "name": "", "category": "",
                "icon": "", "description": "", "sort_order": 0,
                "has_input": owner_module["hull"]["has_input"], "outputs": outputs, "settings": [],
            },
        )
        expected_blind.pop("can_edit", None)
        assert blind_module == expected_blind
        assert "can_edit" not in blind_module

        _set_macro_share(connection, macro_id, viewer.id, "view")
        view_module = module_for(viewer)
        expected_view = deepcopy(owner_module)
        expected_view.update(access="view", blind=False)
        assert view_module == expected_view
        _set_macro_share(connection, macro_id, None, "use")
        use_module = module_for(viewer)
        expected_use = deepcopy(owner_module)
        expected_use.update(access="use", blind=False)
        assert use_module == expected_use
        assert "can_edit" not in view_module and "can_edit" not in use_module


def test_activity_hides_blind_logs_and_masks_job_ids_below_edit(db_engine):
    _, viewer, _, macro_id = _setup(db_engine)
    with db_engine.begin() as connection:
        _insert_instance(connection, macro_id, "activity-instance", "activity-instance.main")
        connection.execute(text(
            "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, list_position, metadata, created_at, updated_at) "
            "VALUES ('activity-asset', 'wf-a', 'activity asset', 'in_progress', 'activity-instance.main', 'running', 50, '{}'::jsonb, :now, :now)"
        ), {"now": fixed_now})
        _insert_job(connection, "activity-asset", "activity-instance.main", "activity-job", None)
        connection.execute(text(
            "INSERT INTO log_lines (workflow_id, module_id, job_id, asset_id, ts, level, text) "
            "VALUES ('wf-a', 'activity-instance.main', 'activity-job', 'activity-asset', :now, 'warn', 'private activity log'), "
            "('wf-a', 'activity-instance', NULL, NULL, :now, 'warn', 'instance activity log')"
        ), {"now": fixed_now})
        blind_activity = get_module_activity(connection, "wf-a", "activity-instance", viewer)
        assert blind_activity["logs"] == [] and blind_activity["logs_hidden"] is True
        _set_macro_share(connection, macro_id, viewer.id, "view")
        view_activity = get_module_activity(connection, "wf-a", "activity-instance", viewer)
        assert view_activity["logs_hidden"] is False
        assert {log["text"] for log in view_activity["logs"]} == {"private activity log", "instance activity log"}
        assert view_activity["jobs"][0]["module_id"] == "activity-instance"
        _set_macro_share(connection, macro_id, viewer.id, "edit")
        edit_activity = get_module_activity(connection, "wf-a", "activity-instance", viewer)
        assert edit_activity["jobs"][0]["module_id"] == "activity-instance.main"
        assert edit_activity["logs_hidden"] is False


def test_asset_detail_and_list_assets_mask_blind_runtime_modules(db_engine):
    _, viewer, inaccessible, macro_id = _setup(db_engine)
    with db_engine.begin() as connection:
        _insert_instance(connection, macro_id, "detail-instance", "detail-instance.main")
        _insert_instance(connection, macro_id, "old-instance", "old-instance.main", version=1)
        _insert_instance(connection, macro_id, "shared-old", "shared-runtime.main", version=1)
        _insert_instance(connection, "step", "shared-current", "shared-runtime.main", version=2)
        _set_macro_share(connection, "step", viewer.id, "use")
        connection.execute(text(
            "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, list_position, metadata, created_at, updated_at) "
            "VALUES ('detail-asset', 'wf-a', 'detail asset', 'done', 'old-instance.main', NULL, 60, '{}'::jsonb, :now, :now), "
            "('list-asset', 'wf-a', 'list asset', 'in_progress', 'detail-instance.main', 'running', 61, '{}'::jsonb, :now, :now)"
        ), {"now": fixed_now})
        _insert_job(connection, "detail-asset", "detail-instance.main", "detail-job-success", "success")
        _insert_job(connection, "detail-asset", "detail-instance.main", "detail-job-unknown", "not-in-exits")
        _insert_job(connection, "detail-asset", "old-instance.main", "old-job", "success")
        _insert_job(connection, "detail-asset", "shared-runtime.main", "shared-job", "success")
        connection.execute(text(
            "INSERT INTO log_lines (workflow_id, module_id, job_id, asset_id, ts, level, text) "
            "VALUES ('wf-a', 'detail-instance.main', 'detail-job-success', 'detail-asset', :now, 'error', 'private log'), "
            "('wf-a', 'old-instance.main', 'old-job', 'detail-asset', :now, 'warn', 'old private log'), "
            "('wf-a', 'shared-runtime.main', 'shared-job', 'detail-asset', :now, 'warn', 'shared private log')"
        ), {"now": fixed_now})
        connection.execute(text(
            "INSERT INTO asset_history (asset_id, ts, kind, text, module_id) "
            "VALUES ('detail-asset', :now, 'error', 'private message', 'old-instance.main'), "
            "('detail-asset', :now, 'info', 'ordinary message', NULL), "
            "('detail-asset', :now, 'info', 'shared runtime message', 'shared-runtime.main')"
        ), {"now": fixed_now})

        blind_detail = get_asset_detail(connection, "detail-asset", user=viewer)
        assert blind_detail["current_module"] == blind_name
        assert all(item["module_name"] == blind_name for item in blind_detail["timeline"])
        shared_entry = next(item for item in blind_detail["timeline"] if item["module_id"] == "shared-runtime.main")
        assert shared_entry["output_label"] == "Erfolg"
        assert blind_detail["log"] == []
        assert {item["text"] for item in blind_detail["messages"]} == {blind_message, "ordinary message"}
        assert all(set(item) == {"ts", "kind", "text"} for item in blind_detail["messages"])
        blind_list = next(item for item in list_assets(connection, workflow_id="wf-a", user=viewer)
                          if item["id"] == "list-asset")
        assert blind_list["current_module"] == blind_name
        assert get_asset_detail(connection, "detail-asset", user=inaccessible) is None

        _set_macro_share(connection, macro_id, viewer.id, "view")
        view_detail = get_asset_detail(connection, "detail-asset", user=viewer)
        assert view_detail["log"]
        assert {item["output_label"] for item in view_detail["timeline"]} == {
            "Private success label", "Erfolg", None,
        }
        view_list = next(item for item in list_assets(connection, workflow_id="wf-a", user=viewer)
                         if item["id"] == "list-asset")
        assert view_list["current_module"] == "Visible detail-instance"

        _set_macro_share(connection, macro_id, viewer.id, "edit")
        edit_detail = get_asset_detail(connection, "detail-asset", user=viewer)
        assert {item["text"] for item in edit_detail["log"]} == {
            "private log", "old private log", "shared private log",
        }
        assert "success" in {item["output_label"] for item in edit_detail["timeline"]}
        assert "not-in-exits" in {item["output_label"] for item in edit_detail["timeline"]}
