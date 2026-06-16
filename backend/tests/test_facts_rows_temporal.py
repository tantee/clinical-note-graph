"""bug_020: the temporal fields on conditions/medications must be persisted into
facts.extra so the evidence trail and the rebuild_graph path retain them."""
import json
from datetime import datetime

from app.schemas.extraction import ClinicalExtractionResult, MedicationChange, PatientFact
from app.services.ingest import _facts_rows


def _rows(**kw):
    ex = ClinicalExtractionResult(patientId="HN1", encounterId="E1", documentId="D1", **kw)
    return _facts_rows(patient_id="HN1", encounter_id="E1", document_id="D1", ex=ex)


def _extra(row):
    return json.loads(row["extra"]) if isinstance(row["extra"], str) else row["extra"]


def test_condition_temporal_fields_persisted_to_extra():
    rows = _rows(problems=[PatientFact(
        type="condition", value="Asthma", status="active", severity="severe",
        onsetDate=datetime(2024, 3, 1), onsetQualifier="estimated", onsetText="since childhood",
        resolvedQualifier="ongoing",
    )])
    extra = _extra(rows[0])
    assert extra["status"] == "active"
    assert extra["severity"] == "severe"
    assert extra["onsetDate"] == "2024-03-01"
    assert extra["onsetQualifier"] == "estimated"
    assert extra["onsetText"] == "since childhood"
    assert extra["resolvedQualifier"] == "ongoing"


def test_condition_null_temporal_fields_omitted_from_extra():
    rows = _rows(problems=[PatientFact(type="condition", value="Asthma")])
    extra = _extra(rows[0])
    assert "onsetDate" not in extra
    assert "severity" not in extra


def test_medication_temporal_fields_persisted_to_extra():
    rows = _rows(medications=[MedicationChange(
        name="Warfarin", action="start", startDate=datetime(2026, 2, 1),
        startQualifier="exact", stopQualifier="ongoing", schedule="5mg daily",
        startText="started this admission",
    )])
    extra = _extra(rows[0])
    # existing fields still present
    assert extra["action"] == "start"
    # new temporal fields
    assert extra["startDate"] == "2026-02-01"
    assert extra["startQualifier"] == "exact"
    assert extra["stopQualifier"] == "ongoing"
    assert extra["schedule"] == "5mg daily"
    assert extra["startText"] == "started this admission"
