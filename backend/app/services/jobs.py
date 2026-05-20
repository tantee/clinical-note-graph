from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text

from app.db.helpers import j
from app.db.postgres import db_session
from app.schemas.coding import CodingSuggestRequest, SummaryRequest
from app.schemas.emr import EMRIngestRequest
from app.services.coding import suggest_coding
from app.services.encounter_summary import (
    make_encounter_summary, suggest_encounter_coding,
)
from app.services.ingest import run_ingest_pipeline
from app.services.queue import register_handler
from app.services.summary import make_summary

logger = logging.getLogger(__name__)


# Each handler wraps an existing async service call. The service is the
# same code the sync routes have always used — the queue handler is just
# the indirection that lets us return a jobId immediately and finish the
# work in the background worker. Result is persisted both in the
# corresponding `patient_summaries` row (via the existing services) AND
# in `jobs.result` (via the queue's _finalize_success), so the UI can
# poll the job for status and the per-patient /latest endpoint for the
# typed payload.

async def _emr_ingest_handler(job: dict, on_progress) -> dict:
    payload = job["payload"]
    req = EMRIngestRequest.model_validate(payload)
    return await run_ingest_pipeline(req, job_id=str(job["job_id"]), on_progress=on_progress)


async def _patient_summary_handler(job: dict, on_progress) -> dict:
    req = SummaryRequest.model_validate(job["payload"])
    resp = await make_summary(job["patient_id"], req)
    on_progress("stage_summary_done", type=req.type)
    return resp.model_dump(mode="json")


async def _patient_coding_handler(job: dict, on_progress) -> dict:
    req = CodingSuggestRequest.model_validate(job["payload"])
    resp = await suggest_coding(job["patient_id"], req)
    on_progress("stage_coding_done",
                primary=bool(resp.primaryDiagnosis),
                candidates=len(resp.codingCandidates or []))
    return resp.model_dump(mode="json")


async def _encounter_summary_handler(job: dict, on_progress) -> dict:
    payload = job["payload"]
    # encounter_id is nested in the payload because jobs.encounter_id isn't a
    # column — we use the job's `payload` JSONB to carry the scope.
    encounter_id = payload.pop("__encounter_id")
    req = SummaryRequest.model_validate(payload)
    resp = await make_encounter_summary(job["patient_id"], encounter_id, req)
    on_progress("stage_encounter_summary_done", type=req.type, encounterId=encounter_id)
    return resp.model_dump(mode="json")


async def _encounter_coding_handler(job: dict, on_progress) -> dict:
    payload = job["payload"]
    encounter_id = payload.pop("__encounter_id")
    req = CodingSuggestRequest.model_validate(payload)
    resp = await suggest_encounter_coding(job["patient_id"], encounter_id, req)
    on_progress("stage_encounter_coding_done",
                encounterId=encounter_id,
                primary=bool(resp.primaryDiagnosis),
                candidates=len(resp.codingCandidates or []))
    return resp.model_dump(mode="json")


register_handler("emr_ingest", _emr_ingest_handler)
register_handler("patient_summary", _patient_summary_handler)
register_handler("patient_coding", _patient_coding_handler)
register_handler("encounter_summary", _encounter_summary_handler)
register_handler("encounter_coding", _encounter_coding_handler)


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


def schedule_patient_summary(patient_id: str, req: SummaryRequest) -> str:
    return create_job(
        type="patient_summary",
        patient_id=patient_id, document_id=None,
        payload=req.model_dump(mode="json"),
    )


def schedule_patient_coding(patient_id: str, req: CodingSuggestRequest) -> str:
    return create_job(
        type="patient_coding",
        patient_id=patient_id, document_id=None,
        payload=req.model_dump(mode="json"),
    )


def schedule_encounter_summary(patient_id: str, encounter_id: str, req: SummaryRequest) -> str:
    # encounter_id rides in the payload because the jobs table doesn't carry
    # an encounter_id column. Handler pops it back out before calling the
    # service. Prefixed `__` so it doesn't collide with any future
    # SummaryRequest field.
    payload = req.model_dump(mode="json")
    payload["__encounter_id"] = encounter_id
    return create_job(
        type="encounter_summary",
        patient_id=patient_id, document_id=None,
        payload=payload,
    )


def schedule_encounter_coding(patient_id: str, encounter_id: str, req: CodingSuggestRequest) -> str:
    payload = req.model_dump(mode="json")
    payload["__encounter_id"] = encounter_id
    return create_job(
        type="encounter_coding",
        patient_id=patient_id, document_id=None,
        payload=payload,
    )
