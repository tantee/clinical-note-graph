from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db.postgres import db_session
from app.services.jobs import get_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    status: str | None = None,
    type: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {"lim": limit, "off": offset}
    if status:
        # Comma-separated list of statuses so the jobs popover can fetch
        # `?status=pending,running` in one round-trip. Single-value form
        # (`?status=pending`) still works — it parses to a one-element list.
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if len(statuses) == 1:
            where.append("status = :st")
            params["st"] = statuses[0]
        else:
            where.append("status = ANY(:sts)")
            params["sts"] = statuses
    if type:
        where.append("type = :tp")
        params["tp"] = type
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with db_session() as s:
        rows = s.execute(
            text(
                "SELECT job_id::text AS job_id, type, status, patient_id, document_id, attempts, "
                "started_at, finished_at, created_at, progress FROM jobs "
                + clause + " ORDER BY created_at DESC LIMIT :lim OFFSET :off"
            ),
            params,
        ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/{job_id}")
def get(job_id: str):
    j = get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Job not found")
    return j


@router.post("/{job_id}/requeue")
def requeue(job_id: str) -> dict[str, Any]:
    with db_session() as s:
        s.execute(text(
            "UPDATE jobs SET status='pending', attempts=0, error=NULL, "
            "next_run_at=now(), locked_by=NULL, locked_until=NULL "
            "WHERE job_id=CAST(:j AS uuid)"
        ), {"j": job_id})
    return {"requeued": job_id}
