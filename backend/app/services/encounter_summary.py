"""Encounter-scoped summary + coding service layer. Thin wrappers around the
existing AI provider; the aggregator + persistence already handle the rest."""
from __future__ import annotations

import asyncio

from app.schemas.coding import (
    CodingSuggestRequest, CodingSuggestResponse,
    SummaryRequest, SummaryResponse,
)
from app.schemas.extraction import CodingCandidate, DiagnosisCandidate
from app.services.ai_provider import get_ai_provider
from app.services.patient_facts import gather_encounter_facts
from app.services.summary_store import save_coding, save_summary


_ADMISSION_TYPES = {"admission", "discharge_summary", "admission_note"}


def default_summary_type_for(encounter_type: str | None) -> str:
    return "discharge_summary" if (encounter_type or "") in _ADMISSION_TYPES else "detailed"


async def make_encounter_summary(
    patient_id: str, encounter_id: str, req: SummaryRequest
) -> SummaryResponse:
    facts = await asyncio.to_thread(gather_encounter_facts, encounter_id)
    summary_type = req.type or default_summary_type_for(facts["encounter"]["type"])
    provider = get_ai_provider()
    md, rec = await provider.summarize(
        patient_facts=facts, summary_type=summary_type, patient_id=patient_id,
    )
    await asyncio.to_thread(
        save_summary,
        patient_id=patient_id, encounter_id=encounter_id, summary_type=summary_type,
        markdown=md, evidence=facts if req.includeEvidence else None,
        model=rec.model, cost_usd=rec.cost_usd, latency_ms=rec.latency_ms,
    )
    return SummaryResponse(
        patientId=patient_id, type=summary_type, markdown=md,
        **{"json": facts if req.includeEvidence else {
            "counts": {
                "thisEncounter": {k: len(v) for k, v in facts["thisEncounter"].items()},
                "background": {k: len(v) for k, v in facts["background"].items()},
                "documents": len(facts["documents"]),
            },
        }},
    )


async def suggest_encounter_coding(
    patient_id: str, encounter_id: str, req: CodingSuggestRequest
) -> CodingSuggestResponse:
    facts = await asyncio.to_thread(gather_encounter_facts, encounter_id)
    provider = get_ai_provider()
    raw, rec = await provider.suggest_coding(
        patient_facts=facts, standards=req.standards, patient_id=patient_id,
    )

    def to_diag(d: dict | None) -> DiagnosisCandidate | None:
        if not d:
            return None
        try:
            return DiagnosisCandidate.model_validate(d)
        except Exception:
            return None

    candidates: list[CodingCandidate] = []
    for c in raw.get("codingCandidates", []) or []:
        try:
            candidates.append(CodingCandidate.model_validate(c))
        except Exception:
            continue

    response = CodingSuggestResponse(
        patientId=patient_id,
        primaryDiagnosis=to_diag(raw.get("primaryDiagnosis")),
        secondaryDiagnoses=[d for d in (to_diag(x) for x in raw.get("secondaryDiagnoses", []) or []) if d],
        complications=[d for d in (to_diag(x) for x in raw.get("complications", []) or []) if d],
        comorbidities=[d for d in (to_diag(x) for x in raw.get("comorbidities", []) or []) if d],
        codingCandidates=candidates,
        evidence=raw.get("evidence", []) or [],
        warnings=raw.get("warnings", []) or [],
    )
    await asyncio.to_thread(
        save_coding,
        patient_id=patient_id, encounter_id=encounter_id,
        payload=response.model_dump(mode="json"),
        model=rec.model, cost_usd=rec.cost_usd, latency_ms=rec.latency_ms,
    )
    return response
