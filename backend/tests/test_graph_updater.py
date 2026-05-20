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


# ---------------------------------------------------------------------------
# AI-declared relationships + condition severity/status (PR for issue #17)
# ---------------------------------------------------------------------------


def test_update_graph_writes_severity_and_status_on_conditions(stub_neo4j):
    """Condition properties set from the extraction should land on the
    MERGE properties so reviewers can see severity / status on the graph
    node — not just buried in the source EMR."""
    from app.services.graph_updater import update_graph_for_document
    from app.schemas.extraction import ClinicalExtractionResult, PatientFact

    extraction = ClinicalExtractionResult(
        patientId="HN1", encounterId="E1", documentId="D1", summary="",
        problems=[
            PatientFact(type="condition", value="Type 2 diabetes",
                        normalizedCode="E11.9", severity="moderate", status="active",
                        evidenceText="DM2 on metformin"),
            PatientFact(type="condition", value="Diabetic nephropathy",
                        normalizedCode="E11.21", severity="severe",
                        status="active", evidenceText="DN, eGFR 35"),
        ],
    )
    update_graph_for_document(
        {"patientId": "HN1"}, {"encounterId": "E1", "dateTime": None},
        {"documentId": "D1"}, extraction,
    )
    # Find the conditions UNWIND row that the writer sent.
    cypher_calls = [c for c in stub_neo4j if "MERGE (c:Condition" in c[0]]
    assert cypher_calls, "expected a Condition MERGE query"
    rows = cypher_calls[0][1].get("rows") or []
    diabetes = next(r for r in rows if r["value"] == "Type 2 diabetes")
    assert diabetes["severity"] == "moderate"
    assert diabetes["status"] == "active"
    nephropathy = next(r for r in rows if r["value"] == "Diabetic nephropathy")
    assert nephropathy["severity"] == "severe"


def test_update_graph_writes_ai_declared_relationships(stub_neo4j):
    """`relationships` from the extraction should turn into per-rel-type
    Cypher MERGEs marked aiDeclared=true so the read query can later
    distinguish them from the heuristic edges."""
    from app.services.graph_updater import update_graph_for_document
    from app.schemas.extraction import (
        ClinicalExtractionResult, PatientFact, MedicationChange,
        ObservationResult, FactRelationship,
    )

    extraction = ClinicalExtractionResult(
        patientId="HN1", encounterId="E1", documentId="D1", summary="",
        problems=[PatientFact(type="condition", value="Type 2 diabetes",
                              normalizedCode="E11.9")],
        medications=[MedicationChange(name="Metformin", rxNorm="6809",
                                      action="start")],
        observations=[ObservationResult(name="HbA1c", loinc="4548-4",
                                        value="8.4", unit="%")],
        relationships=[
            FactRelationship(sourceType="medication", sourceValue="Metformin",
                             targetType="condition", targetValue="Type 2 diabetes",
                             relation="treats", confidence=0.9),
            FactRelationship(sourceType="observation", sourceValue="HbA1c",
                             targetType="condition", targetValue="Type 2 diabetes",
                             relation="monitors", confidence=0.85),
        ],
    )
    update_graph_for_document(
        {"patientId": "HN1"}, {"encounterId": "E1", "dateTime": None},
        {"documentId": "D1"}, extraction,
    )
    # Two AI-relation Cypher queries should have fired — one per rel type.
    rel_queries = [c for c in stub_neo4j if "aiDeclared = true" in c[0]]
    assert len(rel_queries) == 2, (
        f"expected one MERGE per AI relationship type; got {len(rel_queries)}"
    )
    # Each query carries its rows.
    by_type: dict[str, list[dict]] = {}
    for q, params in rel_queries:
        rel_type = "TREATS" if "[rel:TREATS]" in q else ("MONITORS" if "[rel:MONITORS]" in q else None)
        assert rel_type, f"unknown rel type in query: {q[:80]}"
        by_type[rel_type] = params.get("rows") or []
    assert "TREATS" in by_type and "MONITORS" in by_type
    assert by_type["TREATS"][0]["sourceValue"] == "Metformin"
    assert by_type["MONITORS"][0]["sourceValue"] == "HbA1c"
