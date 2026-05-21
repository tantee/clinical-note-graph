"""Integration tests for the encounter-scoped routes. Uses the FakeStore +
TestClient fixtures from conftest."""
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
        "type": "condition", "value": "Pneumonia",
        "normalized_code": "J18.9", "review_status": "ai_suggested",
        "date_time": "2026-04-01", "extra": {}, "confidence": 0.9,
    })


def test_summary_latest_returns_null_when_none_persisted(app_client, fake_store):
    _seed(fake_store)
    r = app_client.get("/api/patient/HN1/encounter/E1/summary/latest")
    assert r.status_code == 200
    assert r.json() is None


def test_summary_post_then_latest_returns_persisted(app_client, fake_store):
    _seed(fake_store)
    r = app_client.post(
        "/api/patient/HN1/encounter/E1/summary",
        json={"type": "discharge_summary", "includeEvidence": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "discharge_summary"
    assert "markdown" in body
    r2 = app_client.get("/api/patient/HN1/encounter/E1/summary/latest")
    assert r2.status_code == 200
    latest = r2.json()
    assert latest is not None
    assert latest["type"] == "discharge_summary"


def test_summary_404_when_encounter_does_not_belong_to_patient(app_client, fake_store):
    _seed(fake_store)
    fake_store.encounters["E2"] = {
        "encounter_id": "E2", "patient_id": "HN-OTHER", "type": "admission",
        "date_time": "2026-04-01T08:00:00+00:00",
    }
    r = app_client.get("/api/patient/HN1/encounter/E2/summary/latest")
    assert r.status_code == 404
    assert r.json()["detail"] == "Encounter not found for patient"


def test_coding_post_then_latest_round_trip(app_client, fake_store):
    _seed(fake_store)
    r = app_client.post(
        "/api/patient/HN1/encounter/E1/coding/suggest",
        json={"standards": ["ICD10", "SNOMEDCT"], "includeEvidence": False},
    )
    assert r.status_code == 200, r.text
    r2 = app_client.get("/api/patient/HN1/encounter/E1/coding/latest")
    assert r2.status_code == 200
    assert r2.json() is not None


def test_default_summary_type_is_discharge_for_admission(app_client, fake_store):
    _seed(fake_store)
    r = app_client.post(
        "/api/patient/HN1/encounter/E1/summary",
        json={"includeEvidence": False},  # no `type` field
    )
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "discharge_summary"


def test_default_summary_type_is_detailed_for_clinic_visit(app_client, fake_store):
    _seed(fake_store)
    fake_store.encounters["E1"]["type"] = "clinic_visit"
    r = app_client.post(
        "/api/patient/HN1/encounter/E1/summary",
        json={"includeEvidence": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "detailed"


def test_encounter_facts_returns_aggregated_view(app_client, fake_store):
    """The Detail tab of the encounter dialog needs facts + background + docs
    in one round-trip BEFORE the user has generated a summary. This endpoint
    is what unblocked that — see fix/encounter-dialog-blank."""
    _seed(fake_store)
    # Plant a background condition on a DIFFERENT encounter so it should
    # show up under background.chronicProblems (not under thisEncounter).
    fake_store.encounters["E0"] = {
        "encounter_id": "E0", "patient_id": "HN1", "type": "visit",
        "date_time": "2026-01-01T08:00:00+00:00",
    }
    fake_store.facts.append({
        "id": "f-bg-1", "patient_id": "HN1", "encounter_id": "E0",
        "type": "condition", "value": "Type 2 diabetes",
        "normalized_code": "E11.9", "review_status": "ai_suggested",
        "extra": {}, "confidence": 0.85,
    })
    fake_store.documents["D1"] = {
        "document_id": "D1", "patient_id": "HN1", "encounter_id": "E1",
        "format": "text", "version": "1",
    }

    r = app_client.get("/api/patient/HN1/encounter/E1/facts")
    assert r.status_code == 200, r.text
    body = r.json()

    # Encounter metadata
    assert body["encounter"]["encounterId"] == "E1"
    assert body["encounter"]["patientId"] == "HN1"
    # This encounter's facts — the pneumonia condition planted in _seed
    assert len(body["thisEncounter"]["problems"]) == 1
    assert body["thisEncounter"]["problems"][0]["value"] == "Pneumonia"
    # Background — the T2DM planted under E0
    bg_problems = [p["value"] for p in body["background"]["chronicProblems"]]
    assert "Type 2 diabetes" in bg_problems
    # Documents
    assert len(body["documents"]) == 1
    assert body["documents"][0]["documentId"] == "D1"


def test_encounter_facts_404_when_encounter_does_not_belong_to_patient(app_client, fake_store):
    _seed(fake_store)
    fake_store.encounters["E2"] = {
        "encounter_id": "E2", "patient_id": "HN-OTHER", "type": "admission",
        "date_time": "2026-04-01T08:00:00+00:00",
    }
    r = app_client.get("/api/patient/HN1/encounter/E2/facts")
    assert r.status_code == 404


def test_encounter_listing(app_client, fake_store):
    _seed(fake_store)
    fake_store.documents["D1"] = {
        "document_id": "D1", "patient_id": "HN1", "encounter_id": "E1",
        "format": "text", "version": "1",
    }
    r = app_client.get("/api/patient/HN1/encounters")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["encounterId"] == "E1"
    assert rows[0]["docCount"] == 1
    assert rows[0]["hasSummary"] is False
