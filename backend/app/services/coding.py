from __future__ import annotations

import asyncio
import json
from typing import Any

from app.schemas.coding import CodingSuggestRequest, CodingSuggestResponse
from app.schemas.extraction import CodingCandidate, DiagnosisCandidate
from app.services.ai_provider import get_ai_provider
from app.services.patient_facts import gather_patient_facts


async def suggest_coding(patient_id: str, req: CodingSuggestRequest) -> CodingSuggestResponse:
    facts = await asyncio.to_thread(gather_patient_facts, patient_id)
    provider = get_ai_provider()
    raw = await provider.suggest_coding(patient_facts=facts, standards=req.standards)

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

    return CodingSuggestResponse(
        patientId=patient_id,
        primaryDiagnosis=to_diag(raw.get("primaryDiagnosis")),
        secondaryDiagnoses=[d for d in (to_diag(x) for x in raw.get("secondaryDiagnoses", []) or []) if d],
        complications=[d for d in (to_diag(x) for x in raw.get("complications", []) or []) if d],
        comorbidities=[d for d in (to_diag(x) for x in raw.get("comorbidities", []) or []) if d],
        codingCandidates=candidates,
        evidence=raw.get("evidence", []) or [],
        warnings=raw.get("warnings", []) or [],
    )
