from __future__ import annotations


def _fhir_bundle() -> dict:
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": "HN42",
                          "name": [{"given": ["Malee"], "family": "Demo"}],
                          "gender": "female", "birthDate": "1972-08-09"}},
            {"resource": {"resourceType": "Encounter", "class": {"display": "admission"},
                          "period": {"start": "2026-05-10T08:00:00+07:00"}}},
            {"resource": {"resourceType": "Condition",
                          "code": {"text": "Community acquired pneumonia",
                                   "coding": [{"system": "http://hl7.org/fhir/sid/icd-10", "code": "J18.9"}]}}},
            {"resource": {"resourceType": "MedicationRequest", "status": "active",
                          "medicationCodeableConcept": {"text": "Ceftriaxone 1g IV q24h"}}},
            {"resource": {"resourceType": "Observation", "code": {"text": "SpO2"},
                          "valueQuantity": {"value": 92, "unit": "%"}}},
        ],
    }


def test_fhir_ingest(app_client, fake_store):
    payload = {
        "patient": {"patientId": "HN42"},
        "encounter": {"type": "admission", "dateTime": "2026-05-10T08:00:00+07:00"},
        "format": "fhir",
        "content": _fhir_bundle(),
        "source": {"system": "FHIR-X", "documentId": "fhir-001", "version": "1"},
    }
    r = app_client.post("/api/emr/ingest", json=payload)
    assert r.status_code == 200, r.text
    assert fake_store.patients["HN42"]["name"] == "Malee Demo"
    assert any("pneumonia" in f["value"].lower() for f in fake_store.facts)


def test_longitudinal_merge_creates_two_documents(app_client, fake_store):
    base = {
        "patient": {"patientId": "HN9"},
        "encounter": {"type": "admission", "dateTime": "2026-01-01T09:00:00+07:00"},
        "format": "text",
        "content": "Patient has hypertension. BP 152/92.",
        "source": {"system": "HIS", "documentId": "adm-9", "version": "1"},
    }
    r1 = app_client.post("/api/emr/ingest", json=base)
    progress = {
        **base,
        "encounter": {"type": "progress_note", "dateTime": "2026-01-05T09:00:00+07:00"},
        "content": "Day 4. BP 132/82 on lisinopril.",
        "source": {"system": "HIS", "documentId": "prog-9", "version": "1"},
    }
    r2 = app_client.post("/api/emr/ingest", json=progress)
    assert r1.status_code == 200 and r2.status_code == 200
    assert {"adm-9", "prog-9"} == set(fake_store.documents.keys())

    timeline = app_client.get("/api/patient/HN9/timeline").json()
    assert len(timeline["encounters"]) == 2
