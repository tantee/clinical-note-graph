"""Encounter-scoped routes — summary + coding mirroring the patient-level
surface, plus the dependency that validates eid belongs to pid."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
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
    response_model=SummaryResponse,
    dependencies=[Depends(verify_encounter)],
)
async def encounter_summary(patient_id: str, encounter_id: str, req: SummaryRequest):
    return await make_encounter_summary(patient_id, encounter_id, req)


@router.get(
    "/patient/{patient_id}/encounter/{encounter_id}/summary/latest",
    dependencies=[Depends(verify_encounter)],
)
def encounter_summary_latest(patient_id: str, encounter_id: str) -> dict[str, Any] | None:
    return latest_summary(patient_id, encounter_id=encounter_id)


@router.post(
    "/patient/{patient_id}/encounter/{encounter_id}/coding/suggest",
    response_model=CodingSuggestResponse,
    dependencies=[Depends(verify_encounter)],
)
async def encounter_coding(patient_id: str, encounter_id: str, req: CodingSuggestRequest):
    return await suggest_encounter_coding(patient_id, encounter_id, req)


@router.get(
    "/patient/{patient_id}/encounter/{encounter_id}/coding/latest",
    dependencies=[Depends(verify_encounter)],
)
def encounter_coding_latest(patient_id: str, encounter_id: str) -> dict[str, Any] | None:
    return latest_coding(patient_id, encounter_id=encounter_id)
