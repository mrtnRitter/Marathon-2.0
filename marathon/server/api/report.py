"""Workflow job-status report endpoint."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from marathon.server.api.deps import get_engine
from marathon.server.db.report_queries import job_status_report


router = APIRouter(tags=["report"])


@router.get("/reports/job-status")
def get_job_status_report(
    start: datetime,
    end: datetime,
    workflow_id: str | None = None,
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Return job-status totals by workflow for the requested time range."""
    if start.utcoffset() is None or end.utcoffset() is None:
        raise HTTPException(status_code=422, detail="Zeitangabe ohne Zeitzone")
    if end <= start:
        raise HTTPException(status_code=422, detail="Ende muss nach dem Start liegen")
    with engine.connect() as connection:
        report = job_status_report(connection, start, end, workflow_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    return report
