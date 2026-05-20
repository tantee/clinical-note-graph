from __future__ import annotations

import asyncio
import json
from typing import Any

from app.schemas.coding import CodingSuggestRequest, CodingSuggestResponse
from app.schemas.extraction import CodingCandidate, DiagnosisCandidate
from app.services.ai_provider import get_ai_provider
from app.services.patient_facts import gather_patient_facts
from app.services.summary_store import save_coding


async def suggest_coding(patient_id: str, req: CodingSuggestRequest) -> CodingSuggestResponse:
    facts = await asyncio.to_thread(gather_patient_facts, patient_id)
    provider = get_ai_provider()
    raw, rec = await provider.suggest_coding(patient_facts=facts, standards=req.standards, patient_id=patient_id)

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

    # Models occasionally collapse list-valued fields into prose. Coerce so
    # one wandering string doesn't 500 the whole coding endpoint — the schema
    # requires list[str] for warnings and list[dict] for evidence.
    raw_warnings = raw.get("warnings") or []
    if isinstance(raw_warnings, str):
        raw_warnings = [raw_warnings] if raw_warnings.strip() else []
    elif not isinstance(raw_warnings, list):
        raw_warnings = []

    raw_evidence = raw.get("evidence") or []
    if isinstance(raw_evidence, str):
        raw_evidence = [{"text": raw_evidence}] if raw_evidence.strip() else []
    elif isinstance(raw_evidence, list):
        # Drop any non-dict entries (some models emit a list of strings here).
        raw_evidence = [
            (e if isinstance(e, dict) else {"text": str(e)})
            for e in raw_evidence
        ]
    else:
        raw_evidence = []

    response = CodingSuggestResponse(
        patientId=patient_id,
        primaryDiagnosis=to_diag(raw.get("primaryDiagnosis")),
        secondaryDiagnoses=[d for d in (to_diag(x) for x in raw.get("secondaryDiagnoses", []) or []) if d],
        complications=[d for d in (to_diag(x) for x in raw.get("complications", []) or []) if d],
        comorbidities=[d for d in (to_diag(x) for x in raw.get("comorbidities", []) or []) if d],
        codingCandidates=candidates,
        evidence=raw_evidence,
        warnings=[str(w) for w in raw_warnings],
    )
    await asyncio.to_thread(
        save_coding,
        patient_id=patient_id, payload=response.model_dump(mode="json"),
        model=rec.model, cost_usd=rec.cost_usd, latency_ms=rec.latency_ms,
    )
    return response
