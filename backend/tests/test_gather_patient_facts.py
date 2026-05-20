"""Unit tests for gather_patient_facts — the patient-level aggregator that
powers the Overview tab. Regression tests for the dedup added in
fix/patient-detail-ui-bugs (issue #15)."""

from __future__ import annotations


def _seed_one_patient(fake_store, *, patient_id="HN1"):
    fake_store.patients[patient_id] = {"patient_id": patient_id, "name": "Test"}
    fake_store.encounters["E1"] = {
        "encounter_id": "E1", "patient_id": patient_id, "type": "admission",
        "date_time": "2026-04-01T08:00:00+00:00",
        "department": "IM", "provider": "Dr A",
    }
    return fake_store


def test_dedupe_conditions_by_normalized_code_keeps_highest_confidence(fake_store):
    """The same diagnosis can be mentioned three times in one EMR (IMP,
    Intraop, Discharge sections). All three rows share the same ICD code,
    so the Overview tab should collapse them into one entry."""
    _seed_one_patient(fake_store)
    # Three mentions of the same condition; only the middle one has high confidence.
    fake_store.facts.extend([
        {"id": "f-1", "patient_id": "HN1", "encounter_id": "E1",
         "type": "condition", "value": "Pelvic organ prolapse stage 3",
         "normalized_code": "N81.10", "review_status": "ai_suggested",
         "date_time": "2026-04-01", "extra": {}, "confidence": 0.60,
         "evidence_text": "IMP: POP stage 3", "created_at": "2026-04-01T08:00:00Z"},
        {"id": "f-2", "patient_id": "HN1", "encounter_id": "E1",
         "type": "condition", "value": "Pelvic organ prolapse stage 3",
         "normalized_code": "N81.10", "review_status": "ai_suggested",
         "date_time": "2026-04-01", "extra": {}, "confidence": 0.90,
         "evidence_text": "Intraop: POP stage 3 with elongated cervix",
         "created_at": "2026-04-01T08:10:00Z"},
        {"id": "f-3", "patient_id": "HN1", "encounter_id": "E1",
         "type": "condition", "value": "Pelvic organ prolapse stage 3",
         "normalized_code": "N81.10", "review_status": "ai_suggested",
         "date_time": "2026-04-01", "extra": {}, "confidence": 0.75,
         "evidence_text": "Discharge: POP stage 3 s/p VH",
         "created_at": "2026-04-01T08:20:00Z"},
    ])

    from app.services.patient_facts import gather_patient_facts
    result = gather_patient_facts("HN1")
    problems = result["problems"]

    assert len(problems) == 1, "all three POP mentions should collapse into one row"
    rep = problems[0]
    # Highest-confidence row wins as the representative.
    assert rep["confidence"] == 0.90
    # Evidence from the duplicates is accumulated so reviewers see every mention.
    assert "Intraop" in (rep.get("evidence_text") or "")
    # The other two mentions' evidence text should be merged in too.
    assert (rep.get("evidence_text") or "").count("·") == 2  # 3 unique evidence strings joined by ·


def test_dedupe_medications_is_case_insensitive(fake_store):
    """`tamoxifen` and `Tamoxifen` are the same medication. Without case-
    insensitive dedup, the Overview shows both. Real-world cause: extractor
    pulls the lowercase from a free-text mention ('on tamoxifen') and the
    titlecased version from a Meds section."""
    _seed_one_patient(fake_store)
    fake_store.facts.extend([
        {"id": "m-1", "patient_id": "HN1", "encounter_id": "E1",
         "type": "medication", "value": "tamoxifen", "normalized_code": None,
         "review_status": "ai_suggested", "extra": {"action": "continue"},
         "confidence": 0.85, "evidence_text": "on tamoxifen",
         "created_at": "2026-04-01T08:00:00Z"},
        {"id": "m-2", "patient_id": "HN1", "encounter_id": "E1",
         "type": "medication", "value": "Tamoxifen", "normalized_code": None,
         "review_status": "ai_suggested", "extra": {"action": "continue"},
         "confidence": 0.85, "evidence_text": "Meds: Tamoxifen",
         "created_at": "2026-04-01T08:10:00Z"},
    ])

    from app.services.patient_facts import gather_patient_facts
    result = gather_patient_facts("HN1")
    meds = result["medications"]

    assert len(meds) == 1, "tamoxifen / Tamoxifen are the same medication"


def test_dedupe_does_not_collapse_distinct_conditions(fake_store):
    """Two distinct conditions must not be collapsed even if their values
    share a substring or one has no normalized code."""
    _seed_one_patient(fake_store)
    fake_store.facts.extend([
        {"id": "f-1", "patient_id": "HN1", "encounter_id": "E1",
         "type": "condition", "value": "Hypertension",
         "normalized_code": "I10", "review_status": "ai_suggested",
         "extra": {}, "confidence": 0.9, "created_at": "2026-04-01T08:00:00Z"},
        {"id": "f-2", "patient_id": "HN1", "encounter_id": "E1",
         "type": "condition", "value": "Breast cancer",
         "normalized_code": "C50.9", "review_status": "ai_suggested",
         "extra": {}, "confidence": 0.9, "created_at": "2026-04-01T08:05:00Z"},
    ])

    from app.services.patient_facts import gather_patient_facts
    problems = gather_patient_facts("HN1")["problems"]
    values = {p["value"] for p in problems}
    assert values == {"Hypertension", "Breast cancer"}


def test_observations_are_not_deduped(fake_store):
    """Observations (labs) deliberately stay un-deduped — the same lab measured
    across visits is a longitudinal trend, not noise."""
    _seed_one_patient(fake_store)
    fake_store.facts.extend([
        {"id": "o-1", "patient_id": "HN1", "encounter_id": "E1",
         "type": "observation", "value": "HbA1c", "normalized_code": "4548-4",
         "review_status": "ai_suggested",
         "extra": {"value": "8.4", "unit": "%"}, "confidence": 0.9,
         "created_at": "2026-04-01T08:00:00Z"},
        {"id": "o-2", "patient_id": "HN1", "encounter_id": "E1",
         "type": "observation", "value": "HbA1c", "normalized_code": "4548-4",
         "review_status": "ai_suggested",
         "extra": {"value": "7.2", "unit": "%"}, "confidence": 0.9,
         "created_at": "2026-04-02T08:00:00Z"},
    ])

    from app.services.patient_facts import gather_patient_facts
    obs = gather_patient_facts("HN1")["observations"]
    assert len(obs) == 2, "same lab measured twice should produce two rows for trending"


def test_dedupe_falls_back_to_value_when_no_code(fake_store):
    """Some extractors don't emit a normalized_code for every mention.
    Without a code, dedupe must key on lower-cased value so two
    "Atrophic vagina" mentions still collapse."""
    _seed_one_patient(fake_store)
    fake_store.facts.extend([
        {"id": "f-1", "patient_id": "HN1", "encounter_id": "E1",
         "type": "condition", "value": "Atrophic vagina",
         "normalized_code": None, "review_status": "ai_suggested",
         "extra": {}, "confidence": 0.8, "created_at": "2026-04-01T08:00:00Z"},
        {"id": "f-2", "patient_id": "HN1", "encounter_id": "E1",
         "type": "condition", "value": "atrophic vagina",  # lowercased
         "normalized_code": None, "review_status": "ai_suggested",
         "extra": {}, "confidence": 0.85, "created_at": "2026-04-01T08:05:00Z"},
    ])

    from app.services.patient_facts import gather_patient_facts
    problems = gather_patient_facts("HN1")["problems"]
    assert len(problems) == 1
