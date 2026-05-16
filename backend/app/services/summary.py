from __future__ import annotations

import asyncio

from app.schemas.coding import SummaryRequest, SummaryResponse
from app.services.ai_provider import get_ai_provider
from app.services.patient_facts import gather_patient_facts


async def make_summary(patient_id: str, req: SummaryRequest) -> SummaryResponse:
    start = req.dateRange.start if req.dateRange else None
    end = req.dateRange.end if req.dateRange else None
    facts = await asyncio.to_thread(gather_patient_facts, patient_id, start=start, end=end)
    provider = get_ai_provider()
    md = await provider.summarize(patient_facts=facts, summary_type=req.type)
    return SummaryResponse(
        patientId=patient_id,
        type=req.type,
        markdown=md,
        **{"json": facts if req.includeEvidence else {"counts": {k: len(v) for k, v in facts.items() if isinstance(v, list)}}},
    )
