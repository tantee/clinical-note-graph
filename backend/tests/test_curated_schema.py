from app.schemas.extraction import MedicationChange, PatientFact


def test_medication_change_accepts_temporal_fields():
    m = MedicationChange(
        name="paclitaxel",
        action="start",
        startDate="2026-01-10",
        startQualifier="exact",
        stopQualifier="ongoing",
        startText="started this admission",
        schedule="q3wk x 6 cycles",
    )
    assert m.startQualifier == "exact"
    assert m.stopQualifier == "ongoing"
    assert m.schedule == "q3wk x 6 cycles"
    assert m.stopDate is None


def test_medication_change_temporal_fields_default_none():
    m = MedicationChange(name="metformin")
    assert m.startDate is None
    assert m.startQualifier is None
    assert m.stopQualifier is None
    assert m.schedule is None


def test_patient_fact_accepts_onset_qualifiers():
    p = PatientFact(
        type="condition",
        value="breast cancer",
        onsetDate="2025-09-01",
        onsetQualifier="estimated",
        onsetText="about 4 months ago",
        resolvedQualifier="ongoing",
    )
    assert p.onsetQualifier == "estimated"
    assert p.onsetText == "about 4 months ago"
    assert p.resolvedQualifier == "ongoing"


def test_patient_fact_qualifiers_default_none():
    p = PatientFact(type="condition", value="diabetes")
    assert p.onsetQualifier is None
    assert p.resolvedQualifier is None


def test_extraction_prompt_mentions_temporal_guidance():
    from app.prompts import templates

    assert hasattr(templates, "EXTRACTION_SYSTEM"), "EXTRACTION_SYSTEM constant renamed?"
    text = templates.EXTRACTION_SYSTEM.lower()
    # Estimated-date anchoring + open bounds must be described to the model.
    assert "estimated" in text
    assert "ongoing" in text
    assert "schedule" in text
    assert "encounter date" in text
