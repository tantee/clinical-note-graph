"""End-to-end de-identification tests at the AI-provider layer.

The OpenAI-compatible provider must redact PHI from every outbound payload.
Each call type gets one test that:

1. Stubs httpx so the test stays offline.
2. Captures the actual JSON body sent to the (fake) provider.
3. Asserts the body contains no patient name, HN, exact date, email, or
   Thai national ID — even when the inputs do.
4. Asserts `ai_outputs.deidentified` is True and `redaction_counts` is
   populated for the row written.

The tests stub `_get_presidio_analyzer` with a tiny in-test fake so the suite
doesn't need the +500 MB Presidio + spaCy install — the production path is
identical, only the model implementation is replaced.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from decimal import Decimal

import httpx
import pytest


# ---------------------------------------------------------------------------
# Fake Presidio analyzer — drop-in for the e2e tests.
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    """Minimal stand-in for Presidio's RecognizerResult."""

    entity_type: str
    start: int
    end: int
    score: float = 0.95


class _FakeAnalyzer:
    """Catches the patient/provider names we plant in fixtures, plus a few
    obvious tokens, so the e2e tests can validate the full safe_harbor
    pipeline without pulling Presidio + spaCy into the dev environment.

    Production code path is unchanged — we only swap the implementation in
    via monkeypatch on the module's lazy loader."""

    _NAMES = (
        "Somchai Sample",
        "Dr Anan Wong",
        "Anan Wong",
    )

    def analyze(self, *, text: str, language: str = "en", score_threshold: float = 0.5):
        results: list[_FakeResult] = []
        for name in self._NAMES:
            for m in re.finditer(re.escape(name), text):
                results.append(_FakeResult(entity_type="PERSON", start=m.start(), end=m.end()))
        return results


@pytest.fixture(autouse=True)
def _stub_presidio(monkeypatch):
    """Activate the fake analyzer for every test in this module."""
    import app.services.deidentify as deid_mod
    monkeypatch.setattr(deid_mod, "_get_presidio_analyzer", lambda: _FakeAnalyzer())
    monkeypatch.setattr(deid_mod, "_get_pythainlp_ner", lambda: None)
    yield


def _seed_pricing(fake_store):
    fake_store.pricing["gpt-4o-mini"] = {
        "model": "gpt-4o-mini",
        "prompt_per_1m": Decimal("0.15"),
        "completion_per_1m": Decimal("0.60"),
        "embedding_per_1m": None,
        "source": "seed",
        "updated_at": "now",
    }
    fake_store.pricing["text-embedding-3-small"] = {
        "model": "text-embedding-3-small",
        "prompt_per_1m": None,
        "completion_per_1m": None,
        "embedding_per_1m": Decimal("0.02"),
        "source": "seed",
        "updated_at": "now",
    }


def _make_settings():
    from app.config import Settings
    return Settings(
        AI_PROVIDER="openai",
        AI_BASE_URL="https://test/v1",
        AI_API_KEY="k",
        AI_MODEL="gpt-4o-mini",
        AI_EMBEDDING_MODEL="text-embedding-3-small",
        DEIDENTIFY_LEVEL="safe_harbor",
        DEIDENTIFY_NER_THRESHOLD=0.5,
    )


def _stub_chat(monkeypatch, captured, response_content):
    """Patch httpx.AsyncClient.post for a chat completion. `response_content` is
    the string the fake provider returns in `choices[0].message.content`."""

    async def fake_post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["body"] = json
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": response_content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


def _assert_no_phi_in_payload(payload_dict, *, phi_strings):
    """Walk the payload JSON; assert none of `phi_strings` appears anywhere."""
    serialised = json.dumps(payload_dict, default=str)
    for s in phi_strings:
        assert s not in serialised, (
            f"PHI {s!r} leaked into outbound payload:\n{serialised[:500]}"
        )


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


def test_extract_redacts_content_and_persists_audit(monkeypatch, fake_store, isolated_vault):
    from app.services.ai_provider import OpenAICompatibleProvider

    _seed_pricing(fake_store)
    captured: dict = {}
    minimal_extraction = json.dumps({
        "patientId": "PATIENT-A1",
        "summary": "ok",
        "problems": [], "medications": [], "observations": [],
        "procedures": [], "allergies": [], "plans": [],
        "diagnoses": [], "codingCandidates": [],
        "graphUpdates": [], "markdownUpdates": [], "warnings": [],
    })
    _stub_chat(monkeypatch, captured, minimal_extraction)

    settings = _make_settings()
    p = OpenAICompatibleProvider(settings)

    content = (
        "Patient Somchai Sample (HN-DEMO-1), DOB 1962-03-04. "
        "Seen on 2026-05-19 by Dr Anan Wong. "
        "Phone 081-234-5678, email somchai@example.com. "
        "Thai national ID 1101700230708. "
        "Diagnosis: type 2 diabetes."
    )

    out, rec = asyncio.run(p.extract(
        patient_id="HN-DEMO-1",
        encounter_type="visit",
        encounter_dt="2026-05-19T14:03",
        document_id="D1",
        content=content,
        job_id=None,
    ))

    # The outbound body must not contain real PHI.
    _assert_no_phi_in_payload(captured["body"], phi_strings=[
        "Somchai Sample",
        "HN-DEMO-1",
        "1962-03-04",
        "2026-05-19",
        "081-234-5678",
        "somchai@example.com",
        "1101700230708",
    ])

    # The parsed extraction has the real patientId restored so downstream
    # graph code can still find the row.
    assert out["patientId"] == "HN-DEMO-1"

    # Audit row written with the new columns populated.
    row = fake_store.ai_outputs[-1]
    assert row["deidentified"] is True
    assert row["redaction_counts"], "expected redaction_counts to be populated"
    assert isinstance(row["redaction_counts"], dict)
    # Email + phone + HN + date should all have been caught.
    rc = row["redaction_counts"]
    assert rc.get("EMAIL_ADDRESS", 0) >= 1
    assert rc.get("PHONE_NUMBER", 0) >= 1
    assert rc.get("DATE_TIME", 0) >= 1


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------


def test_summarize_redacts_patient_facts(monkeypatch, fake_store, isolated_vault):
    from app.services.ai_provider import OpenAICompatibleProvider

    _seed_pricing(fake_store)
    captured: dict = {}
    _stub_chat(monkeypatch, captured, "# Summary\n- some text")

    settings = _make_settings()
    p = OpenAICompatibleProvider(settings)

    facts = {
        "patient": {
            "patient_id": "HN-DEMO-1",
            "name": "Somchai Sample",
            "birth_date": "1962-03-04",
        },
        "encounters": [
            {"encounter_id": "E1", "received_at": "2026-05-19T14:03"},
        ],
        "problems": [
            {
                "value": "Type 2 diabetes",
                "evidenceText": "Somchai Sample noted with hyperglycemia on 2026-05-19.",
            }
        ],
    }

    md, rec = asyncio.run(p.summarize(
        patient_facts=facts,
        summary_type="brief",
        job_id=None,
        patient_id="HN-DEMO-1",
    ))

    _assert_no_phi_in_payload(captured["body"], phi_strings=[
        "Somchai Sample",
        "HN-DEMO-1",
        "1962-03-04",
        "2026-05-19",
    ])

    row = fake_store.ai_outputs[-1]
    assert row["call_type"] == "summary"
    assert row["deidentified"] is True


# ---------------------------------------------------------------------------
# suggest_coding
# ---------------------------------------------------------------------------


def test_suggest_coding_redacts_facts(monkeypatch, fake_store, isolated_vault):
    from app.services.ai_provider import OpenAICompatibleProvider

    _seed_pricing(fake_store)
    captured: dict = {}
    _stub_chat(monkeypatch, captured, json.dumps({
        "primaryDiagnosis": None,
        "secondaryDiagnoses": [],
        "complications": [],
        "comorbidities": [],
        "codingCandidates": [],
        "evidence": [],
        "warnings": [],
    }))

    settings = _make_settings()
    p = OpenAICompatibleProvider(settings)

    facts = {
        "patient": {
            "patient_id": "HN-DEMO-1",
            "name": "Somchai Sample",
        },
        "problems": [
            {
                "value": "Pneumonia",
                "evidenceText": "Pt Somchai Sample, contact a@b.com, on 2026-05-19.",
            }
        ],
    }

    parsed, rec = asyncio.run(p.suggest_coding(
        patient_facts=facts,
        standards=["ICD10"],
        job_id=None,
        patient_id="HN-DEMO-1",
    ))

    _assert_no_phi_in_payload(captured["body"], phi_strings=[
        "Somchai Sample",
        "HN-DEMO-1",
        "a@b.com",
        "2026-05-19",
    ])

    row = fake_store.ai_outputs[-1]
    assert row["call_type"] == "coding"
    assert row["deidentified"] is True


# ---------------------------------------------------------------------------
# embed
# ---------------------------------------------------------------------------


def test_embed_redacts_input(monkeypatch, fake_store, isolated_vault):
    from app.services.ai_provider import OpenAICompatibleProvider

    _seed_pricing(fake_store)
    captured: dict = {}

    async def fake_post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["body"] = json
        return httpx.Response(
            200,
            json={
                "data": [{"embedding": [0.1, 0.2, 0.3]}],
                "usage": {"prompt_tokens": 10},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    settings = _make_settings()
    p = OpenAICompatibleProvider(settings)
    vec, rec = asyncio.run(p.embed(
        "Patient Somchai Sample (HN-DEMO-1), email a@b.com",
        job_id=None, patient_id="HN-DEMO-1", ref_id="r1",
    ))
    assert vec == [0.1, 0.2, 0.3]

    _assert_no_phi_in_payload(captured["body"], phi_strings=[
        "Somchai Sample",
        "HN-DEMO-1",
        "a@b.com",
    ])

    row = fake_store.ai_outputs[-1]
    assert row["call_type"] == "embed"
    assert row["deidentified"] is True


# ---------------------------------------------------------------------------
# Hierarchical payload (gather_patient_facts_for_ai shape — issue #27 Part B)
# ---------------------------------------------------------------------------


def test_summarize_redacts_hierarchical_payload(monkeypatch, fake_store, isolated_vault):
    """Compliance audit (#11 invariant) on the new compressed payload shape.

    When an encounter has a persisted summary, gather_patient_facts_for_ai
    ships its markdown body in place of the raw facts. The redactor must
    still walk into that markdown and scrub PHI before the HTTP call lands.
    """
    from app.services.ai_provider import OpenAICompatibleProvider

    _seed_pricing(fake_store)
    captured: dict = {}
    _stub_chat(monkeypatch, captured, "# Summary\n- ok")

    settings = _make_settings()
    p = OpenAICompatibleProvider(settings)

    # Mirrors the gather_patient_facts_for_ai output: patient header +
    # encounters list with both representations. PHI is planted in:
    # - patient.name (PATIENT_NAME field)
    # - chronicProblems[0].evidenceText (string body)
    # - encounters[0].provider (PROVIDER_NAME field)
    # - encounters[0].markdown (string body, collapsed-summary path)
    # - encounters[1].problems[*].evidenceText (string body, raw_facts path)
    facts = {
        "patient": {
            "patient_id": "HN-DEMO-1",
            "name": "Somchai Sample",
            "birth_date": "1962-03-04",
        },
        "allergies": [{"value": "Penicillin", "evidenceText": "told us at admit"}],
        "chronicProblems": [
            {"value": "Type 2 diabetes",
             "evidenceText": "Somchai Sample, dx 2026-05-19"},
        ],
        "encounters": [
            {
                "encounterId": "E1", "type": "admission",
                "dateTime": "2026-05-19T14:03",
                "department": "IM", "provider": "Dr Anan Wong",
                "representation": "summary",
                "markdown": (
                    "# Discharge summary\n"
                    "- Somchai Sample (HN-DEMO-1)\n"
                    "- Contact a@b.com on 2026-05-19\n"
                    "- Seen by Dr Anan Wong"
                ),
            },
            {
                "encounterId": "E2", "type": "visit",
                "dateTime": "2026-06-01T09:00",
                "department": "OPD", "provider": "Dr Anan Wong",
                "representation": "raw_facts",
                "problems": [{
                    "value": "Hyperglycemia",
                    "evidenceText": "Somchai Sample seen 2026-06-01 by Dr Anan Wong",
                }],
                "medications": [], "observations": [], "procedures": [],
                "plans": [], "diagnoses": [], "codingCandidates": [],
            },
        ],
    }

    md, rec = asyncio.run(p.summarize(
        patient_facts=facts, summary_type="brief",
        job_id=None, patient_id="HN-DEMO-1",
    ))

    _assert_no_phi_in_payload(captured["body"], phi_strings=[
        "Somchai Sample",
        "HN-DEMO-1",
        "Anan Wong",
        "a@b.com",
        "1962-03-04",
        "2026-05-19",
        "2026-06-01",
    ])
    row = fake_store.ai_outputs[-1]
    assert row["call_type"] == "summary"
    assert row["deidentified"] is True
    rc = row["redaction_counts"] or {}
    assert sum(rc.values()) > 0, "expected at least one redaction on the compressed payload"


# ---------------------------------------------------------------------------
# rag_ask
# ---------------------------------------------------------------------------


def test_rag_ask_redacts_question_and_chunks(monkeypatch, fake_store, isolated_vault):
    from app.services.ai_provider import OpenAICompatibleProvider

    _seed_pricing(fake_store)
    captured: dict = {}
    _stub_chat(monkeypatch, captured, "Some answer [1].")

    settings = _make_settings()
    p = OpenAICompatibleProvider(settings)

    chunks = [
        {"content": "Somchai Sample (HN-DEMO-1) seen 2026-05-19; email a@b.com"},
    ]
    answer, rec = asyncio.run(p.rag_ask(
        question="What did Somchai Sample take for HN-DEMO-1?",
        chunks=chunks,
        history=None,
        patient_id="HN-DEMO-1",
    ))

    _assert_no_phi_in_payload(captured["body"], phi_strings=[
        "Somchai Sample",
        "HN-DEMO-1",
        "a@b.com",
        "2026-05-19",
    ])

    row = fake_store.ai_outputs[-1]
    assert row["call_type"] == "rag"
    assert row["deidentified"] is True


# ---------------------------------------------------------------------------
# off-level escape hatch
# ---------------------------------------------------------------------------


def test_off_level_skips_redaction(monkeypatch, fake_store, isolated_vault):
    """`DEIDENTIFY_LEVEL=off` must let PHI through verbatim (BAA-bound path)."""
    from app.services.ai_provider import OpenAICompatibleProvider
    from app.config import Settings

    _seed_pricing(fake_store)
    captured: dict = {}
    _stub_chat(monkeypatch, captured, "# Summary\nnone")

    settings = Settings(
        AI_PROVIDER="openai",
        AI_BASE_URL="https://test/v1",
        AI_API_KEY="k",
        AI_MODEL="gpt-4o-mini",
        DEIDENTIFY_LEVEL="off",
    )
    p = OpenAICompatibleProvider(settings)
    facts = {"patient": {"name": "Somchai Sample", "patient_id": "HN-DEMO-1"}}
    _, _ = asyncio.run(p.summarize(
        patient_facts=facts,
        summary_type="brief",
        patient_id="HN-DEMO-1",
    ))

    body = json.dumps(captured["body"])
    assert "Somchai Sample" in body
    assert "HN-DEMO-1" in body

    row = fake_store.ai_outputs[-1]
    assert row["deidentified"] is False
