from app.services.curated import reconcile_curated, list_curated
from app.schemas.extraction import ClinicalExtractionResult, MedicationChange, PatientFact


def _extraction(**kw):
    return ClinicalExtractionResult(
        patientId="HN1", encounterId="E1", documentId="D1", **kw
    )


def test_reconcile_inserts_new_curated_rows(fake_store, stub_neo4j):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Jane"}
    ex = _extraction(
        problems=[PatientFact(type="condition", value="Breast cancer",
                              normalizedCode="C50.9", codingSystem="ICD10",
                              onsetDate="2025-09-01", onsetQualifier="estimated")],
        medications=[MedicationChange(name="paclitaxel", rxNorm="56946",
                                      action="start", schedule="q3wk x 6 cycles")],
    )
    reconcile_curated("HN1", ex)
    rows = {r["normalized_key"]: r for r in fake_store.curated_facts}
    assert "c50.9" in rows and rows["c50.9"]["review_status"] == "ai_suggested"
    assert rows["56946"]["schedule_text"] == "q3wk x 6 cycles"
    assert any("Condition" in q or "Medication" in q for q, _ in stub_neo4j)


def test_reconcile_preserves_human_edits_on_rementioning(fake_store, stub_neo4j):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Jane"}
    fake_store.curated_facts.append({
        "id": "cur1", "patient_id": "HN1", "type": "medication",
        "normalized_key": "56946", "display_value": "Paclitaxel",
        "normalized_code": "56946", "coding_system": "RxNorm",
        "start_date": "2026-01-10", "start_qualifier": "exact",
        "stop_date": None, "stop_qualifier": "ongoing",
        "start_text": None, "stop_text": None, "schedule_text": "q3wk x 6 cycles",
        "status": "start", "record_state": "dismissed",
        "review_status": "human_confirmed", "origin": "ai",
        "human_edited_fields": ["start_date"], "last_evidence_fact_id": None,
    })
    ex = _extraction(medications=[
        MedicationChange(name="paclitaxel", rxNorm="56946", action="continue",
                         startDate="2099-01-01")
    ])
    reconcile_curated("HN1", ex)
    row = next(r for r in fake_store.curated_facts if r["normalized_key"] == "56946")
    assert row["record_state"] == "active"
    assert row["review_status"] == "ai_suggested"
    assert row["start_date"] == "2026-01-10"
    assert row["status"] == "continue"


def test_reconcile_propagates_null_onset_for_dateless_condition(fake_store, stub_neo4j):
    fake_store.patients["HN1"] = {"patient_id": "HN1"}
    ex = _extraction(problems=[PatientFact(type="condition", value="Asthma")])
    reconcile_curated("HN1", ex)
    cond_calls = [(q, p) for q, p in stub_neo4j if "Condition" in q]
    assert cond_calls, "expected a Condition propagation"
    _, params = cond_calls[-1]
    assert params["startDate"] is None
    assert params["value"] == "Asthma"


def test_list_curated_returns_only_active(fake_store):
    fake_store.patients["HN1"] = {"patient_id": "HN1"}
    fake_store.curated_facts.extend([
        {"id": "a", "patient_id": "HN1", "type": "condition", "normalized_key": "x",
         "display_value": "X", "record_state": "active", "review_status": "ai_suggested",
         "human_edited_fields": []},
        {"id": "b", "patient_id": "HN1", "type": "condition", "normalized_key": "y",
         "display_value": "Y", "record_state": "dismissed", "review_status": "ai_suggested",
         "human_edited_fields": []},
    ])
    items = list_curated("HN1", "condition")
    keys = {i["normalized_key"] for i in items}
    assert keys == {"x"}
