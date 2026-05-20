from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import httpx
from sqlalchemy import text

from app.config import Settings
from app.db.helpers import j
from app.db.postgres import db_session
from app.prompts.templates import (
    CODING_SUGGEST_SYSTEM,
    EXTRACTION_SYSTEM,
    EXTRACTION_USER,
    RAG_SYSTEM,
    SUMMARY_SYSTEM,
    summary_system_for,
)
from app.schemas.extraction import ClinicalExtractionResult
from app.services.deidentify import Deidentifier, for_settings as deidentifier_for
from app.services.pricing import compute_cost, load_rates
from app.services.runtime_config import effective as effective_settings


def _provider_label(base_url: str) -> str:
    """Coarse provider label for log lines — `openrouter`, `openai`, or `custom`."""
    b = (base_url or "").lower()
    if "openrouter.ai" in b:
        return "openrouter"
    if "api.openai.com" in b:
        return "openai"
    if "anthropic" in b:
        return "anthropic"
    return "custom"


def _log_redaction(call_type: str, provider: str, deid: "Deidentifier") -> None:
    """Emit a single log line per call summarising what got redacted, e.g.
    `redacted_categories=PERSON:2,EMAIL_ADDRESS:1 ai_provider=openrouter call_type=extract`.
    """
    counts = deid.counts_snapshot()
    if not counts:
        return
    summary = ",".join(f"{k}:{v}" for k, v in sorted(counts.items()))
    log_msg = (
        f"redacted_categories={summary} ai_provider={provider} call_type={call_type}"
    )
    # Reuse the module logger via stdlib logging so the audit-trail line is
    # consistent with the rest of the backend.
    import logging
    logging.getLogger("app.services.ai_provider").info(log_msg)


CallType = Literal["extract", "summary", "coding", "embed", "rag"]


@dataclass
class AICallRecord:
    call_type: CallType
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    cost_usd: Decimal | None
    raw_response: dict
    error: str | None
    job_id: str | None
    patient_id: str | None
    document_id: str | None


_PROMPT_TEMPLATE_BY_CALL_TYPE = {
    "extract": "EMR_EXTRACTION",
    "summary": "SUMMARY",
    "coding": "CODING_SUGGEST",
    "embed": "EMBED",
    "rag": "RAG",
}


def _persist_ai_call(
    rec: AICallRecord,
    *,
    valid: bool,
    validation_errors: list,
    deidentified: bool = False,
    redaction_counts: dict | None = None,
) -> None:
    """Persist one AI provider call into ai_outputs with all metering columns."""
    with db_session() as s:
        s.execute(
            text(
                """
                INSERT INTO ai_outputs
                  (document_id, patient_id, job_id, prompt_template, model, raw_output,
                   valid, validation_errors, call_type, prompt_tokens, completion_tokens,
                   total_tokens, latency_ms, cost_usd, error,
                   deidentified, redaction_counts)
                VALUES
                  (:d, :p, :job_id, :pt, :m, CAST(:r AS jsonb),
                   :v, CAST(:e AS jsonb), :call_type, :prompt_tokens, :completion_tokens,
                   :total_tokens, :latency_ms, :cost_usd, :err,
                   :deidentified, CAST(:redaction_counts AS jsonb))
                """
            ),
            {
                "d": rec.document_id,
                "p": rec.patient_id,
                "job_id": rec.job_id,
                "pt": _PROMPT_TEMPLATE_BY_CALL_TYPE.get(rec.call_type, rec.call_type.upper()),
                "m": rec.model,
                "r": j(rec.raw_response),
                "v": valid,
                "e": j(validation_errors),
                "call_type": rec.call_type,
                "prompt_tokens": rec.prompt_tokens,
                "completion_tokens": rec.completion_tokens,
                "total_tokens": rec.total_tokens,
                "latency_ms": rec.latency_ms,
                "cost_usd": str(rec.cost_usd) if rec.cost_usd is not None else None,
                "err": rec.error,
                "deidentified": bool(deidentified),
                "redaction_counts": j(redaction_counts) if redaction_counts else None,
            },
        )


def _estimate_tokens(text_in: str) -> int:
    """Cheap dev-friendly token estimate: ~1.3 tokens per whitespace word, min 1."""
    if not text_in:
        return 1
    return max(1, int(len(text_in.split()) * 1.3))


class AIProvider(ABC):
    @abstractmethod
    async def extract(
        self,
        *,
        patient_id: str,
        encounter_type: str,
        encounter_dt: str,
        document_id: str,
        content: str,
        job_id: str | None = None,
    ) -> tuple[dict[str, Any], AICallRecord]:
        ...

    @abstractmethod
    async def suggest_coding(
        self,
        *,
        patient_facts: dict[str, Any],
        standards: list[str],
        job_id: str | None = None,
        patient_id: str | None = None,
    ) -> tuple[dict[str, Any], AICallRecord]:
        ...

    @abstractmethod
    async def summarize(
        self,
        *,
        patient_facts: dict[str, Any],
        summary_type: str,
        job_id: str | None = None,
        patient_id: str | None = None,
    ) -> tuple[str, AICallRecord]:
        ...

    @abstractmethod
    async def embed(
        self,
        text_in: str,
        *,
        job_id: str | None = None,
        patient_id: str | None = None,
        ref_id: str | None = None,
    ) -> tuple[list[float], AICallRecord]:
        ...

    @abstractmethod
    async def rag_ask(
        self,
        *,
        question: str,
        chunks: list[dict[str, Any]],
        history: list[dict[str, str]] | None = None,
        patient_id: str | None = None,
    ) -> tuple[str, AICallRecord]:
        """Compose a RAG answer given retrieved chunks and optional prior
        chat history. Returns (markdown_answer, ai_call_record)."""
        ...


# ----------------------------- MOCK PROVIDER -----------------------------

KEYWORDS = {
    "diabetes": {"value": "Type 2 diabetes mellitus", "icd10": "E11.9", "snomed": "44054006"},
    "hypertension": {"value": "Essential hypertension", "icd10": "I10", "snomed": "59621000"},
    "asthma": {"value": "Asthma", "icd10": "J45.909", "snomed": "195967001"},
    "pneumonia": {"value": "Pneumonia", "icd10": "J18.9", "snomed": "233604007"},
    "copd": {"value": "Chronic obstructive pulmonary disease", "icd10": "J44.9", "snomed": "13645005"},
    "chest pain": {"value": "Chest pain", "icd10": "R07.9", "snomed": "29857009"},
    "myocardial infarction": {"value": "Acute myocardial infarction", "icd10": "I21.9", "snomed": "22298006"},
    "stroke": {"value": "Cerebrovascular accident", "icd10": "I63.9", "snomed": "230690007"},
    "anemia": {"value": "Anemia", "icd10": "D64.9", "snomed": "271737000"},
    "ckd": {"value": "Chronic kidney disease", "icd10": "N18.9", "snomed": "709044004"},
}

MED_KEYWORDS = {
    "metformin": {"rxnorm": "6809", "indication": "diabetes"},
    "insulin": {"rxnorm": "5856", "indication": "diabetes"},
    "lisinopril": {"rxnorm": "29046", "indication": "hypertension"},
    "amlodipine": {"rxnorm": "17767", "indication": "hypertension"},
    "atorvastatin": {"rxnorm": "83367", "indication": "hyperlipidemia"},
    "aspirin": {"rxnorm": "1191", "indication": "antiplatelet"},
    "salbutamol": {"rxnorm": "435", "indication": "asthma"},
    "albuterol": {"rxnorm": "435", "indication": "asthma"},
    "amoxicillin": {"rxnorm": "723", "indication": "infection"},
    "ceftriaxone": {"rxnorm": "2193", "indication": "infection"},
}

LAB_PATTERNS = [
    (re.compile(r"(?i)\b(?:HbA1c|A1C)\b[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*%?"), "HbA1c", "4548-4", "%"),
    (re.compile(r"(?i)\bglucose\b[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*(mg/dl|mmol/l)?"), "Glucose", "2345-7", "mg/dL"),
    (re.compile(r"(?i)\bbp\b[^0-9]*([0-9]{2,3})\s*/\s*([0-9]{2,3})"), "Blood pressure", "85354-9", "mmHg"),
    (re.compile(r"(?i)\bcreatinine\b[^0-9]*([0-9]+(?:\.[0-9]+)?)"), "Creatinine", "2160-0", "mg/dL"),
    (re.compile(r"(?i)\bhemoglobin\b[^0-9]*([0-9]+(?:\.[0-9]+)?)"), "Hemoglobin", "718-7", "g/dL"),
    (re.compile(r"(?i)\bSpO2\b[^0-9]*([0-9]+)"), "SpO2", "59408-5", "%"),
    (re.compile(r"(?i)\btemp(?:erature)?\b[^0-9]*([0-9]+(?:\.[0-9]+)?)"), "Temperature", "8310-5", "°C"),
]

PLAN_HINTS = [
    "plan:", "plan -", "discharge plan", "follow up", "follow-up", "consult", "admit",
    "investigate", "monitor", "education", "advise",
]


def _evidence_window(text: str, idx: int, span: int = 90) -> str:
    start = max(0, idx - span)
    end = min(len(text), idx + span)
    snippet = text[start:end].strip().replace("\n", " ")
    return snippet


def mock_extract(content: str, *, patient_id: str, encounter_id: str | None, document_id: str | None) -> dict[str, Any]:
    """Deterministic keyword-based extractor used when AI_PROVIDER=mock."""
    text = content
    lower = text.lower()

    problems: list[dict[str, Any]] = []
    diagnoses: list[dict[str, Any]] = []
    coding: list[dict[str, Any]] = []
    seen_conditions: set[str] = set()
    for k, v in KEYWORDS.items():
        idx = lower.find(k)
        if idx < 0 or v["value"] in seen_conditions:
            continue
        seen_conditions.add(v["value"])
        evidence = _evidence_window(text, idx)
        problems.append(
            {
                "type": "condition",
                "value": v["value"],
                "normalizedCode": v["icd10"],
                "codingSystem": "ICD10",
                "sourceDocumentId": document_id,
                "evidenceText": evidence,
                "confidence": 0.7,
                "reviewStatus": "ai_suggested",
            }
        )
        diagnoses.append(
            {
                "condition": v["value"],
                "icd10": v["icd10"],
                "snomed": v["snomed"],
                "rationale": f"Mentioned in document near: '{evidence[:60]}...'",
                "evidenceText": evidence,
                "confidence": 0.65,
                "role": "candidate",
            }
        )
        coding.append(
            {
                "code": v["icd10"],
                "system": "ICD10",
                "display": v["value"],
                "forCondition": v["value"],
                "rationale": "Keyword match (mock extractor)",
                "confidence": 0.6,
            }
        )
        coding.append(
            {
                "code": v["snomed"],
                "system": "SNOMEDCT",
                "display": v["value"],
                "forCondition": v["value"],
                "rationale": "Keyword match (mock extractor)",
                "confidence": 0.6,
            }
        )

    medications: list[dict[str, Any]] = []
    for k, m in MED_KEYWORDS.items():
        idx = lower.find(k)
        if idx < 0:
            continue
        evidence = _evidence_window(text, idx)
        # crude action detection
        action = "start"
        for cue, a in (("stop", "stop"), ("discontinue", "stop"), ("hold", "hold"), ("continue", "continue"), ("change", "modify")):
            if cue in lower[max(0, idx - 30) : idx + 30]:
                action = a
                break
        medications.append(
            {
                "name": k.capitalize(),
                "rxNorm": m["rxnorm"],
                "action": action,
                "indication": m["indication"],
                "evidenceText": evidence,
                "confidence": 0.65,
            }
        )

    observations: list[dict[str, Any]] = []
    for pattern, name, loinc, unit in LAB_PATTERNS:
        for match in pattern.finditer(text):
            if name == "Blood pressure":
                value = f"{match.group(1)}/{match.group(2)}"
            else:
                value = match.group(1)
            observations.append(
                {
                    "name": name,
                    "loinc": loinc,
                    "value": value,
                    "unit": unit,
                    "evidenceText": _evidence_window(text, match.start()),
                    "confidence": 0.7,
                }
            )

    plans: list[dict[str, Any]] = []
    for line in text.splitlines():
        low = line.lower().strip()
        if not low:
            continue
        if any(h in low for h in PLAN_HINTS):
            plans.append(
                {
                    "description": line.strip(" -*\t"),
                    "category": "other",
                    "evidenceText": line.strip(),
                    "confidence": 0.55,
                }
            )

    primary = diagnoses[0] if diagnoses else None
    summary_bits = []
    if primary:
        summary_bits.append(f"Primary: {primary['condition']}")
    if medications:
        summary_bits.append("Meds: " + ", ".join(m["name"] for m in medications[:5]))
    if observations:
        summary_bits.append("Obs: " + ", ".join(f"{o['name']}={o['value']}" for o in observations[:5]))

    result = {
        "patientId": patient_id,
        "encounterId": encounter_id,
        "documentId": document_id,
        "summary": "; ".join(summary_bits) or "No structured findings extracted by mock provider.",
        "problems": problems,
        "medications": medications,
        "observations": observations,
        "procedures": [],
        "allergies": [],
        "plans": plans,
        "diagnoses": diagnoses,
        "codingCandidates": coding,
        "graphUpdates": [],
        "markdownUpdates": [],
        "warnings": [],
    }
    # validate before returning
    ClinicalExtractionResult.model_validate(result)
    return result


def _mock_summary_markdown(patient_facts: dict[str, Any], summary_type: str) -> str:
    problems = patient_facts.get("problems", [])
    meds = patient_facts.get("medications", [])
    obs = patient_facts.get("observations", [])
    plans = patient_facts.get("plans", [])

    lines = [f"# Patient Summary ({summary_type})", ""]
    if problems:
        lines.append("## Active problems")
        for p in problems:
            code = f" `{p.get('normalizedCode')}`" if p.get("normalizedCode") else ""
            lines.append(f"- {p.get('value')}{code}")
        lines.append("")
    if meds:
        lines.append("## Medications")
        for m in meds:
            lines.append(
                f"- {m.get('name')} — {m.get('action', 'start')}"
                f"{(' (' + m.get('indication') + ')') if m.get('indication') else ''}"
            )
        lines.append("")
    if obs:
        lines.append("## Recent observations")
        for o in obs[:20]:
            lines.append(f"- {o.get('name')}: {o.get('value')} {o.get('unit', '')}".rstrip())
        lines.append("")
    if plans:
        lines.append("## Plan")
        for p in plans:
            lines.append(f"- {p.get('description')}")
        lines.append("")
    lines.append("> AI-assisted output requires clinical review.")
    return "\n".join(lines)


def _mock_suggest_coding(patient_facts: dict[str, Any], standards: list[str]) -> dict[str, Any]:
    diagnoses = []
    candidates: list[dict[str, Any]] = []
    seen = set()
    for f in patient_facts.get("problems", []):
        cond = f.get("value")
        if not cond or cond in seen:
            continue
        seen.add(cond)
        icd = f.get("normalizedCode") if f.get("codingSystem") == "ICD10" else None
        snomed = None
        for k, v in KEYWORDS.items():
            if v["value"].lower() == cond.lower():
                icd = icd or v["icd10"]
                snomed = v["snomed"]
                break
        diagnoses.append(
            {
                "condition": cond,
                "icd10": icd,
                "snomed": snomed,
                "evidenceText": f.get("evidenceText"),
                "confidence": float(f.get("confidence", 0.5)),
                "role": "candidate",
            }
        )
        if "ICD10" in standards and icd:
            candidates.append(
                {"code": icd, "system": "ICD10", "display": cond, "forCondition": cond, "confidence": 0.6}
            )
        if "SNOMEDCT" in standards and snomed:
            candidates.append(
                {"code": snomed, "system": "SNOMEDCT", "display": cond, "forCondition": cond, "confidence": 0.6}
            )

    diagnoses.sort(key=lambda d: -d.get("confidence", 0.0))
    primary = diagnoses[0] if diagnoses else None
    if primary:
        primary = {**primary, "role": "primary"}
    secondary = [{**d, "role": "secondary"} for d in diagnoses[1:]]
    return {
        "primaryDiagnosis": primary,
        "secondaryDiagnoses": secondary,
        "complications": [],
        "comorbidities": [],
        "codingCandidates": candidates,
        "evidence": [
            {"condition": d["condition"], "evidence": d.get("evidenceText")}
            for d in diagnoses
            if d.get("evidenceText")
        ],
        "warnings": [],
    }


class MockProvider(AIProvider):
    async def extract(
        self,
        *,
        patient_id: str,
        encounter_type: str,
        encounter_dt: str,
        document_id: str,
        content: str,
        job_id: str | None = None,
    ) -> tuple[dict[str, Any], AICallRecord]:
        t0 = time.perf_counter()
        out = mock_extract(content, patient_id=patient_id, encounter_id=None, document_id=document_id)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        prompt_tok = _estimate_tokens(content)
        completion_tok = _estimate_tokens(str(out)[:5000])
        rec = AICallRecord(
            call_type="extract",
            model="mock",
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
            total_tokens=prompt_tok + completion_tok,
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                load_rates("mock"),
                prompt_tokens=prompt_tok,
                completion_tokens=completion_tok,
            ),
            raw_response=out,
            error=None,
            job_id=job_id,
            patient_id=patient_id,
            document_id=document_id,
        )
        _persist_ai_call(rec, valid=True, validation_errors=[])
        return out, rec

    async def suggest_coding(
        self,
        *,
        patient_facts: dict[str, Any],
        standards: list[str],
        job_id: str | None = None,
        patient_id: str | None = None,
    ) -> tuple[dict[str, Any], AICallRecord]:
        t0 = time.perf_counter()
        out = _mock_suggest_coding(patient_facts, standards)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        prompt_tok = _estimate_tokens(str(patient_facts)[:8000])
        completion_tok = _estimate_tokens(str(out)[:5000])
        rec = AICallRecord(
            call_type="coding",
            model="mock",
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
            total_tokens=prompt_tok + completion_tok,
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                load_rates("mock"),
                prompt_tokens=prompt_tok,
                completion_tokens=completion_tok,
            ),
            raw_response=out,
            error=None,
            job_id=job_id,
            patient_id=patient_id,
            document_id=None,
        )
        _persist_ai_call(rec, valid=True, validation_errors=[])
        return out, rec

    async def summarize(
        self,
        *,
        patient_facts: dict[str, Any],
        summary_type: str,
        job_id: str | None = None,
        patient_id: str | None = None,
    ) -> tuple[str, AICallRecord]:
        t0 = time.perf_counter()
        md = _mock_summary_markdown(patient_facts, summary_type)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        prompt_tok = _estimate_tokens(str(patient_facts)[:8000])
        completion_tok = _estimate_tokens(md)
        rec = AICallRecord(
            call_type="summary",
            model="mock",
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
            total_tokens=prompt_tok + completion_tok,
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                load_rates("mock"),
                prompt_tokens=prompt_tok,
                completion_tokens=completion_tok,
            ),
            raw_response={"markdown": md},
            error=None,
            job_id=job_id,
            patient_id=patient_id,
            document_id=None,
        )
        _persist_ai_call(rec, valid=True, validation_errors=[])
        return md, rec

    async def embed(
        self,
        text_in: str,
        *,
        job_id: str | None = None,
        patient_id: str | None = None,
        ref_id: str | None = None,
    ) -> tuple[list[float], AICallRecord]:
        t0 = time.perf_counter()
        # Mock provider returns no vector — caller treats [] as "skip persisting".
        vec: list[float] = []
        latency_ms = int((time.perf_counter() - t0) * 1000)
        prompt_tok = _estimate_tokens(text_in)
        rec = AICallRecord(
            call_type="embed",
            model="mock",
            prompt_tokens=prompt_tok,
            completion_tokens=None,
            total_tokens=prompt_tok,
            latency_ms=latency_ms,
            cost_usd=compute_cost(load_rates("mock"), embedding_tokens=prompt_tok),
            raw_response={"ref_id": ref_id, "note": "mock embedding (empty vector)"},
            error=None,
            job_id=job_id,
            patient_id=patient_id,
            document_id=None,
        )
        _persist_ai_call(rec, valid=True, validation_errors=[])
        return vec, rec

    async def rag_ask(
        self,
        *,
        question: str,
        chunks: list[dict[str, Any]],
        history: list[dict[str, str]] | None = None,
        patient_id: str | None = None,
    ) -> tuple[str, AICallRecord]:
        # Deterministic mock: cite [1] if there's at least one chunk,
        # otherwise say no relevant excerpts.
        if not chunks:
            answer = "No relevant excerpts retrieved for this question."
        else:
            first_snippet = (chunks[0].get("content") or "")[:80]
            answer = (
                f"Based on the retrieved excerpts, the relevant information "
                f"is: {first_snippet} [1].\n\n"
                "_AI-assisted output — please verify against the source notes._"
            )
        rec = AICallRecord(
            call_type="rag",
            model="mock-rag",
            prompt_tokens=_estimate_tokens(question + str(chunks)),
            completion_tokens=_estimate_tokens(answer),
            total_tokens=None,
            latency_ms=1,
            cost_usd=None,
            raw_response={"mock": True, "answer": answer},
            error=None,
            job_id=None,
            patient_id=patient_id,
            document_id=None,
        )
        _persist_ai_call(rec, valid=True, validation_errors=[])
        return answer, rec


# ----------------------------- OPENAI-COMPATIBLE PROVIDER -----------------------------


class OpenAICompatibleProvider(AIProvider):
    """Works with OpenAI's API or any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM, etc.)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        base = (settings.AI_BASE_URL or "https://api.openai.com/v1").strip().rstrip("/")
        self.base_url = base
        self.model = settings.AI_MODEL                      # default fallback
        self.embedding_model = settings.AI_EMBEDDING_MODEL
        self.headers = {"Content-Type": "application/json"}
        if settings.AI_API_KEY:
            self.headers["Authorization"] = f"Bearer {settings.AI_API_KEY}"

    def _model_for(self, call_type: str) -> str:
        """Resolve the model for one call_type; falls back to AI_MODEL when override is blank."""
        override = {
            "extract": self.settings.AI_MODEL_EXTRACT,
            "summary": self.settings.AI_MODEL_SUMMARY,
            "coding": self.settings.AI_MODEL_CODING,
        }.get(call_type, "")
        return (override or self.settings.AI_MODEL).strip() or self.settings.AI_MODEL

    async def _chat(
        self, system: str, user: str, *, model: str, json_mode: bool = True
    ) -> tuple[str, dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        # OpenRouter occasionally routes to upstreams that return empty or
        # malformed JSON for json_object responses (observed: WandB on DeepSeek
        # V3.1). Ask OpenRouter to skip them and fall back to a sibling.
        if "openrouter.ai" in (self.base_url or ""):
            payload["provider"] = {"ignore": ["WandB"], "allow_fallbacks": True}

        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=self.headers)
            r.raise_for_status()
            data = r.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if not content:
                raise RuntimeError(
                    f"Empty completion from {data.get('provider') or 'upstream'} for model={model}; "
                    f"finish_reason={(data.get('choices') or [{}])[0].get('finish_reason')!r}"
                )
            return content, data

    async def extract(
        self,
        *,
        patient_id: str,
        encounter_type: str,
        encounter_dt: str,
        document_id: str,
        content: str,
        job_id: str | None = None,
    ) -> tuple[dict[str, Any], AICallRecord]:
        deid = deidentifier_for(self.settings)
        # Pseudonymise the patient ID first so the same token appears in both
        # the prompt scaffold and the embedded narrative.
        safe_patient_id = deid.pseudonym_for(patient_id, "HN") if not deid.disabled() else patient_id
        safe_encounter_dt = deid.pseudonymize_facts({"encounter_dt": encounter_dt})["encounter_dt"]
        safe_content = deid.redact_text(content, lang="auto")

        system = EXTRACTION_SYSTEM + "\n\nSchema:\n" + json.dumps(
            ClinicalExtractionResult.model_json_schema()
        )
        user = EXTRACTION_USER.format(
            patient_id=safe_patient_id,
            encounter_type=encounter_type,
            encounter_dt=safe_encounter_dt,
            document_id=document_id,
            content=safe_content,
        )
        model = self._model_for("extract")
        t0 = time.perf_counter()
        raw_text, raw_resp = await self._chat(system, user, model=model, json_mode=True)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage = raw_resp.get("usage") or {}
        prompt_tok = usage.get("prompt_tokens")
        completion_tok = usage.get("completion_tokens")
        total_tok = usage.get("total_tokens")
        parsed = json.loads(raw_text)
        # The LLM is responding about the redacted patient token; rehydrate
        # the original IDs on the structured result so downstream code can
        # still find the patient row in our local DB.
        parsed["patientId"] = patient_id
        parsed.setdefault("documentId", document_id)
        rec = AICallRecord(
            call_type="extract",
            model=model,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
            total_tokens=total_tok,
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                load_rates(model),
                prompt_tokens=prompt_tok or 0,
                completion_tokens=completion_tok or 0,
            ),
            raw_response=raw_resp,
            error=None,
            job_id=job_id,
            patient_id=patient_id,
            document_id=document_id,
        )
        _log_redaction("extract", _provider_label(self.base_url), deid)
        _persist_ai_call(
            rec,
            valid=True,
            validation_errors=[],
            deidentified=not deid.disabled(),
            redaction_counts=deid.counts_snapshot(),
        )
        return parsed, rec

    async def suggest_coding(
        self,
        *,
        patient_facts: dict[str, Any],
        standards: list[str],
        job_id: str | None = None,
        patient_id: str | None = None,
    ) -> tuple[dict[str, Any], AICallRecord]:
        deid = deidentifier_for(self.settings)
        safe_facts = deid.pseudonymize_facts(patient_facts)
        system = CODING_SUGGEST_SYSTEM
        user = (
            "Patient structured facts:\n"
            + json.dumps(safe_facts, default=str)
            + f"\n\nStandards enabled: {standards}\n"
            "Return JSON with keys: primaryDiagnosis, secondaryDiagnoses, complications, comorbidities, "
            "codingCandidates, evidence, warnings."
        )
        model = self._model_for("coding")
        t0 = time.perf_counter()
        raw_text, raw_resp = await self._chat(system, user, model=model, json_mode=True)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage = raw_resp.get("usage") or {}
        prompt_tok = usage.get("prompt_tokens")
        completion_tok = usage.get("completion_tokens")
        total_tok = usage.get("total_tokens")
        parsed = json.loads(raw_text)
        rec = AICallRecord(
            call_type="coding",
            model=model,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
            total_tokens=total_tok,
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                load_rates(model),
                prompt_tokens=prompt_tok or 0,
                completion_tokens=completion_tok or 0,
            ),
            raw_response=raw_resp,
            error=None,
            job_id=job_id,
            patient_id=patient_id,
            document_id=None,
        )
        _log_redaction("coding", _provider_label(self.base_url), deid)
        _persist_ai_call(
            rec,
            valid=True,
            validation_errors=[],
            deidentified=not deid.disabled(),
            redaction_counts=deid.counts_snapshot(),
        )
        return parsed, rec

    async def summarize(
        self,
        *,
        patient_facts: dict[str, Any],
        summary_type: str,
        job_id: str | None = None,
        patient_id: str | None = None,
    ) -> tuple[str, AICallRecord]:
        deid = deidentifier_for(self.settings)
        safe_facts = deid.pseudonymize_facts(patient_facts)
        system = summary_system_for(summary_type)
        user = "Structured facts:\n" + json.dumps(safe_facts, default=str)
        model = self._model_for("summary")
        t0 = time.perf_counter()
        raw_text, raw_resp = await self._chat(system, user, model=model, json_mode=False)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage = raw_resp.get("usage") or {}
        prompt_tok = usage.get("prompt_tokens")
        completion_tok = usage.get("completion_tokens")
        total_tok = usage.get("total_tokens")
        rec = AICallRecord(
            call_type="summary",
            model=model,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
            total_tokens=total_tok,
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                load_rates(model),
                prompt_tokens=prompt_tok or 0,
                completion_tokens=completion_tok or 0,
            ),
            raw_response=raw_resp,
            error=None,
            job_id=job_id,
            patient_id=patient_id,
            document_id=None,
        )
        _log_redaction("summary", _provider_label(self.base_url), deid)
        _persist_ai_call(
            rec,
            valid=True,
            validation_errors=[],
            deidentified=not deid.disabled(),
            redaction_counts=deid.counts_snapshot(),
        )
        return raw_text, rec

    async def embed(
        self,
        text_in: str,
        *,
        job_id: str | None = None,
        patient_id: str | None = None,
        ref_id: str | None = None,
    ) -> tuple[list[float], AICallRecord]:
        deid = deidentifier_for(self.settings)
        safe_text = deid.redact_text(text_in, lang="auto")
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.base_url}/embeddings",
                json={"model": self.embedding_model, "input": safe_text},
                headers=self.headers,
            )
            r.raise_for_status()
            data = r.json()
        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage = data.get("usage") or {}
        embed_tok = usage.get("prompt_tokens")
        vec = data["data"][0]["embedding"]
        rec = AICallRecord(
            call_type="embed",
            model=self.embedding_model,
            prompt_tokens=embed_tok,
            completion_tokens=None,
            total_tokens=embed_tok,
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                load_rates(self.embedding_model),
                embedding_tokens=embed_tok or 0,
            ),
            raw_response={"usage": usage, "ref_id": ref_id},
            error=None,
            job_id=job_id,
            patient_id=patient_id,
            document_id=None,
        )
        _log_redaction("embed", _provider_label(self.base_url), deid)
        _persist_ai_call(
            rec,
            valid=True,
            validation_errors=[],
            deidentified=not deid.disabled(),
            redaction_counts=deid.counts_snapshot(),
        )
        return vec, rec

    async def rag_ask(
        self,
        *,
        question: str,
        chunks: list[dict[str, Any]],
        history: list[dict[str, str]] | None = None,
        patient_id: str | None = None,
    ) -> tuple[str, AICallRecord]:
        deid = deidentifier_for(self.settings)
        safe_question = deid.redact_text(question, lang="auto")
        safe_chunks = [
            {**c, "content": deid.redact_text((c.get("content") or ""), lang="auto")}
            for c in chunks
        ]
        safe_history: list[dict[str, str]] | None = None
        if history:
            safe_history = [
                {"role": t.get("role", "user"), "content": deid.redact_text(t.get("content", ""), lang="auto")}
                for t in history
            ]

        system = RAG_SYSTEM
        excerpts = "\n".join(
            f"[{i + 1}] {(c.get('content') or '').strip()}"
            for i, c in enumerate(safe_chunks)
        )
        user = (
            f"Question: {safe_question}\n\n"
            f"Relevant excerpts from this patient's notes:\n{excerpts}\n\n"
            "Answer the question using ONLY the excerpts. Cite as [N]."
        )

        # Compose messages: system, then optional history, then the new user turn.
        payload_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        if safe_history:
            for turn in safe_history:
                payload_messages.append({"role": turn["role"], "content": turn["content"]})
        payload_messages.append({"role": "user", "content": user})

        model = self._model_for("rag")
        t0 = time.perf_counter()
        payload: dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "temperature": 0.0,
        }
        if "openrouter.ai" in (self.base_url or ""):
            payload["provider"] = {"ignore": ["WandB"], "allow_fallbacks": True}
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload, headers=self.headers,
            )
            r.raise_for_status()
            data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
        if not content:
            raise RuntimeError(
                f"Empty completion from {data.get('provider') or 'upstream'} for model={model}; "
                f"finish_reason={(data.get('choices') or [{}])[0].get('finish_reason')!r}"
            )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage = data.get("usage") or {}
        rec = AICallRecord(
            call_type="rag",
            model=model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                load_rates(model),
                prompt_tokens=usage.get("prompt_tokens") or 0,
                completion_tokens=usage.get("completion_tokens") or 0,
            ),
            raw_response=data,
            error=None,
            job_id=None,
            patient_id=patient_id,
            document_id=None,
        )
        _log_redaction("rag", _provider_label(self.base_url), deid)
        _persist_ai_call(
            rec,
            valid=True,
            validation_errors=[],
            deidentified=not deid.disabled(),
            redaction_counts=deid.counts_snapshot(),
        )
        return content, rec


# Alias for backwards-compatibility and plan sanity-check imports
OpenAIProvider = OpenAICompatibleProvider


def get_ai_provider(settings: Settings | None = None) -> AIProvider:
    s = settings or effective_settings()
    if s.AI_PROVIDER == "mock":
        return MockProvider()
    return OpenAICompatibleProvider(s)
