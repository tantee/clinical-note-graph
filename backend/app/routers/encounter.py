"""Encounter-scoped routes — summary + coding mirroring the patient-level
surface, plus the dependency that validates eid belongs to pid."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.db.postgres import db_session
from app.schemas.coding import (
    CodingSuggestRequest, CodingSuggestResponse,
    SummaryRequest, SummaryResponse,
)
from app.services.encounter_summary import (
    make_encounter_summary, suggest_encounter_coding,
)
from app.services.summary_store import latest_coding, latest_summary

router = APIRouter(prefix="/api", tags=["encounter"])


def verify_encounter(patient_id: str, encounter_id: str) -> None:
    with db_session() as s:
        row = s.execute(
            text("SELECT patient_id FROM encounters WHERE encounter_id = :eid"),
            {"eid": encounter_id},
        ).mappings().first()
    if not row or row["patient_id"] != patient_id:
        raise HTTPException(status_code=404, detail="Encounter not found for patient")


@router.post(
    "/patient/{patient_id}/encounter/{encounter_id}/summary",
    dependencies=[Depends(verify_encounter)],
)
async def encounter_summary(
    patient_id: str, encounter_id: str, req: SummaryRequest,
    async_processing: bool = Query(False, alias="async"),
):
    if async_processing:
        from app.services.jobs import schedule_encounter_summary
        job_id = schedule_encounter_summary(patient_id, encounter_id, req)
        return {"jobId": job_id, "status": "queued", "type": "encounter_summary",
                "patientId": patient_id, "encounterId": encounter_id}
    return await make_encounter_summary(patient_id, encounter_id, req)


@router.get(
    "/patient/{patient_id}/encounter/{encounter_id}/summary/latest",
    dependencies=[Depends(verify_encounter)],
)
def encounter_summary_latest(patient_id: str, encounter_id: str) -> dict[str, Any] | None:
    return latest_summary(patient_id, encounter_id=encounter_id)


@router.post(
    "/patient/{patient_id}/encounter/{encounter_id}/coding/suggest",
    dependencies=[Depends(verify_encounter)],
)
async def encounter_coding(
    patient_id: str, encounter_id: str, req: CodingSuggestRequest,
    async_processing: bool = Query(False, alias="async"),
):
    if async_processing:
        from app.services.jobs import schedule_encounter_coding
        job_id = schedule_encounter_coding(patient_id, encounter_id, req)
        return {"jobId": job_id, "status": "queued", "type": "encounter_coding",
                "patientId": patient_id, "encounterId": encounter_id}
    return await suggest_encounter_coding(patient_id, encounter_id, req)


@router.get(
    "/patient/{patient_id}/encounter/{encounter_id}/coding/latest",
    dependencies=[Depends(verify_encounter)],
)
def encounter_coding_latest(patient_id: str, encounter_id: str) -> dict[str, Any] | None:
    return latest_coding(patient_id, encounter_id=encounter_id)
