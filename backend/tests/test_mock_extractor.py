from __future__ import annotations

from app.services.ai_provider import mock_extract
from app.schemas.extraction import ClinicalExtractionResult


SAMPLE = """
Admission note. Patient with Type 2 diabetes mellitus and hypertension.
HbA1c 8.4% on admission. BP 152/95. Glucose 220 mg/dL.
Start metformin 500 mg bid. Add lisinopril 10 mg daily.
Plan: cardiology consult.
"""


def test_mock_extracts_problems_meds_observations():
    result = mock_extract(SAMPLE, patient_id="HN1", encounter_id="E1", document_id="D1")
    obj = ClinicalExtractionResult.model_validate(result)
    cond_values = {p.value for p in obj.problems}
    assert "Type 2 diabetes mellitus" in cond_values
    assert "Essential hypertension" in cond_values

    med_names = {m.name.lower() for m in obj.medications}
    assert "metformin" in med_names
    assert "lisinopril" in med_names

    obs_names = {o.name for o in obj.observations}
    assert "HbA1c" in obs_names
    assert "Blood pressure" in obs_names

    # codes attached
    icd_codes = {p.normalizedCode for p in obj.problems}
    assert "E11.9" in icd_codes
    assert "I10" in icd_codes


def test_mock_extractor_idempotent():
    a = mock_extract(SAMPLE, patient_id="X", encounter_id="Y", document_id="Z")
    b = mock_extract(SAMPLE, patient_id="X", encounter_id="Y", document_id="Z")
    assert a == b


def test_mock_extractor_picks_up_plan_lines():
    result = mock_extract("Plan: admit CCU, consult cardiology, follow up clinic", patient_id="P", encounter_id=None, document_id="D")
    obj = ClinicalExtractionResult.model_validate(result)
    assert any("admit" in pl.description.lower() or "consult" in pl.description.lower() or "follow" in pl.description.lower() for pl in obj.plans)
