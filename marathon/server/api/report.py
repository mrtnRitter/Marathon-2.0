"""Workflow job-status report endpoint."""

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from marathon.server.api.deps import get_engine
from marathon.server.db.report_queries import job_status_report


router = APIRouter(tags=["report"])


def _load_report(
    start: datetime,
    end: datetime,
    workflow_id: str | None,
    engine: Engine,
) -> dict[str, Any]:
    if start.utcoffset() is None or end.utcoffset() is None:
        raise HTTPException(status_code=422, detail="Zeitangabe ohne Zeitzone")
    if end <= start:
        raise HTTPException(status_code=422, detail="Ende muss nach dem Start liegen")
    with engine.connect() as connection:
        report = job_status_report(connection, start, end, workflow_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    return report


def job_status_csv(report: dict[str, Any]) -> str:
    """Serialize a job-status report as a spreadsheet-safe UTF-8 CSV string."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow([
        "Workflow", "Gesamt", "offen", "läuft", "erfolgreich", "fehlgeschlagen", "abgebrochen", "% erfolgreich"
    ])

    def success_percent(total: int, succeeded: int) -> str:
        return f"{succeeded / total * 100:.1f}".replace(".", ",") if total else ""

    def counts_row(label: str, total: int, counts: dict[str, int]) -> list[Any]:
        return [
            label,
            total,
            counts["open"],
            counts["assigned"],
            counts["succeeded"],
            counts["failed"],
            counts["cancelled"],
            success_percent(total, counts["succeeded"]),
        ]

    for row in report["rows"]:
        workflow_name = row["workflow_name"]
        if workflow_name.startswith(("=", "+", "-", "@", "\t", "\r")):
            workflow_name = f"'{workflow_name}"
        writer.writerow(counts_row(workflow_name, row["total"], row["counts"]))

    totals = report["totals"]
    writer.writerow(counts_row("Summe", totals["total"], totals["counts"]))
    return f"\ufeff{buffer.getvalue()}"


@router.get("/reports/job-status")
def get_job_status_report(
    start: datetime,
    end: datetime,
    workflow_id: str | None = None,
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Return job-status totals by workflow for the requested time range."""
    return _load_report(start, end, workflow_id, engine)


@router.get("/reports/job-status.csv")
def download_job_status_csv(
    start: datetime,
    end: datetime,
    workflow_id: str | None = None,
    engine: Engine = Depends(get_engine),
) -> Response:
    """Download the requested report as a CSV attachment."""
    report = _load_report(start, end, workflow_id, engine)
    return Response(
        content=job_status_csv(report),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment"},
    )
