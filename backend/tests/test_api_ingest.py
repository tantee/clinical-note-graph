from __future__ import annotations

import json
from datetime import datetime


def _admission_payload() -> dict:
    return {
        "patient": {"patientId": "HN1", "name": "Sample", "gender": "male", "birthDate": "1965-04-12"},
        "encounter": {"type": "admission", "dateTime": "2026-05-15T10:00:00+07:00", "department": "IM", "provider": "Dr"},
        "format": "text",
        "content": (
            "Admission note. Patient has Type 2 diabetes mellitus and hypertension. "
            "HbA1c 8.4 % on admission. BP 152/95. Start metformin 500mg bid. Plan: cardiology consult."
        ),
        "source": {"system": "HIS", "documentId": "doc-001", "version": "1"},
    }


def test_ingest_text_round_trip(app_client, fake_store):
    r = app_client.post("/api/emr/ingest?async=false", json=_admission_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["patientId"] == "HN1"
    assert body["documentId"] == "doc-001"
    assert body["status"] == "completed"

    # Patient row was written
    assert fake_store.patients["HN1"]["name"] == "Sample"
    # Document raw kept verbatim
    assert "Type 2 diabetes" in fake_store.documents["doc-001"]["raw_content"]
    # Facts persisted
    types = {f["type"] for f in fake_store.facts}
    assert {"condition", "medication", "observation"}.issubset(types)


def test_ingest_idempotent(app_client, fake_store):
    app_client.post("/api/emr/ingest?async=false", json=_admission_payload())
    facts_before = len(fake_store.facts)
    app_client.post("/api/emr/ingest?async=false", json=_admission_payload())
    # Same (patientId, source.documentId, version) — document upserts, facts append (history).
    assert fake_store.documents["doc-001"]["document_id"] == "doc-001"
    assert len(fake_store.facts) >= facts_before


def test_get_patient_404_when_missing(app_client):
    r = app_client.get("/api/patient/UNKNOWN")
    assert r.status_code == 404


def test_get_patient_aggregates(app_client):
    app_client.post("/api/emr/ingest?async=false", json=_admission_payload())
    r = app_client.get("/api/patient/HN1")
    assert r.status_code == 200
    facts = r.json()
    assert facts["patient"]["patient_id"] == "HN1"
    assert any("diabetes" in p["value"].lower() for p in facts["problems"])


def test_encounter_documents_endpoint(app_client):
    app_client.post("/api/emr/ingest?async=false", json=_admission_payload())
    timeline = app_client.get("/api/patient/HN1/timeline").json()
    encounter_id = timeline["encounters"][0]["encounter_id"]
    docs = app_client.get(f"/api/patient/HN1/encounter/{encounter_id}/documents").json()
    assert docs["documents"][0]["document_id"] == "doc-001"


def test_review_fact_audit_payload_safe(app_client, fake_store):
    app_client.post("/api/emr/ingest?async=false", json=_admission_payload())
    fact_id = fake_store.facts[0]["id"]
    # The endpoint expects a real UUID, so we use a fixed shape that our fake store
    # echoes back. Validate the input rejection on invalid status:
    r = app_client.patch("/api/facts/00000000-0000-0000-0000-000000000000/review?status=bogus")
    assert r.status_code == 422


def test_config_patch_persists(app_client, fake_store):
    r = app_client.patch("/api/config", json={"AI_PROVIDER": "openai", "AI_MODEL": "gpt-4o"})
    assert r.status_code == 200
    assert "AI_PROVIDER" in r.json()["updated"]
    assert fake_store.config["AI_PROVIDER"] == "openai"
    # GET reflects the override
    cfg = app_client.get("/api/config").json()
    assert cfg["settings"]["AI_PROVIDER"] == "openai"


def test_config_api_key_is_masked(app_client, fake_store):
    fake_store.config["AI_API_KEY"] = "sk-supersecret"
    r = app_client.get("/api/config").json()
    assert r["settings"]["AI_API_KEY"].startswith("***")
    assert "supersecret" not in r["settings"]["AI_API_KEY"]


def test_invalid_payload_returns_422(app_client):
    r = app_client.post("/api/emr/ingest", json={"patient": {}})
    assert r.status_code == 422


def test_summary_endpoint(app_client):
    app_client.post("/api/emr/ingest?async=false", json=_admission_payload())
    r = app_client.post("/api/patient/HN1/summary", json={"type": "brief", "includeEvidence": False})
    assert r.status_code == 200
    body = r.json()
    assert "Patient Summary" in body["markdown"] or "Type 2" in body["markdown"]


def test_coding_suggest_endpoint(app_client):
    app_client.post("/api/emr/ingest?async=false", json=_admission_payload())
    r = app_client.post("/api/patient/HN1/coding/suggest", json={"standards": ["ICD10", "SNOMEDCT"]})
    assert r.status_code == 200
    body = r.json()
    assert body["disclaimer"].startswith("AI-assisted")


def test_export_fhir_bundle(app_client):
    app_client.post("/api/emr/ingest?async=false", json=_admission_payload())
    r = app_client.post("/api/export", json={"patientId": "HN1", "exportType": "fhir_bundle"})
    assert r.status_code == 200
    bundle = r.json()["data"]
    assert bundle["resourceType"] == "Bundle"
    rtypes = {e["resource"]["resourceType"] for e in bundle["entry"]}
    assert "Patient" in rtypes
    assert "Condition" in rtypes


def test_health_and_ready(app_client):
    assert app_client.get("/health").status_code == 200
    # /ready hits a real engine; we just verify the route exists.
    assert app_client.get("/ready").status_code in (200, 503)


def test_api_key_enforced_when_configured(monkeypatch, app_client):
    # Re-create the app with API_KEY set so the middleware enforces it
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("API_KEY", "test-key")
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app()) as client:
        r = client.post("/api/emr/ingest?async=false", json=_admission_payload())
        assert r.status_code == 401
        r = client.post("/api/emr/ingest?async=false", json=_admission_payload(), headers={"X-API-Key": "wrong"})
        assert r.status_code == 401
        r = client.post("/api/emr/ingest?async=false", json=_admission_payload(), headers={"X-API-Key": "test-key"})
        assert r.status_code == 200

        # Endpoints under /api/patient(s) and friends are also PHI — they
        # must require the key. (Regression: an earlier allow-list only
        # gated /api/emr|/api/config|/api/export|/api/facts|/api/debug,
        # leaving patient list + detail open.)
        assert client.get("/api/patients").status_code == 401
        assert client.get("/api/patient/anything").status_code == 401
        assert client.get("/api/jobs").status_code == 401
        # With a valid key the same paths stop returning 401.
        assert client.get("/api/patients", headers={"X-API-Key": "test-key"}).status_code != 401

        # Anonymous-safe routes outside /api/ stay open even with API_KEY set.
        assert client.get("/health").status_code == 200
