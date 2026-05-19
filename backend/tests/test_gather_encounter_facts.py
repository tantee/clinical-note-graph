"""Unit tests for gather_encounter_facts — the aggregator that splits facts
into 'this encounter' and 'background' before sending to the AI."""
from __future__ import annotations

import pytest


@pytest.fixture()
def seeded_facts(fake_store):
    # One patient, two encounters: an admission (E1) and a follow-up (E2).
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Test"}
    fake_store.encounters["E1"] = {
        "encounter_id": "E1", "patient_id": "HN1", "type": "admission",
        "date_time": "2026-04-01T08:00:00+00:00",
        "department": "IM", "provider": "Dr A",
    }
    fake_store.encounters["E2"] = {
        "encounter_id": "E2", "patient_id": "HN1", "type": "discharge_summary",
        "date_time": "2026-05-01T08:00:00+00:00",
        "department": "IM", "provider": "Dr B",
    }
    # Documents.
    fake_store.documents["D1"] = {
        "document_id": "D1", "patient_id": "HN1", "encounter_id": "E1",
        "format": "text", "version": "1",
    }
    fake_store.documents["D2"] = {
        "document_id": "D2", "patient_id": "HN1", "encounter_id": "E2",
        "format": "text", "version": "1",
    }
    # Facts. E1: hypertension + amlodipine.
    # E2: pneumonia + ceftriaxone, plus a SECOND mention of hypertension.
    fake_store.facts.extend([
        {"id": "f-1", "patient_id": "HN1", "encounter_id": "E1",
         "document_id": "D1", "type": "condition",
         "value": "Hypertension", "normalized_code": "I10",
         "review_status": "ai_suggested", "date_time": "2026-04-01",
         "extra": {}, "confidence": 0.9},
        {"id": "f-2", "patient_id": "HN1", "encounter_id": "E1",
         "document_id": "D1", "type": "medication",
         "value": "Amlodipine", "review_status": "ai_suggested",
         "extra": {"action": "continue"}, "confidence": 0.9},
        {"id": "f-3", "patient_id": "HN1", "encounter_id": "E2",
         "document_id": "D2", "type": "condition",
         "value": "Pneumonia", "normalized_code": "J18.9",
         "review_status": "ai_suggested", "date_time": "2026-05-01",
         "extra": {}, "confidence": 0.9},
        {"id": "f-4", "patient_id": "HN1", "encounter_id": "E2",
         "document_id": "D2", "type": "medication",
         "value": "Ceftriaxone", "review_status": "ai_suggested",
         "extra": {"action": "start"}, "confidence": 0.9},
        # Second hypertension mention from the later encounter — used to
        # verify "latest mention wins" dedupe in background.
        {"id": "f-5", "patient_id": "HN1", "encounter_id": "E2",
         "document_id": "D2", "type": "condition",
         "value": "Hypertension", "normalized_code": "I10",
         "review_status": "ai_suggested", "date_time": "2026-05-01",
         "extra": {}, "confidence": 0.9},
        # A rejected fact must be excluded from both sections.
        {"id": "f-6", "patient_id": "HN1", "encounter_id": "E1",
         "document_id": "D1", "type": "condition",
         "value": "Anxiety", "normalized_code": "F41.9",
         "review_status": "rejected",
         "extra": {}, "confidence": 0.9},
    ])
    return fake_store


def test_gather_returns_encounter_metadata(seeded_facts):
    from app.services.patient_facts import gather_encounter_facts
    result = gather_encounter_facts("E2")
    assert result["encounter"]["encounterId"] == "E2"
    assert result["encounter"]["type"] == "discharge_summary"
    assert result["encounter"]["department"] == "IM"


def test_this_encounter_contains_only_eid_facts(seeded_facts):
    from app.services.patient_facts import gather_encounter_facts
    result = gather_encounter_facts("E2")
    this = result["thisEncounter"]
    # Pneumonia + ceftriaxone + (second) hypertension came from E2.
    problem_values = {p["value"] for p in this["problems"]}
    assert problem_values == {"Pneumonia", "Hypertension"}
    med_values = {m["value"] for m in this["medications"]}
    assert med_values == {"Ceftriaxone"}


def test_background_excludes_this_encounter_facts(seeded_facts):
    from app.services.patient_facts import gather_encounter_facts
    result = gather_encounter_facts("E2")
    bg = result["background"]
    # E1's hypertension is in this-encounter via the dedupe-on-code rule
    # later; for the background section specifically, we want only facts
    # whose encounter_id <> E2.
    # E1 had hypertension + amlodipine; rejected anxiety is excluded.
    bg_problems = {p["value"] for p in bg["chronicProblems"]}
    assert "Hypertension" in bg_problems
    assert "Anxiety" not in bg_problems
    bg_meds = {m["value"] for m in bg["homeMedications"]}
    assert bg_meds == {"Amlodipine"}


def test_background_dedupes_by_normalized_code_keeping_latest(seeded_facts):
    from app.services.patient_facts import gather_encounter_facts
    # When viewing from a hypothetical third encounter, hypertension appears
    # in BOTH E1 and E2; background should keep only the latest mention.
    fake_store = seeded_facts
    fake_store.encounters["E3"] = {
        "encounter_id": "E3", "patient_id": "HN1", "type": "clinic_visit",
        "date_time": "2026-06-01T08:00:00+00:00",
        "department": "Outpatient", "provider": "Dr C",
    }
    from app.services.patient_facts import gather_encounter_facts
    result = gather_encounter_facts("E3")
    htn_mentions = [p for p in result["background"]["chronicProblems"]
                    if p["normalized_code"] == "I10"]
    assert len(htn_mentions) == 1, "should dedupe by normalized_code"
    # Latest mention is from E2 (2026-05-01 > 2026-04-01).
    assert htn_mentions[0]["date_time"] == "2026-05-01"


def test_raises_lookup_error_on_unknown_encounter(fake_store):
    from app.services.patient_facts import gather_encounter_facts
    with pytest.raises(LookupError):
        gather_encounter_facts("E-does-not-exist")


def test_documents_filtered_to_encounter(seeded_facts):
    from app.services.patient_facts import gather_encounter_facts
    result = gather_encounter_facts("E1")
    doc_ids = {d["documentId"] for d in result["documents"]}
    assert doc_ids == {"D1"}
