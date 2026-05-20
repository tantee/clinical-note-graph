"""Confirms the existing fetch_patient_graph(patient_id) entry point and
the GET /patient/{pid}/graph route (no params) still work for callers that
predate the scope/filter parameters."""
from __future__ import annotations


def test_fetch_patient_graph_returns_nodes_edges_shape(stub_neo4j):
    from app.services.graph_updater import fetch_patient_graph
    stub_neo4j.prime([{
        "p": {"patientId": "HN-X"},
        "encounters": [],
        "conditions": [], "medications": [], "observations": [],
        "plans": [], "allergies": [],
    }])
    result = fetch_patient_graph("HN-X")
    assert set(result.keys()) == {"nodes", "edges"}
    assert all(isinstance(n, dict) and "id" in n and "label" in n for n in result["nodes"])


def test_get_graph_route_with_no_params_returns_200(app_client, stub_neo4j, fake_store):
    fake_store.patients["HN-1"] = {"patient_id": "HN-1", "name": "Test"}
    stub_neo4j.prime([{
        "p": {"patientId": "HN-1"},
        "encounters": [], "conditions": [], "medications": [],
        "observations": [], "plans": [], "allergies": [],
    }])
    r = app_client.get("/api/patient/HN-1/graph")
    assert r.status_code == 200
    body = r.json()
    assert "nodes" in body and "edges" in body
