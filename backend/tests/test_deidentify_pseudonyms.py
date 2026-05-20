"""Pseudonym + structured-field tests for the de-identifier.

Pseudonyms must be deterministic within one Deidentifier instance (so the
LLM sees the same `PATIENT-A1` token across every mention in the prompt)
and reset between instances (so cross-request linkage doesn't leak).
"""

from __future__ import annotations

from app.services.deidentify import Deidentifier


def test_same_value_gets_same_pseudonym_within_request():
    d = Deidentifier(level="regex_only")
    a = d.pseudonym_for("Somchai Sample", "PATIENT")
    b = d.pseudonym_for("Somchai Sample", "PATIENT")
    c = d.pseudonym_for("somchai sample", "PATIENT")  # case-insensitive
    assert a == b == c
    assert a.startswith("PATIENT-A")


def test_different_values_get_different_pseudonyms():
    d = Deidentifier(level="regex_only")
    a = d.pseudonym_for("Patient Alpha", "PATIENT")
    b = d.pseudonym_for("Patient Beta", "PATIENT")
    assert a != b


def test_pseudonym_resets_across_instances():
    d1 = Deidentifier(level="regex_only")
    p1 = d1.pseudonym_for("Somchai Sample", "PATIENT")
    d2 = Deidentifier(level="regex_only")
    p2 = d2.pseudonym_for("Somchai Sample", "PATIENT")
    # Same shape, different instances — the second one starts from PATIENT-A1
    # again because the per-request map is fresh.
    assert p1 == p2 == "PATIENT-A1"


def test_pseudonymize_patient_redacts_name_hn_dob_dates():
    d = Deidentifier(level="regex_only")
    src = {
        "patient_id": "HN-DEMO-1",
        "name": "Somchai Sample",
        "birth_date": "1962-03-04",
        "received_at": "2026-05-19T14:03:00+07:00",
        "gender": "male",
    }
    out = d.pseudonymize_patient(src)
    assert out is not None
    assert out["patient_id"].startswith("HN-A")
    assert out["name"].startswith("PATIENT-A")
    # Birth date redacted to year + age bucket
    assert "1962" in out["birth_date"]
    assert "(" in out["birth_date"]
    # Encounter datetime rounded to year
    assert out["received_at"] == "2026"
    # Non-PHI fields preserved verbatim
    assert out["gender"] == "male"


def test_pseudonymize_facts_walks_evidence_text():
    d = Deidentifier(level="regex_only")
    src = {
        "patient": {"name": "Somchai Sample", "patient_id": "HN-DEMO-1"},
        "problems": [
            {
                "value": "Type 2 diabetes",
                "evidenceText": "Patient Somchai Sample (HN-DEMO-1) seen on 2026-05-19. Contact: a@b.com",
                "dateTime": "2026-05-19T14:03",
            }
        ],
    }
    out = d.pseudonymize_facts(src)
    assert out is not None
    ev = out["problems"][0]["evidenceText"]
    # Name and HN should be replaced with the same pseudonym used in patient
    patient_token = out["patient"]["name"]
    assert patient_token in ev
    assert "Somchai Sample" not in ev
    assert "HN-DEMO-1" not in ev
    assert "a@b.com" not in ev
    assert "<EMAIL_ADDRESS>" in ev
    # Embedded dateTime field on the fact gets rounded to year
    assert out["problems"][0]["dateTime"] == "2026"


def test_pseudonymize_birth_date_age_bucket():
    d = Deidentifier(level="regex_only")
    young = d.pseudonymize_patient({"birth_date": "2010-01-01"})
    elder = d.pseudonymize_patient({"birth_date": "1940-01-01"})
    assert young is not None and elder is not None
    assert "<30" in young["birth_date"]
    assert "65+" in elder["birth_date"]


def test_off_level_is_passthrough_for_structured():
    d = Deidentifier(level="off")
    src = {"name": "Real Patient", "patient_id": "HN-REAL"}
    out = d.pseudonymize_patient(src)
    assert out == src
    assert d.counts_snapshot() == {}
