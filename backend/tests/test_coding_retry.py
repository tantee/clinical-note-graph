"""Coding-retry regression tests.

When the upstream LLM returns no primary diagnosis AND no coding
candidates despite the patient having problems, services.coding.suggest_coding
should re-prompt once with an explicit reminder. This prevents the
'model punts on complex case, user sees empty card' UX hit observed on
patient 5468426 with DeepSeek R1.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest


class _FakeAICallRecord:
    """Minimal stand-in for AICallRecord — services.coding only uses .model /
    .cost_usd / .latency_ms when persisting."""

    def __init__(self, model: str = "test-model") -> None:
        self.model = model
        self.cost_usd = None
        self.latency_ms = 1


@dataclass
class _FakeProvider:
    """Returns whatever's set up in `responses`, one entry per call. Records
    every (patient_facts, standards, system_addendum) tuple so the test
    can assert on the retry's prompt context."""
    responses: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def suggest_coding(self, *, patient_facts, standards,
                             job_id=None, patient_id=None, system_addendum=""):
        self.calls.append({
            "patient_facts": patient_facts,
            "standards": standards,
            "system_addendum": system_addendum,
        })
        raw = self.responses.pop(0) if self.responses else {}
        return raw, _FakeAICallRecord()


@pytest.fixture()
def patched_provider(fake_store, monkeypatch):
    """Wire a fake provider into services.coding.get_ai_provider and seed
    a single patient with problems so the retry path is exercised."""
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Test"}
    fake_store.facts.extend([
        {"id": "f-1", "patient_id": "HN1", "encounter_id": "E1",
         "type": "condition", "value": "Type 2 diabetes mellitus",
         "normalized_code": "E11.9", "review_status": "ai_suggested",
         "extra": {}, "confidence": 0.9},
    ])
    fp = _FakeProvider()
    import app.services.coding as coding_mod
    monkeypatch.setattr(coding_mod, "get_ai_provider", lambda: fp)
    return fp


def test_retries_once_when_model_punts_with_no_codes(patched_provider):
    """First call returns nothing → service must retry with a non-empty
    system_addendum and persist the retry's result."""
    patched_provider.responses = [
        # First call — model declined to commit to anything.
        {"primaryDiagnosis": None, "secondaryDiagnoses": [],
         "codingCandidates": [], "warnings": ["case is complex"]},
        # Retry — model commits.
        {"primaryDiagnosis": {"condition": "Type 2 diabetes mellitus",
                              "icd10": "E11.9", "snomed": "44054006",
                              "confidence": 0.5, "role": "primary"},
         "secondaryDiagnoses": [], "complications": [], "comorbidities": [],
         "codingCandidates": [{"system": "ICD10", "code": "E11.9",
                                "display": "Type 2 DM", "forCondition": "Type 2 diabetes mellitus",
                                "confidence": 0.5}],
         "warnings": ["primary chosen on tie-break"]},
    ]
    from app.services.coding import suggest_coding
    from app.schemas.coding import CodingSuggestRequest

    resp = asyncio.run(suggest_coding(
        "HN1", CodingSuggestRequest(standards=["ICD10"], includeEvidence=False),
    ))

    assert len(patched_provider.calls) == 2, "service must call the provider twice"
    assert patched_provider.calls[0]["system_addendum"] == ""
    assert patched_provider.calls[1]["system_addendum"], (
        "second call must carry a non-empty addendum so the model sees the rule"
    )
    assert "previous response" in patched_provider.calls[1]["system_addendum"].lower(), (
        "addendum should explicitly reference the prior failed attempt"
    )
    assert resp.primaryDiagnosis is not None
    assert resp.primaryDiagnosis.icd10 == "E11.9"
    assert len(resp.codingCandidates) == 1


def test_no_retry_when_model_returns_any_code(patched_provider):
    """If the model commits to even one candidate, the service uses that
    result directly — no extra round-trip / cost."""
    patched_provider.responses = [
        {"primaryDiagnosis": {"condition": "Type 2 diabetes mellitus",
                              "icd10": "E11.9", "confidence": 0.7,
                              "role": "primary"},
         "secondaryDiagnoses": [], "complications": [], "comorbidities": [],
         "codingCandidates": [{"system": "ICD10", "code": "E11.9",
                                "display": "Type 2 DM",
                                "forCondition": "Type 2 diabetes mellitus",
                                "confidence": 0.7}],
         "warnings": []},
    ]
    from app.services.coding import suggest_coding
    from app.schemas.coding import CodingSuggestRequest

    resp = asyncio.run(suggest_coding(
        "HN1", CodingSuggestRequest(standards=["ICD10"], includeEvidence=False),
    ))

    assert len(patched_provider.calls) == 1, "expected single call when first response has codes"
    assert resp.primaryDiagnosis is not None


def test_no_retry_when_patient_has_no_problems(fake_store, monkeypatch):
    """If the patient has no problems / diagnoses at all, an empty response
    is legitimate — don't burn an extra AI call for nothing."""
    fake_store.patients["HN-NO-PROBS"] = {"patient_id": "HN-NO-PROBS", "name": "Empty"}
    fp = _FakeProvider()
    import app.services.coding as coding_mod
    monkeypatch.setattr(coding_mod, "get_ai_provider", lambda: fp)
    fp.responses = [
        {"primaryDiagnosis": None, "secondaryDiagnoses": [],
         "codingCandidates": [], "warnings": []},
    ]
    from app.services.coding import suggest_coding
    from app.schemas.coding import CodingSuggestRequest

    asyncio.run(suggest_coding(
        "HN-NO-PROBS", CodingSuggestRequest(standards=["ICD10"], includeEvidence=False),
    ))
    assert len(fp.calls) == 1, "empty problem list should not trigger a retry"
