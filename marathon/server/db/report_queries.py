"""Read-only SQL queries for workflow job status reports."""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


_job_statuses = ("open", "assigned", "succeeded", "failed", "cancelled")


def job_status_report(
    connection: Connection,
    start: datetime,
    end: datetime,
    workflow_id: str | None = None,
) -> dict[str, Any] | None:
    """Return current job-status counts by workflow for a half-open time range."""
    counts_by_workflow: dict[str, dict[str, Any]] = {}
    parameters: dict[str, Any] = {"start": start, "end": end}
    workflow_filter = ""
    if workflow_id is not None:
        workflow_filter = "WHERE workflow.id = :workflow_id"
        parameters["workflow_id"] = workflow_id
    rows = connection.execute(
        text(
            "SELECT workflow.id AS workflow_id, workflow.name AS workflow_name, "
            "job.status AS job_status, COUNT(job.id) AS job_count "
            "FROM workflows AS workflow "
            "LEFT JOIN jobs AS job ON job.workflow_id = workflow.id "
            "AND job.created_at >= :start AND job.created_at < :end "
            f"{workflow_filter} "
            "GROUP BY workflow.id, workflow.name, job.status "
            "ORDER BY lower(workflow.name), workflow.id"
        ),
        parameters,
    ).mappings()
    for row in rows:
        workflow = counts_by_workflow.setdefault(
            row["workflow_id"],
            {
                "workflow_id": row["workflow_id"],
                "workflow_name": row["workflow_name"],
                "counts": {status: 0 for status in _job_statuses},
            },
        )
        if row["job_status"] in workflow["counts"]:
            workflow["counts"][row["job_status"]] = row["job_count"]

    if workflow_id is not None and not counts_by_workflow:
        return None
    report_rows = [
        {**workflow, "total": sum(workflow["counts"].values())}
        for workflow in counts_by_workflow.values()
    ]
    totals_counts = {
        status: sum(row["counts"][status] for row in report_rows)
        for status in _job_statuses
    }
    return {
        "start": start,
        "end": end,
        "rows": report_rows,
        "totals": {"counts": totals_counts, "total": sum(totals_counts.values())},
    }
