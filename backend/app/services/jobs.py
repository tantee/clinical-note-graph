from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text

from app.db.helpers import j
from app.db.postgres import db_session
from app.schemas.emr import EMRIngestRequest
from app.services.ingest import run_ingest, run_ingest_pipeline
from app.services.queue import register_handler

logger = logging.getLogger(__name__)


async def _emr_ingest_handler(job: dict, on_progress) -> dict:
    payload = job["payload"]
    req = EMRIngestRequest.model_validate(payload)
    return await run_ingest_pipeline(req, job_id=str(job["job_id"]), on_progress=on_progress)


register_handler("emr_ingest", _emr_ingest_handler)


def _update_job(job_id: str, *, status: str, result: dict | None = None, error: str | None = None,
                mark_started: bool = False, mark_finished: bool = False) -> None:
    if mark_started and mark_finished:
        ts_clause = ", started_at = COALESCE(started_at, now()), finished_at = now()"
    elif mark_started:
        ts_clause = ", started_at = COALESCE(started_at, now())"
    elif mark_finished:
        ts_clause = ", finished_at = now()"
    else:
        ts_clause = ""
    with db_session() as s:
        s.execute(
            text(
                f"""
                UPDATE jobs
                SET status = :st,
                    result = COALESCE(CAST(:res AS jsonb), result),
                    error = COALESCE(:err, error){ts_clause}
                WHERE job_id = CAST(:jid AS uuid)
                """
            ),
            {"st": status, "res": j(result) if result is not None else None, "err": error, "jid": job_id},
        )


def create_job(*, type: str, patient_id: str | None, document_id: str | None, payload: dict[str, Any]) -> str:
    job_id = str(uuid.uuid4())
    with db_session() as s:
        s.execute(
            text(
                """
                INSERT INTO jobs (job_id, type, status, patient_id, document_id, payload)
                VALUES (CAST(:jid AS uuid), :t, 'pending', :pid, :did, CAST(:p AS jsonb))
                """
            ),
            {"jid": job_id, "t": type, "pid": patient_id, "did": document_id, "p": j(payload)},
        )
    return job_id


async def run_ingest_job(job_id: str, req: EMRIngestRequest) -> None:
    _update_job(job_id, status="running", mark_started=True)
    try:
        result = await run_ingest(req, job_id=job_id)
        _update_job(job_id, status="completed", result=result, mark_finished=True)
    except Exception as exc:
        logger.exception("Job %s failed: %s", job_id, exc)
        _update_job(job_id, status="failed", error=str(exc), mark_finished=True)


def get_job(job_id: str) -> dict[str, Any] | None:
    with db_session() as s:
        row = s.execute(
            text(
                "SELECT job_id, type, status, patient_id, document_id, payload, result, error, "
                "started_at, finished_at, created_at FROM jobs WHERE job_id = CAST(:j AS uuid)"
            ),
            {"j": job_id},
        ).mappings().first()
    if not row:
        return None
    d = dict(row)
    d["job_id"] = str(d["job_id"])
    return d


def schedule_ingest(req: EMRIngestRequest) -> str:
    job_id = create_job(
        type="emr_ingest",
        patient_id=req.patient.patientId,
        document_id=(req.source.documentId if req.source else None),
        payload=req.model_dump(mode="json"),
    )
    return job_id
