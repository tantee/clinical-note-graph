import pytest


@pytest.fixture()
def patient(fake_store):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Jane"}
    return "HN1"


def test_list_empty(app_client, patient):
    r = app_client.get(f"/api/patient/{patient}/curated", params={"type": "medication"})
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_manual_insert_then_list(app_client, patient, stub_neo4j):
    body = {
        "type": "medication", "displayValue": "Warfarin",
        "startDate": "2026-02-01", "startQualifier": "exact",
        "stopQualifier": "ongoing", "scheduleText": "5mg daily", "status": "start",
    }
    r = app_client.post(f"/api/patient/{patient}/curated", json=body)
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["origin"] == "human"
    assert created["reviewStatus"] == "human_confirmed"
    assert created["displayValue"] == "Warfarin"

    r2 = app_client.get(f"/api/patient/{patient}/curated", params={"type": "medication"})
    items = r2.json()["items"]
    assert len(items) == 1 and items[0]["displayValue"] == "Warfarin"


def test_patch_marks_field_human_edited(app_client, patient, fake_store, stub_neo4j):
    fake_store.curated_facts.append({
        "id": "cur1", "patient_id": "HN1", "type": "medication",
        "normalized_key": "warfarin", "display_value": "Warfarin",
        "normalized_code": None, "coding_system": None, "start_date": "2026-01-01",
        "start_qualifier": "exact", "stop_date": None, "stop_qualifier": "ongoing",
        "start_text": None, "stop_text": None, "schedule_text": None, "status": "start",
        "record_state": "active", "review_status": "ai_suggested", "origin": "ai",
        "human_edited_fields": [], "last_evidence_fact_id": None,
    })
    r = app_client.patch("/api/curated/cur1", json={"startDate": "2025-12-25"})
    assert r.status_code == 200, r.text
    row = next(r for r in fake_store.curated_facts if r["id"] == "cur1")
    assert row["start_date"] == "2025-12-25"
    assert "start_date" in row["human_edited_fields"]
    assert row["review_status"] == "human_confirmed"


def test_patch_ongoing_clears_stop_date(app_client, patient, fake_store, stub_neo4j):
    fake_store.curated_facts.append({
        "id": "cur2", "patient_id": "HN1", "type": "medication",
        "normalized_key": "warfarin", "display_value": "Warfarin",
        "normalized_code": None, "coding_system": None, "start_date": "2026-01-01",
        "start_qualifier": "exact", "stop_date": "2026-03-01", "stop_qualifier": "exact",
        "start_text": None, "stop_text": None, "schedule_text": None, "status": "start",
        "record_state": "active", "review_status": "ai_suggested", "origin": "ai",
        "human_edited_fields": [], "last_evidence_fact_id": None,
    })
    r = app_client.patch("/api/curated/cur2", json={"stopQualifier": "ongoing"})
    assert r.status_code == 200, r.text
    row = next(r for r in fake_store.curated_facts if r["id"] == "cur2")
    assert row["stop_date"] is None


def test_delete_then_restore(app_client, patient, fake_store, stub_neo4j):
    fake_store.curated_facts.append({
        "id": "cur3", "patient_id": "HN1", "type": "condition",
        "normalized_key": "asthma", "display_value": "Asthma",
        "normalized_code": None, "coding_system": None, "start_date": None,
        "start_qualifier": "unknown", "stop_date": None, "stop_qualifier": "ongoing",
        "start_text": None, "stop_text": None, "schedule_text": None, "status": "active",
        "record_state": "active", "review_status": "ai_suggested", "origin": "ai",
        "human_edited_fields": [], "last_evidence_fact_id": None,
    })
    r = app_client.delete("/api/curated/cur3")
    assert r.status_code == 200
    row = next(r for r in fake_store.curated_facts if r["id"] == "cur3")
    assert row["record_state"] == "dismissed"
    r2 = app_client.post("/api/curated/cur3/restore")
    assert r2.status_code == 200
    assert row["record_state"] == "active"


def test_patch_missing_id_404(app_client, patient):
    r = app_client.patch("/api/curated/nope", json={"startDate": "2026-01-01"})
    assert r.status_code == 404


def test_delete_missing_id_404(app_client, patient):
    r = app_client.delete("/api/curated/nope")
    assert r.status_code == 404


def test_restore_missing_id_404(app_client, patient):
    r = app_client.post("/api/curated/nope/restore")
    assert r.status_code == 404
