"""Recovery endpoint: rebuild the curated layer for a patient from stored AI
extractions (ai_outputs.raw_output) without re-calling the AI.

Needed for patients ingested before `curated_facts` existed: reconcile_curated
failed silently then (best-effort), so the curated Problems/Medications panels
are empty even though the raw AI facts populated. This replays the stored
extractions through reconcile, mirroring the /graph/rebuild recovery path.
"""
import pytest


def _extract_output(doc, created, **ex):
    return {
        "id": f"ai-{doc}-{created}", "document_id": doc, "patient_id": "HN1",
        "call_type": "extract", "valid": True, "model": "mock", "created_at": created,
        "raw_output": {"patientId": "HN1", "encounterId": "E1", "documentId": doc, **ex},
    }


@pytest.fixture()
def patient(fake_store):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Jane"}
    return "HN1"


def test_reconcile_from_history_populates_curated(app_client, patient, fake_store, stub_neo4j):
    fake_store.ai_outputs.append(_extract_output(
        "D1", "2026-04-01T08:00:00Z",
        problems=[{"type": "condition", "value": "Appendicitis"},
                  {"type": "condition", "value": "Pneumonia"}],
        medications=[{"name": "IV antibiotics", "action": "start"}],
    ))
    # A non-extraction AI output (coding) must be ignored.
    fake_store.ai_outputs.append({
        "id": "ai-coding", "document_id": "D1", "patient_id": "HN1",
        "call_type": "coding", "valid": True, "model": "mock",
        "created_at": "2026-04-01T08:05:00Z", "raw_output": {"candidates": []},
    })

    r = app_client.post("/api/patient/HN1/curated/reconcile")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["patientId"] == "HN1"
    assert body["documents"] == 1

    conds = [c["display_value"] for c in fake_store.curated_facts if c["type"] == "condition"]
    meds = [m["display_value"] for m in fake_store.curated_facts if m["type"] == "medication"]
    assert "Appendicitis" in conds and "Pneumonia" in conds
    assert "IV antibiotics" in meds


def test_reconcile_from_history_uses_latest_extraction_per_document(app_client, patient, fake_store, stub_neo4j):
    # Two extractions for the same document (a retry). The later one wins.
    fake_store.ai_outputs.append(_extract_output(
        "D1", "2026-04-01T08:00:00Z",
        problems=[{"type": "condition", "value": "Old guess"}],
    ))
    fake_store.ai_outputs.append(_extract_output(
        "D1", "2026-04-01T09:00:00Z",
        problems=[{"type": "condition", "value": "Appendicitis"}],
    ))
    r = app_client.post("/api/patient/HN1/curated/reconcile")
    assert r.status_code == 200, r.text
    assert r.json()["documents"] == 1
    conds = [c["display_value"] for c in fake_store.curated_facts if c["type"] == "condition"]
    assert "Appendicitis" in conds
    assert "Old guess" not in conds


def test_reconcile_from_history_404_for_unknown_patient(app_client, fake_store):
    r = app_client.post("/api/patient/NOPE/curated/reconcile")
    assert r.status_code == 404
