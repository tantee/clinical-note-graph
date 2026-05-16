from __future__ import annotations

from app.schemas.extraction import (
    ClinicalExtractionResult, MedicationChange, ObservationResult, PatientFact, PlanItem,
)
from app.services.graph_updater import update_graph_for_document


def _extraction() -> ClinicalExtractionResult:
    return ClinicalExtractionResult(
        patientId="HN1",
        encounterId="E1",
        documentId="D1",
        problems=[PatientFact(type="condition", value="Type 2 diabetes mellitus", normalizedCode="E11.9", codingSystem="ICD10", confidence=0.8)],
        medications=[MedicationChange(name="metformin", action="start", rxNorm="6809", indication="diabetes")],
        observations=[ObservationResult(name="HbA1c", loinc="4548-4", value="8.4", unit="%")],
        plans=[PlanItem(description="Diabetes education", category="education", addressesCondition="diabetes")],
    )


def test_graph_updater_uses_one_session(stub_neo4j):
    counts = update_graph_for_document(
        patient={"patientId": "HN1"},
        encounter={"encounterId": "E1", "type": "admission", "dateTime": "2026-05-15T10:00:00+07:00"},
        document={"documentId": "D1", "sourceSystem": "X", "version": "1", "format": "text"},
        extraction=_extraction(),
    )
    assert counts["conditions"] == 1
    assert counts["medications"] == 1
    assert counts["observations"] == 1
    assert counts["plans"] == 1
    # One root call + one per non-empty fact-type list. No per-fact calls.
    queries = [q for q, _ in stub_neo4j]
    assert any("MERGE (p:Patient" in q for q in queries)
    assert any("UNWIND $rows AS r" in q for q in queries)


def test_empty_extraction_only_writes_root(stub_neo4j):
    update_graph_for_document(
        patient={"patientId": "HN2"},
        encounter={"encounterId": "E2", "type": "lab", "dateTime": "2026-05-15T10:00:00+07:00"},
        document={"documentId": "D2", "sourceSystem": "X", "version": "1", "format": "text"},
        extraction=ClinicalExtractionResult(patientId="HN2", encounterId="E2", documentId="D2"),
    )
    queries = [q for q, _ in stub_neo4j]
    assert len([q for q in queries if "UNWIND" in q]) == 0
