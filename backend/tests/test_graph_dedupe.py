"""Unit tests for _dedupe_nodes_edges — the pure function that collapses
same-condition / same-medication nodes across encounters and rewrites
edges to point at the surviving node."""
from __future__ import annotations

import pytest


def _condition(value: str, normalized_code: str | None, suffix: str = "") -> dict:
    """Helper: synthesize a Condition node like fetch_graph emits."""
    return {
        "id": f"Condition:{value}{suffix}",
        "label": "Condition",
        "data": {"value": value, "normalized_code": normalized_code},
    }


def _med(name: str, rxnorm: str | None, suffix: str = "") -> dict:
    return {
        "id": f"Medication:{name}{suffix}",
        "label": "Medication",
        "data": {"name": name, "rxNorm": rxnorm},
    }


def test_collapse_conditions_by_normalized_code():
    from app.services.graph_updater import _dedupe_nodes_edges
    nodes = [
        {"id": "Patient:p1", "label": "Patient", "data": {"patientId": "p1"}},
        _condition("Hypertension", "I10", suffix=":e1"),
        _condition("Hypertension", "I10", suffix=":e2"),  # duplicate from another encounter
        _condition("Diabetes",     "E11", suffix=":e1"),
    ]
    edges = [
        {"from": "Patient:p1", "to": "Condition:Hypertension:e1", "type": "HAS_CONDITION"},
        {"from": "Patient:p1", "to": "Condition:Hypertension:e2", "type": "HAS_CONDITION"},
        {"from": "Patient:p1", "to": "Condition:Diabetes:e1",     "type": "HAS_CONDITION"},
    ]
    out = _dedupe_nodes_edges({"nodes": nodes, "edges": edges})
    # Only ONE Hypertension node survives.
    cond_nodes = [n for n in out["nodes"] if n["label"] == "Condition"]
    assert len(cond_nodes) == 2
    htn_nodes = [n for n in cond_nodes if n["data"]["normalized_code"] == "I10"]
    assert len(htn_nodes) == 1
    # Both Hypertension edges now point at the surviving node id.
    htn_id = htn_nodes[0]["id"]
    htn_edges = [e for e in out["edges"] if e["to"] == htn_id]
    assert len(htn_edges) == 2  # de-duplication does NOT collapse parallel edges; that's a future enhancement


def test_collapse_conditions_by_value_when_code_missing():
    from app.services.graph_updater import _dedupe_nodes_edges
    nodes = [
        _condition("Asthma", None, suffix=":e1"),
        _condition("asthma", None, suffix=":e2"),  # different case, same logical condition
    ]
    edges = []
    out = _dedupe_nodes_edges({"nodes": nodes, "edges": edges})
    assert len([n for n in out["nodes"] if n["label"] == "Condition"]) == 1


def test_medications_dedupe_by_rxnorm():
    from app.services.graph_updater import _dedupe_nodes_edges
    nodes = [
        _med("Lisinopril", "29046", suffix=":e1"),
        _med("Lisinopril", "29046", suffix=":e2"),
        _med("Lisinopril", "12345", suffix=":e3"),  # different rxNorm → stays separate
    ]
    edges = []
    out = _dedupe_nodes_edges({"nodes": nodes, "edges": edges})
    med_nodes = [n for n in out["nodes"] if n["label"] == "Medication"]
    assert len(med_nodes) == 2  # rxNorm 29046 collapsed; 12345 kept distinct


def test_medications_dedupe_by_name_when_rxnorm_missing():
    from app.services.graph_updater import _dedupe_nodes_edges
    nodes = [
        _med("Aspirin", None, suffix=":e1"),
        _med("aspirin", None, suffix=":e2"),
    ]
    out = _dedupe_nodes_edges({"nodes": nodes, "edges": []})
    assert len([n for n in out["nodes"] if n["label"] == "Medication"]) == 1


def test_observations_are_not_deduped():
    """Same observation name at different times is signal, not noise."""
    from app.services.graph_updater import _dedupe_nodes_edges
    nodes = [
        {"id": "Observation:BP:e1", "label": "Observation",
         "data": {"name": "Blood pressure", "value": "150/95"}},
        {"id": "Observation:BP:e2", "label": "Observation",
         "data": {"name": "Blood pressure", "value": "132/82"}},
    ]
    out = _dedupe_nodes_edges({"nodes": nodes, "edges": []})
    assert len(out["nodes"]) == 2


def test_documents_and_plans_pass_through_unchanged():
    from app.services.graph_updater import _dedupe_nodes_edges
    nodes = [
        {"id": "Document:d1", "label": "Document", "data": {"documentId": "d1"}},
        {"id": "Document:d2", "label": "Document", "data": {"documentId": "d2"}},
        {"id": "Plan:p1",     "label": "Plan",     "data": {"description": "Follow up"}},
        {"id": "Plan:p2",     "label": "Plan",     "data": {"description": "Follow up"}},
    ]
    out = _dedupe_nodes_edges({"nodes": nodes, "edges": []})
    assert len(out["nodes"]) == 4


def test_empty_input():
    from app.services.graph_updater import _dedupe_nodes_edges
    out = _dedupe_nodes_edges({"nodes": [], "edges": []})
    assert out == {"nodes": [], "edges": []}
