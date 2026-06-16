from app.services.curated import (
    normalized_key,
    normalize_bounds,
    ai_item_from_condition,
    ai_item_from_medication,
    merge_curated,
)
from app.schemas.extraction import MedicationChange, PatientFact


def test_normalized_key_prefers_code():
    assert normalized_key("E11.9", "Type 2 Diabetes") == "e11.9"
    assert normalized_key(None, "Type 2 Diabetes") == "type 2 diabetes"
    assert normalized_key("  ", "  Asthma ") == "asthma"


def test_normalize_bounds_defaults_and_ongoing_clears_stop():
    s_date, s_q, e_date, e_q = normalize_bounds("2026-01-01", None, "2026-02-01", "ongoing")
    assert s_q == "exact"          # date present, no qualifier -> exact
    assert e_q == "ongoing"
    assert e_date is None          # ongoing clears the stop date
    s_date, s_q, e_date, e_q = normalize_bounds(None, None, None, None)
    assert s_q == "unknown" and e_q == "unknown"


def test_ai_item_from_condition_maps_onset_resolved():
    p = PatientFact(
        type="condition", value="Breast cancer", normalizedCode="C50.9",
        codingSystem="ICD10", onsetDate="2025-09-01", onsetQualifier="estimated",
        onsetText="about 4 months ago", resolvedQualifier="ongoing", status="active",
    )
    item = ai_item_from_condition(p)
    assert item["type"] == "condition"
    assert item["normalized_key"] == "c50.9"
    assert item["display_value"] == "Breast cancer"
    assert item["start_qualifier"] == "estimated"
    assert item["start_text"] == "about 4 months ago"
    assert item["stop_qualifier"] == "ongoing"
    assert item["status"] == "active"


def test_ai_item_from_medication_maps_schedule_and_action():
    m = MedicationChange(
        name="Paclitaxel", rxNorm="56946", action="start",
        startDate="2026-01-10", startQualifier="exact", stopQualifier="ongoing",
        schedule="q3wk x 6 cycles",
    )
    item = ai_item_from_medication(m)
    assert item["type"] == "medication"
    assert item["normalized_key"] == "56946"
    assert item["display_value"] == "Paclitaxel"
    assert item["schedule_text"] == "q3wk x 6 cycles"
    assert item["status"] == "start"
    assert item["stop_qualifier"] == "ongoing"


def test_merge_new_identity_is_insert_ai_suggested():
    ai = ai_item_from_medication(MedicationChange(name="metformin", startDate="2026-01-01"))
    row, is_new = merge_curated(None, ai, resurface=False)
    assert is_new is True
    assert row["origin"] == "ai"
    assert row["review_status"] == "ai_suggested"
    assert row["record_state"] == "active"
    assert row["start_date"] == "2026-01-01"
    assert row["human_edited_fields"] == []


def test_merge_never_clobbers_human_edited_field():
    existing = {
        "display_value": "Metformin XR", "normalized_code": None, "coding_system": None,
        "start_date": "2025-12-25", "start_qualifier": "exact", "stop_date": None,
        "stop_qualifier": "ongoing", "start_text": None, "stop_text": None,
        "schedule_text": None, "status": "continue", "record_state": "active",
        "review_status": "human_confirmed", "origin": "ai",
        "human_edited_fields": ["start_date", "display_value"],
    }
    ai = ai_item_from_medication(
        MedicationChange(name="metformin", startDate="2026-01-01", action="modify")
    )
    row, is_new = merge_curated(existing, ai, resurface=False)
    assert is_new is False
    assert row["start_date"] == "2025-12-25"      # human edit preserved
    assert row["display_value"] == "Metformin XR" # human edit preserved
    assert row["status"] == "modify"              # non-human field refreshed from AI


def test_merge_fills_empty_non_human_field():
    existing = {
        "display_value": "Metformin", "normalized_code": None, "coding_system": None,
        "start_date": None, "start_qualifier": "unknown", "stop_date": None,
        "stop_qualifier": "unknown", "start_text": None, "stop_text": None,
        "schedule_text": None, "status": None, "record_state": "active",
        "review_status": "ai_suggested", "origin": "ai", "human_edited_fields": [],
    }
    ai = ai_item_from_medication(MedicationChange(name="metformin", startDate="2026-01-01"))
    row, _ = merge_curated(existing, ai, resurface=False)
    assert row["start_date"] == "2026-01-01"      # empty field filled from AI


def test_merge_resurface_reactivates_and_preserves_human_dates():
    existing = {
        "display_value": "Breast cancer", "normalized_code": "C50.9", "coding_system": "ICD10",
        "start_date": "2025-09-01", "start_qualifier": "estimated", "stop_date": None,
        "stop_qualifier": "ongoing", "start_text": "4 months ago", "stop_text": None,
        "schedule_text": None, "status": "active", "record_state": "dismissed",
        "review_status": "human_confirmed", "origin": "ai",
        "human_edited_fields": ["start_date", "start_qualifier"],
    }
    ai = ai_item_from_condition(
        PatientFact(type="condition", value="Breast cancer", normalizedCode="C50.9",
                    codingSystem="ICD10", onsetDate="2024-01-01", onsetQualifier="exact")
    )
    row, is_new = merge_curated(existing, ai, resurface=True)
    assert is_new is False
    assert row["record_state"] == "active"        # resurfaced
    assert row["review_status"] == "ai_suggested" # back to review
    assert row["start_date"] == "2025-09-01"       # human date edit preserved
    assert row["start_qualifier"] == "estimated"
