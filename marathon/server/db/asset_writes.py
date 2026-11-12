"""Transactional write operations for assets."""

# IMPORTS
from uuid import uuid4

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.engine import Connection


# EXCEPTIONS
class AssetValidationError(ValueError):
    """Raised when an asset reprocessing request fails validation."""


class AssetNotFound(LookupError):
    """Raised when a requested asset or workflow does not exist."""


class AssetConflict(Exception):
    """Raised when an asset cannot be reprocessed in its current state."""


# ASSET WRITES
def reprocess_assets(
    connection: Connection,
    asset_ids: list[str],
    target_workflow_id: str | None,
) -> list[str]:
    """Reset assets or transfer them to another workflow in input order."""
    unique_ids = list(dict.fromkeys(asset_ids))
    if not unique_ids:
        raise AssetValidationError("Keine Assets gewählt")
    if len(unique_ids) > 1000:
        raise AssetValidationError("Zu viele Assets (max. 1000)")

    connection.execute(text("SET LOCAL lock_timeout = '10s'"))
    target_workflow = None
    if target_workflow_id is not None:
        target_workflow = connection.execute(
            text("SELECT id, name FROM workflows WHERE id = :id FOR SHARE"),
            {"id": target_workflow_id},
        ).mappings().one_or_none()
        if target_workflow is None:
            raise AssetNotFound("Ziel-Workflow nicht gefunden")

    rows = connection.execute(
        text(
            "SELECT a.id, a.workflow_id, a.name, a.status, a.metadata, w.name AS workflow_name "
            "FROM assets a JOIN workflows w ON w.id = a.workflow_id "
            "WHERE a.id = ANY(:ids) ORDER BY a.id FOR UPDATE OF a"
        ).bindparams(bindparam("ids", type_=ARRAY(Text()))),
        {"ids": unique_ids},
    ).mappings().all()
    assets_by_id = {row["id"]: row for row in rows}
    for asset_id in unique_ids:
        if asset_id not in assets_by_id:
            raise AssetNotFound(f"Asset nicht gefunden: {asset_id}")
    for asset_id in unique_ids:
        asset = assets_by_id[asset_id]
        if asset["status"] == "in_progress":
            raise AssetConflict(f"Asset „{asset['name']}“ wird gerade verarbeitet")

    result_ids = []
    for asset_id in unique_ids:
        asset = assets_by_id[asset_id]
        if target_workflow is None or target_workflow_id == asset["workflow_id"]:
            connection.execute(
                text(
                    "UPDATE assets SET status = 'pending', current_module_id = NULL, step_status = NULL, "
                    "updated_at = now() WHERE id = :id"
                ),
                {"id": asset_id},
            )
            connection.execute(
                text(
                    "INSERT INTO asset_history (asset_id, kind, text) "
                    "VALUES (:id, 'info', 'Erneut verarbeiten: beginnt den Workflow von vorn')"
                ),
                {"id": asset_id},
            )
            result_ids.append(asset_id)
            continue

        new_id = f"as-{uuid4().hex[:12]}"
        connection.execute(
            text(
                "INSERT INTO assets (id, workflow_id, name, status, current_module_id, step_status, "
                "list_position, metadata) VALUES (:id, :workflow_id, :name, 'pending', NULL, NULL, "
                "(SELECT COALESCE(MAX(list_position), 0) + 1 FROM assets WHERE workflow_id = :workflow_id), "
                ":metadata)"
            ).bindparams(bindparam("metadata", type_=JSONB())),
            {
                "id": new_id,
                "workflow_id": target_workflow_id,
                "name": asset["name"],
                "metadata": asset["metadata"],
            },
        )
        connection.execute(
            text("UPDATE assets SET status = 'done', step_status = NULL, updated_at = now() WHERE id = :id"),
            {"id": asset_id},
        )
        connection.execute(
            text(
                "INSERT INTO asset_history (asset_id, kind, text) VALUES (:id, 'info', :text)"
            ),
            {
                "id": asset_id,
                "text": f"Workflow gewechselt nach „{target_workflow['name']}“ (neues Asset {new_id})",
            },
        )
        connection.execute(
            text(
                "INSERT INTO asset_history (asset_id, kind, text) VALUES (:id, 'info', :text)"
            ),
            {
                "id": new_id,
                "text": f"Kommt aus Workflow „{asset['workflow_name']}“ (Asset {asset_id})",
            },
        )
        result_ids.append(new_id)

    return result_ids
