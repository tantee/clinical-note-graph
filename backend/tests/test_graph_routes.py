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


# ---------------------------------------------------------------------------
# Graph rebuild endpoint — the recovery path for the silent-upsert-fail mode.
# ---------------------------------------------------------------------------


def test_rebuild_graph_404_when_patient_unknown(app_client, fake_store):
    r = app_client.post("/api/patient/HN-MISSING/graph/rebuild")
    assert r.status_code == 404


def test_rebuild_graph_returns_zero_when_no_documents(app_client, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    r = app_client.post("/api/patient/HN-1/graph/rebuild")
    assert r.status_code == 200
    body = r.json()
    assert body["documents"] == 0
    assert body["perDocument"] == []


def test_rebuild_graph_replays_facts_per_document(app_client, fake_store, stub_neo4j):
    """Seed one patient with one encounter, one document, three conditions.
    Calling rebuild should hit the Cypher writes — verified by inspecting
    the calls captured by stub_neo4j."""
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    fake_store.encounters["E1"] = {
        "encounter_id": "E1", "patient_id": "HN-1", "type": "admission",
        "date_time": "2026-04-01T08:00:00+00:00",
        "department": "IM", "provider": "Dr A",
    }
    fake_store.documents["D1"] = {
        "document_id": "D1", "patient_id": "HN-1", "encounter_id": "E1",
        "format": "text", "version": "1",
    }
    fake_store.facts.extend([
        {"id": "f-1", "patient_id": "HN-1", "encounter_id": "E1",
         "document_id": "D1", "type": "condition",
         "value": "Hypertension", "normalized_code": "I10",
         "review_status": "ai_suggested", "extra": {}, "confidence": 0.9,
         "created_at": "2026-04-01T08:00:00Z"},
        {"id": "f-2", "patient_id": "HN-1", "encounter_id": "E1",
         "document_id": "D1", "type": "medication",
         "value": "Lisinopril", "normalized_code": None,
         "review_status": "ai_suggested",
         "extra": {"action": "start", "indication": "Hypertension"},
         "confidence": 0.85, "created_at": "2026-04-01T08:01:00Z"},
        # A rejected fact must be excluded from the rebuild.
        {"id": "f-3", "patient_id": "HN-1", "encounter_id": "E1",
         "document_id": "D1", "type": "condition",
         "value": "Anxiety", "normalized_code": "F41.9",
         "review_status": "rejected", "extra": {}, "confidence": 0.6,
         "created_at": "2026-04-01T08:02:00Z"},
    ])

    r = app_client.post("/api/patient/HN-1/graph/rebuild")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["documents"] == 1
    assert len(body["perDocument"]) == 1
    counts = body["perDocument"][0]["counts"]
    # Rejected condition excluded, so 1 not 2.
    assert counts["conditions"] == 1
    assert counts["medications"] == 1

    # Confirm Cypher writes actually fired — root + conditions + medications.
    queries = [q for q, _ in stub_neo4j]
    assert any("MERGE (p:Patient" in q for q in queries)
    assert any("MERGE (c:Condition" in q for q in queries)
    assert any("MERGE (med:Medication" in q for q in queries)
