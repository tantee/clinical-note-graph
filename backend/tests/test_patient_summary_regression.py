"""Confirms the patient-level summary + coding endpoints continue to work
after the patient_summaries schema change and the new encounter routes."""
from __future__ import annotations


def _seed(fake_store):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Test"}
    fake_store.encounters["E1"] = {
        "encounter_id": "E1", "patient_id": "HN1", "type": "admission",
        "date_time": "2026-04-01T08:00:00+00:00",
        "department": "IM", "provider": "Dr A",
    }
    fake_store.facts.append({
        "id": "f-1", "patient_id": "HN1", "encounter_id": "E1",
        "type": "condition", "value": "Hypertension",
        "normalized_code": "I10", "review_status": "ai_suggested",
        "date_time": "2026-04-01", "extra": {}, "confidence": 0.9,
    })


def test_patient_level_summary_post_returns_markdown(app_client, fake_store):
    _seed(fake_store)
    r = app_client.post(
        "/api/patient/HN1/summary",
        json={"type": "detailed", "includeEvidence": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["patientId"] == "HN1"
    assert body["type"] == "detailed"
    assert "markdown" in body


def test_patient_level_summary_latest_unchanged_shape(app_client, fake_store):
    _seed(fake_store)
    app_client.post("/api/patient/HN1/summary",
                    json={"type": "brief", "includeEvidence": False})
    r = app_client.get("/api/patient/HN1/summary/latest")
    assert r.status_code == 200
    latest = r.json()
    assert latest is not None
    assert latest["type"] == "brief"
    assert "vaultPath" in latest


def test_patient_level_coding_round_trip(app_client, fake_store):
    _seed(fake_store)
    app_client.post(
        "/api/patient/HN1/coding/suggest",
        json={"standards": ["ICD10"], "includeEvidence": False},
    )
    r = app_client.get("/api/patient/HN1/coding/latest")
    assert r.status_code == 200
    assert r.json() is not None


def test_patient_level_summary_does_not_leak_into_encounter_latest(app_client, fake_store):
    """A patient-level summary (encounter_id IS NULL) must not be returned
    by GET /encounter/{eid}/summary/latest."""
    _seed(fake_store)
    app_client.post("/api/patient/HN1/summary",
                    json={"type": "brief", "includeEvidence": False})
    r = app_client.get("/api/patient/HN1/encounter/E1/summary/latest")
    assert r.status_code == 200
    assert r.json() is None  # no encounter-scoped row exists yet
