"""Integration tests for GET /api/patient/{pid}/graph with the new scope
and filter query parameters."""
from __future__ import annotations


def _patient_row(*, with_conditions: int = 0, with_meds: int = 0,
                 with_obs: int = 0) -> dict:
    """Build a single primed Cypher row mimicking what the real query returns."""
    row = {
        "p": {"patientId": "HN-1"},
        "encounters": [{"encounterId": "E1", "type": "admission"}],
        "conditions": [
            {"encounterId": "E1", "value": f"Cond{i}", "normalized_code": f"X{i}",
             "reviewStatus": "ai_suggested"}
            for i in range(with_conditions)
        ],
        "medications": [
            {"encounterId": "E1", "name": f"Med{i}", "rxNorm": str(1000 + i),
             "reviewStatus": "ai_suggested"}
            for i in range(with_meds)
        ],
        "observations": [
            {"encounterId": "E1", "name": f"Obs{i}", "value": str(i),
             "reviewStatus": "ai_suggested"}
            for i in range(with_obs)
        ],
        "plans": [],
        "allergies": [],
    }
    return row


def test_default_patient_scope_dedupes_no_encounter_nodes(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    # Two encounters mention Hypertension (same normalized_code=I10) — should collapse.
    row = {
        "p": {"patientId": "HN-1"},
        "encounters": [
            {"encounterId": "E1"}, {"encounterId": "E2"},
        ],
        "conditions": [
            {"encounterId": "E1", "value": "Hypertension", "normalized_code": "I10"},
            {"encounterId": "E2", "value": "Hypertension", "normalized_code": "I10"},
        ],
        "medications": [], "observations": [], "plans": [], "allergies": [],
    }
    stub_neo4j.prime([row])
    r = app_client.get("/api/patient/HN-1/graph")
    assert r.status_code == 200
    body = r.json()
    # Default: include_encounters=False so encounters are still returned in the row
    # but NOT in the response nodes; dedupe=True collapses the two HTN nodes.
    labels = [n["label"] for n in body["nodes"]]
    assert "Encounter" not in labels  # encounters omitted in patient-scope default
    htn = [n for n in body["nodes"] if n["label"] == "Condition"]
    assert len(htn) == 1


def test_scope_encounter_includes_encounter_node(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    stub_neo4j.prime([_patient_row(with_conditions=1, with_meds=1)])
    r = app_client.get("/api/patient/HN-1/graph", params={"scope": "encounter", "encounterId": "E1"})
    assert r.status_code == 200
    labels = [n["label"] for n in r.json()["nodes"]]
    assert "Encounter" in labels
    assert "Condition" in labels


def test_scope_encounter_requires_encounter_id(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    stub_neo4j.prime([_patient_row()])
    r = app_client.get("/api/patient/HN-1/graph", params={"scope": "encounter"})
    assert r.status_code == 400
    assert "encounter_ids" in r.json()["detail"]


def test_oversized_returns_422_with_node_count(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    # Generate a row that materializes > 500 nodes pre-dedupe. 600 conditions
    # with unique normalized_codes so dedupe doesn't collapse them.
    stub_neo4j.prime([_patient_row(with_conditions=600)])
    r = app_client.get("/api/patient/HN-1/graph", params={"dedupe": "false"})
    assert r.status_code == 422
    body = r.json()
    # FastAPI wraps custom dict detail in {"detail": <dict>}
    assert body["detail"]["detail"] == "Graph too large; narrow the scope"
    assert body["detail"]["nodeCount"] > 500


def test_include_documents_adds_document_nodes(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    row = _patient_row()
    row["docs"] = [{"encounterId": "E1", "documentId": "D1"}]
    stub_neo4j.prime([row])
    r = app_client.get(
        "/api/patient/HN-1/graph",
        params={"scope": "encounter", "encounterId": "E1", "includeDocuments": "true"},
    )
    assert r.status_code == 200
    labels = [n["label"] for n in r.json()["nodes"]]
    assert "Document" in labels


def test_review_status_confirmed_only_includes_confirmed_in_cypher(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    stub_neo4j.prime([_patient_row()])
    app_client.get("/api/patient/HN-1/graph", params={"reviewStatus": "confirmed"})
    # Inspect the last Cypher query: must include reviewStatus filter.
    last_query = stub_neo4j[-1][0]
    assert "human_confirmed" in last_query
