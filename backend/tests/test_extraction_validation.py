from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.extraction import (
    ClinicalExtractionResult,
    DiagnosisCandidate,
    MedicationChange,
    PatientFact,
)


def test_minimal_valid_extraction():
    obj = ClinicalExtractionResult(patientId="HN1")
    assert obj.problems == []
    assert obj.medications == []
    assert obj.codingCandidates == []


def test_strict_rejects_unknown_field():
    with pytest.raises(ValidationError):
        ClinicalExtractionResult.model_validate({"patientId": "X", "bogus": 1})


def test_fact_confidence_bounds():
    with pytest.raises(ValidationError):
        PatientFact.model_validate({"type": "condition", "value": "DM", "confidence": 2.0})


def test_medication_action_enum():
    with pytest.raises(ValidationError):
        MedicationChange.model_validate({"name": "metformin", "action": "boop"})


def test_diagnosis_role_enum():
    DiagnosisCandidate.model_validate({"condition": "DM", "role": "primary"})
    with pytest.raises(ValidationError):
        DiagnosisCandidate.model_validate({"condition": "DM", "role": "guess"})


def test_round_trip_real_payload():
    payload = {
        "patientId": "HN1",
        "summary": "Type 2 DM",
        "problems": [{"type": "condition", "value": "Type 2 diabetes mellitus", "normalizedCode": "E11.9", "codingSystem": "ICD10", "confidence": 0.8}],
        "medications": [{"name": "metformin", "action": "start", "rxNorm": "6809", "indication": "diabetes", "confidence": 0.9}],
        "observations": [{"name": "HbA1c", "loinc": "4548-4", "value": "8.4", "unit": "%", "confidence": 0.95}],
        "plans": [{"description": "Education on diabetes", "category": "education"}],
        "diagnoses": [{"condition": "Type 2 diabetes mellitus", "icd10": "E11.9", "role": "primary"}],
        "codingCandidates": [{"code": "E11.9", "system": "ICD10", "display": "Type 2 DM", "forCondition": "Type 2 diabetes mellitus", "confidence": 0.7}],
    }
    obj = ClinicalExtractionResult.model_validate(payload)
    assert obj.diagnoses[0].role == "primary"
    assert obj.medications[0].action == "start"
    assert obj.observations[0].loinc == "4548-4"
