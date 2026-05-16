from __future__ import annotations

from app.services.fhir_adapter import fhir_bundle_to_text, fhir_extract_patient


BUNDLE = {
    "resourceType": "Bundle",
    "type": "collection",
    "entry": [
        {"resource": {"resourceType": "Patient", "id": "HN42",
                       "name": [{"given": ["Somchai"], "family": "Sample"}],
                       "gender": "male", "birthDate": "1965-04-12"}},
        {"resource": {"resourceType": "Encounter", "class": {"display": "admission"},
                       "period": {"start": "2026-05-15T10:00:00+07:00"}}},
        {"resource": {"resourceType": "Condition", "code": {"text": "Type 2 diabetes mellitus"}}},
        {"resource": {"resourceType": "MedicationStatement", "status": "active",
                       "medicationCodeableConcept": {"text": "Metformin"}}},
        {"resource": {"resourceType": "Observation", "code": {"text": "HbA1c"},
                       "valueQuantity": {"value": 8.4, "unit": "%"}}},
    ],
}


def test_fhir_text_render_includes_key_fields():
    out = fhir_bundle_to_text(BUNDLE)
    assert "Somchai Sample" in out
    assert "Type 2 diabetes mellitus" in out
    assert "Metformin" in out
    assert "HbA1c" in out and "8.4" in out


def test_fhir_extract_patient():
    p = fhir_extract_patient(BUNDLE)
    assert p["patientId"] == "HN42"
    assert p["gender"] == "male"
    assert p["birthDate"] == "1965-04-12"
