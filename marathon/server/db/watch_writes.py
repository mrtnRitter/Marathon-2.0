"""Transactional database operations for watchfolder scans."""

# IMPORTS
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from marathon.modules.watchfolder.rules import read_settings
from marathon.modules.watchfolder.scan import FileStat, KnownFile, plan_scan

# CONFIG
watch_type_id = "watchfolder"
invalid_retry_s = 60.0

# FUNC
@dataclass(frozen=True)
class WatchClaim:
    """Claimed watchfolder module scheduled for one scan."""

    workflow_id: str
    module_id: str
    module_name: str
    settings: dict[str, Any]
    next_scan_at: datetime


def sync_watch_modules(connection: Connection, now: datetime) -> None:
    """Create state for active current watch modules and remove stale state."""
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(
        text(
            "INSERT INTO watch_modules (workflow_id, module_id, next_scan_at) "
            "SELECT workflow.id, module.id, :now "
            "FROM workflows AS workflow "
            "JOIN workflow_modules AS module "
            "ON module.workflow_id = workflow.id AND module.version = workflow.current_version "
            "WHERE workflow.active AND module.type_id = :watch_type_id "
            "ON CONFLICT (workflow_id, module_id) DO NOTHING"
        ),
        {"now": now, "watch_type_id": watch_type_id},
    )
    connection.execute(
        text(
            "DELETE FROM watch_modules AS watched "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM workflows AS workflow "
            "JOIN workflow_modules AS module "
            "ON module.workflow_id = workflow.id AND module.version = workflow.current_version "
            "WHERE workflow.id = watched.workflow_id AND module.id = watched.module_id "
            "AND module.type_id = :watch_type_id)"
        ),
        {"watch_type_id": watch_type_id},
    )


def record_watch_error(
    connection: Connection,
    workflow_id: str,
    module_id: str,
    message: str,
    level: str = "warn",
) -> bool:
    """Store a changed watch error and append one corresponding log line."""
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    changed = connection.execute(
        text(
            "UPDATE watch_modules SET last_error = :message "
            "WHERE workflow_id = :workflow_id AND module_id = :module_id "
            "AND last_error IS DISTINCT FROM :message RETURNING 1"
        ),
        {"workflow_id": workflow_id, "module_id": module_id, "message": message},
    ).scalar_one_or_none()
    if changed is None:
        return False
    connection.execute(
        text(
            "INSERT INTO log_lines (workflow_id, module_id, job_id, level, text) "
            "VALUES (:workflow_id, :module_id, NULL, :level, :text)"
        ),
        {"workflow_id": workflow_id, "module_id": module_id, "level": level, "text": message},
    )
    return True


def claim_due_modules(connection: Connection, now: datetime) -> list[WatchClaim]:
    """Lock and schedule due active watch modules, returning valid scan claims."""
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '10s'"))
    rows = connection.execute(
        text(
            "SELECT watched.workflow_id, watched.module_id, module.name, module.settings "
            "FROM watch_modules AS watched "
            "JOIN workflows AS workflow ON workflow.id = watched.workflow_id "
            "JOIN workflow_modules AS module "
            "ON module.workflow_id = workflow.id AND module.version = workflow.current_version "
            "AND module.id = watched.module_id "
            "WHERE watched.next_scan_at <= :now AND workflow.active AND module.type_id = :watch_type_id "
            "ORDER BY watched.workflow_id, watched.module_id "
            "FOR UPDATE OF watched SKIP LOCKED"
        ),
        {"now": now, "watch_type_id": watch_type_id},
    ).mappings().all()

    claims = []
    for row in rows:
        try:
            settings = read_settings(row["settings"])
        except ValueError as error:
            next_scan_at = now + timedelta(seconds=invalid_retry_s)
            connection.execute(
                text(
                    "UPDATE watch_modules SET next_scan_at = :next_scan_at "
                    "WHERE workflow_id = :workflow_id AND module_id = :module_id"
                ),
                {
                    "workflow_id": row["workflow_id"],
                    "module_id": row["module_id"],
                    "next_scan_at": next_scan_at,
                },
            )
            record_watch_error(
                connection,
                row["workflow_id"],
                row["module_id"],
                str(error),
                "error",
            )
            continue

        next_scan_at = now + timedelta(seconds=settings.wait_s)
        connection.execute(
            text(
                "UPDATE watch_modules SET next_scan_at = :next_scan_at "
                "WHERE workflow_id = :workflow_id AND module_id = :module_id"
            ),
            {
                "workflow_id": row["workflow_id"],
                "module_id": row["module_id"],
                "next_scan_at": next_scan_at,
            },
        )
        claims.append(
            WatchClaim(
                row["workflow_id"],
                row["module_id"],
                row["name"],
                row["settings"],
                next_scan_at,
            )
        )
    return claims


def apply_scan(
    connection: Connection,
    claim: WatchClaim,
    listing: Mapping[str, FileStat],
    scanned_at: datetime,
) -> list[str] | None:
    """Apply a current scan claim and return IDs of assets created by it."""
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    watched = connection.execute(
        text(
            "SELECT next_scan_at, last_error FROM watch_modules "
            "WHERE workflow_id = :workflow_id AND module_id = :module_id FOR UPDATE"
        ),
        {"workflow_id": claim.workflow_id, "module_id": claim.module_id},
    ).mappings().one_or_none()
    if watched is None or watched["next_scan_at"] != claim.next_scan_at:
        return None

    current_module = connection.execute(
        text(
            "SELECT workflow.active, module.type_id, module.settings "
            "FROM workflows AS workflow "
            "JOIN workflow_modules AS module "
            "ON module.workflow_id = workflow.id AND module.version = workflow.current_version "
            "WHERE workflow.id = :workflow_id AND module.id = :module_id "
            "FOR SHARE OF workflow"
        ),
        {"workflow_id": claim.workflow_id, "module_id": claim.module_id},
    ).mappings().one_or_none()
    if (
        current_module is None
        or not current_module["active"]
        or current_module["type_id"] != watch_type_id
        or current_module["settings"] != claim.settings
    ):
        return None

    settings = read_settings(claim.settings)
    known_rows = connection.execute(
        text(
            "SELECT path, size, mtime_ns, stable_since, asset_id, missing_since FROM watch_files "
            "WHERE workflow_id = :workflow_id AND module_id = :module_id"
        ),
        {"workflow_id": claim.workflow_id, "module_id": claim.module_id},
    ).mappings().all()
    known = {
        row["path"]: KnownFile(
            row["size"],
            row["mtime_ns"],
            row["stable_since"],
            row["asset_id"],
            row["missing_since"],
        )
        for row in known_rows
    }
    plan = plan_scan(known, listing, scanned_at, settings.wait_s)

    for path in plan.deletes:
        connection.execute(
            text(
                "DELETE FROM watch_files WHERE workflow_id = :workflow_id "
                "AND module_id = :module_id AND path = :path"
            ),
            {"workflow_id": claim.workflow_id, "module_id": claim.module_id, "path": path},
        )
    upsert = text(
        "INSERT INTO watch_files "
        "(workflow_id, module_id, path, size, mtime_ns, stable_since, asset_id, missing_since) "
        "VALUES (:workflow_id, :module_id, :path, :size, :mtime_ns, :stable_since, :asset_id, :missing_since) "
        "ON CONFLICT (workflow_id, module_id, path) DO UPDATE SET "
        "size = EXCLUDED.size, mtime_ns = EXCLUDED.mtime_ns, stable_since = EXCLUDED.stable_since, "
        "asset_id = EXCLUDED.asset_id, missing_since = EXCLUDED.missing_since"
    )
    for path, file in plan.upserts.items():
        connection.execute(
            upsert,
            {
                "workflow_id": claim.workflow_id,
                "module_id": claim.module_id,
                "path": path,
                "size": file.size,
                "mtime_ns": file.mtime_ns,
                "stable_since": file.stable_since,
                "asset_id": file.asset_id,
                "missing_since": file.missing_since,
            },
        )

    asset_insert = text(
        "INSERT INTO assets "
        "(id, workflow_id, name, status, current_module_id, step_status, list_position, metadata) "
        "VALUES (:id, :workflow_id, :name, 'pending', :module_id, NULL, "
        "(SELECT COALESCE(MAX(list_position), 0) + 1 FROM assets WHERE workflow_id = :workflow_id), :metadata)"
    ).bindparams(bindparam("metadata", type_=JSONB()))
    asset_ids = []
    for path in plan.ready:
        file = listing[path]
        name = os.path.basename(path)
        asset_id = f"as-{uuid4().hex[:12]}"
        metadata = {
            "source_path": path,
            "size": str(file.size),
            "mtime": datetime.fromtimestamp(file.mtime_ns / 1_000_000_000, timezone.utc).isoformat(),
        }
        connection.execute(
            asset_insert,
            {
                "id": asset_id,
                "workflow_id": claim.workflow_id,
                "name": name,
                "module_id": claim.module_id,
                "metadata": metadata,
            },
        )
        connection.execute(
            text("INSERT INTO asset_history (asset_id, kind, text) VALUES (:asset_id, 'info', :text)"),
            {
                "asset_id": asset_id,
                "text": f"Vom Watchfolder „{claim.module_name}“ übernommen: {path}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO log_lines (workflow_id, module_id, job_id, level, text) "
                "VALUES (:workflow_id, :module_id, NULL, 'info', :text)"
            ),
            {
                "workflow_id": claim.workflow_id,
                "module_id": claim.module_id,
                "text": f"Neue Datei: {name}",
            },
        )
        updated_path = connection.execute(
            text(
                "UPDATE watch_files SET asset_id = :asset_id "
                "WHERE workflow_id = :workflow_id AND module_id = :module_id AND path = :path "
                "AND asset_id IS NULL RETURNING path"
            ),
            {
                "asset_id": asset_id,
                "workflow_id": claim.workflow_id,
                "module_id": claim.module_id,
                "path": path,
            },
        ).scalar_one_or_none()
        if updated_path != path:
            raise RuntimeError(f"Watchfolder-Datei wurde bereits übernommen: {path}")
        asset_ids.append(asset_id)

    if watched["last_error"] is not None:
        connection.execute(
            text(
                "UPDATE watch_modules SET last_error = NULL "
                "WHERE workflow_id = :workflow_id AND module_id = :module_id"
            ),
            {"workflow_id": claim.workflow_id, "module_id": claim.module_id},
        )
        connection.execute(
            text(
                "INSERT INTO log_lines (workflow_id, module_id, job_id, level, text) "
                "VALUES (:workflow_id, :module_id, NULL, 'info', :text)"
            ),
            {
                "workflow_id": claim.workflow_id,
                "module_id": claim.module_id,
                "text": f"Ordner wieder erreichbar: {settings.path}",
            },
        )
    return asset_ids
