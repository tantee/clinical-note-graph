from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import httpx

from app.config import Settings
from app.prompts.templates import (
    CODING_SUGGEST_SYSTEM,
    EXTRACTION_SYSTEM,
    EXTRACTION_USER,
    SUMMARY_SYSTEM,
)
from app.schemas.extraction import ClinicalExtractionResult
from app.services.runtime_config import effective as effective_settings


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
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    async def suggest_coding(self, *, patient_facts: dict[str, Any], standards: list[str]) -> dict[str, Any]:
        ...

    @abstractmethod
    async def summarize(self, *, patient_facts: dict[str, Any], summary_type: str) -> str:
        ...

    async def embed(self, text: str) -> list[float]:
        return []  # Providers that don't support embeddings return empty so the caller skips writes.


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


class MockProvider(AIProvider):
    async def extract(
        self,
        *,
        patient_id: str,
        encounter_type: str,
        encounter_dt: str,
        document_id: str,
        content: str,
    ) -> dict[str, Any]:
        return mock_extract(content, patient_id=patient_id, encounter_id=None, document_id=document_id)

    async def suggest_coding(self, *, patient_facts: dict[str, Any], standards: list[str]) -> dict[str, Any]:
        # Convert existing facts into candidate codes deterministically.
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

        # pick primary = highest-confidence diagnosis
        diagnoses.sort(key=lambda d: -d.get("confidence", 0.0))
        primary = diagnoses[0] if diagnoses else None
        if primary:
            primary = {**primary, "role": "primary"}
        secondary = [
            {**d, "role": "secondary"} for d in diagnoses[1:]
        ]
        return {
            "primaryDiagnosis": primary,
            "secondaryDiagnoses": secondary,
            "complications": [],
            "comorbidities": [],
            "codingCandidates": candidates,
            "evidence": [
                {"condition": d["condition"], "evidence": d.get("evidenceText")} for d in diagnoses if d.get("evidenceText")
            ],
            "warnings": [],
        }

    async def summarize(self, *, patient_facts: dict[str, Any], summary_type: str) -> str:
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
                lines.append(f"- {m.get('name')} — {m.get('action', 'start')}{(' (' + m.get('indication') + ')') if m.get('indication') else ''}")
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


# ----------------------------- OPENAI-COMPATIBLE PROVIDER -----------------------------


class OpenAICompatibleProvider(AIProvider):
    """Works with OpenAI's API or any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM, etc.)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        base = (settings.AI_BASE_URL or "https://api.openai.com/v1").strip().rstrip("/")
        self.base_url = base
        self.model = settings.AI_MODEL
        self.embedding_model = settings.AI_EMBEDDING_MODEL
        self.headers = {"Content-Type": "application/json"}
        if settings.AI_API_KEY:
            self.headers["Authorization"] = f"Bearer {settings.AI_API_KEY}"

    async def _chat(self, system: str, user: str, *, json_mode: bool = True) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=self.headers)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]

    async def extract(
        self,
        *,
        patient_id: str,
        encounter_type: str,
        encounter_dt: str,
        document_id: str,
        content: str,
    ) -> dict[str, Any]:
        system = EXTRACTION_SYSTEM + "\n\nSchema:\n" + json.dumps(
            ClinicalExtractionResult.model_json_schema()
        )
        user = EXTRACTION_USER.format(
            patient_id=patient_id,
            encounter_type=encounter_type,
            encounter_dt=encounter_dt,
            document_id=document_id,
            content=content,
        )
        raw = await self._chat(system, user, json_mode=True)
        parsed = json.loads(raw)
        # Make sure patientId/documentId are set even if model omitted
        parsed.setdefault("patientId", patient_id)
        parsed.setdefault("documentId", document_id)
        return parsed

    async def suggest_coding(self, *, patient_facts: dict[str, Any], standards: list[str]) -> dict[str, Any]:
        system = CODING_SUGGEST_SYSTEM
        user = (
            "Patient structured facts:\n"
            + json.dumps(patient_facts, default=str)
            + f"\n\nStandards enabled: {standards}\n"
            "Return JSON with keys: primaryDiagnosis, secondaryDiagnoses, complications, comorbidities, "
            "codingCandidates, evidence, warnings."
        )
        raw = await self._chat(system, user, json_mode=True)
        return json.loads(raw)

    async def summarize(self, *, patient_facts: dict[str, Any], summary_type: str) -> str:
        system = SUMMARY_SYSTEM.format(summary_type=summary_type)
        user = "Structured facts:\n" + json.dumps(patient_facts, default=str)
        return await self._chat(system, user, json_mode=False)

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.base_url}/embeddings",
                json={"model": self.embedding_model, "input": text},
                headers=self.headers,
            )
            r.raise_for_status()
            data = r.json()
            return data["data"][0]["embedding"]


def get_ai_provider(settings: Settings | None = None) -> AIProvider:
    s = settings or effective_settings()
    if s.AI_PROVIDER == "mock":
        return MockProvider()
    return OpenAICompatibleProvider(s)
