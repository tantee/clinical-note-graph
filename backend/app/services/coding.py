from __future__ import annotations

import asyncio
import json
from typing import Any

from app.schemas.coding import CodingSuggestRequest, CodingSuggestResponse
from app.schemas.extraction import CodingCandidate, DiagnosisCandidate
from app.services.ai_provider import get_ai_provider
from app.services.patient_facts import gather_patient_facts_for_ai
from app.services.summary_store import save_coding


def _has_any_codes(raw: dict[str, Any]) -> bool:
    """Did the model produce SOMETHING actionable? Either a primaryDiagnosis
    or at least one codingCandidate. Used to decide whether to retry."""
    if raw.get("primaryDiagnosis"):
        return True
    cands = raw.get("codingCandidates") or []
    return any(c for c in cands if (c or {}).get("code"))


_CODING_RETRY_ADDENDUM = (
    "Your previous response had no codingCandidates AND no primaryDiagnosis. "
    "That's not acceptable per the schema rules above — empty output forces "
    "the human coder to redo the work from scratch. Please retry. Pick the "
    "single most likely primary diagnosis from the patient's problems (any "
    "tie-break is fine, just commit and note the reason in `warnings`), and "
    "emit at least one ICD-10 candidate per active problem at lower confidence "
    "(0.3-0.5 is fine when uncertain). Any caveats belong in `warnings`, not "
    "as a reason to omit codes."
)


def _patient_has_problems(facts: dict[str, Any]) -> bool:
    """True if the patient has any condition/diagnosis worth retrying on.

    Looks at chronicProblems (patient-wide dedup'd list) and falls back to
    walking the encounters for raw_facts diagnoses — covers the edge case
    where a condition only appears as an encounter-scoped diagnosis_candidate
    and didn't make it into the deduped chronic list.
    """
    if facts.get("chronicProblems"):
        return True
    for enc in facts.get("encounters") or []:
        if enc.get("problems") or enc.get("diagnoses"):
            return True
    return False


async def suggest_coding(patient_id: str, req: CodingSuggestRequest) -> CodingSuggestResponse:
    facts = await asyncio.to_thread(gather_patient_facts_for_ai, patient_id)
    provider = get_ai_provider()
    raw, rec = await provider.suggest_coding(
        patient_facts=facts, standards=req.standards, patient_id=patient_id,
    )
    # If the model punted (no primary diagnosis AND no candidates), retry
    # once with an explicit reminder of the rules from CODING_SUGGEST_SYSTEM.
    # Only retry when the patient actually has problems — empty input
    # legitimately produces empty output.
    if _patient_has_problems(facts) and not _has_any_codes(raw):
        raw, rec = await provider.suggest_coding(
            patient_facts=facts, standards=req.standards, patient_id=patient_id,
            system_addendum=_CODING_RETRY_ADDENDUM,
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
