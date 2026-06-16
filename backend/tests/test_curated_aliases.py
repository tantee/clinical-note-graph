"""Root fix for the curated-rename graph divergence.

A renamed curated item records its prior name as an alias. reconcile re-links a
re-mention of the old name to the same row (no duplicate), and both the ingest
reconcile path and the rebuild post-pass relabel/collapse the stale old-value
Neo4j node into the curated node — so the graph never diverges after a rename.
"""
import pytest

from app.schemas.extraction import ClinicalExtractionResult, PatientFact
from app.services.curated import reconcile_curated


@pytest.fixture()
def patient(fake_store):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Jane"}
    return "HN1"


def _condition_row(id_, **kw):
    row = {
        "id": id_, "patient_id": "HN1", "type": "condition",
        "normalized_key": "asthma", "display_value": "Asthma",
        "normalized_code": None, "coding_system": None, "start_date": None,
        "start_qualifier": "unknown", "stop_date": None, "stop_qualifier": "ongoing",
        "start_text": None, "stop_text": None, "schedule_text": None,
        "status": "active", "record_state": "active", "review_status": "ai_suggested",
        "origin": "ai", "human_edited_fields": [], "aliases": [],
        "last_evidence_fact_id": None,
    }
    row.update(kw)
    return row


def _extraction(**kw):
    return ClinicalExtractionResult(patientId="HN1", encounterId="E1", documentId="D1", **kw)


# ---- rename records the prior name as an alias ------------------------------

def test_rename_records_prior_value_in_aliases(app_client, patient, fake_store, stub_neo4j):
    fake_store.curated_facts.append(_condition_row(
        "ren1", display_value="high blood pressure", normalized_key="high blood pressure",
    ))
    r = app_client.patch("/api/curated/ren1", json={"displayValue": "Hypertension"})
    assert r.status_code == 200, r.text
    row = next(x for x in fake_store.curated_facts if x["id"] == "ren1")
    assert "high blood pressure" in (row.get("aliases") or [])


# ---- reconcile re-links a code-less old-name re-mention via alias -----------

def test_reconcile_relinks_codeless_old_name(patient, fake_store, stub_neo4j):
    """After rename, an AI re-mention of the OLD code-less name must update the
    SAME curated row (matched by alias) — not create a duplicate — and relabel
    the stale Neo4j node onto the curated display value."""
    fake_store.curated_facts.append(_condition_row(
        "rl1", display_value="Hypertension", normalized_key="hypertension",
        human_edited_fields=["display_value"], aliases=["high blood pressure"],
    ))
    reconcile_curated("HN1", _extraction(
        problems=[PatientFact(type="condition", value="high blood pressure")],
    ))
    conditions = [r for r in fake_store.curated_facts if r["type"] == "condition"]
    assert len(conditions) == 1, "re-mention of the old name must not create a duplicate row"
    assert conditions[0]["display_value"] == "Hypertension"
    assert conditions[0]["normalized_key"] == "hypertension"
    relabel = [
        (q, p) for q, p in stub_neo4j
        if p.get("oldValue") == "high blood pressure" and p.get("newValue") == "Hypertension"
    ]
    assert relabel, f"expected a relabel cypher collapsing the old node, got {list(stub_neo4j)}"


# ---- reconcile records + relabels on a coded rename re-mention --------------

def test_reconcile_coded_rename_remention_records_alias_and_relabels(patient, fake_store, stub_neo4j):
    fake_store.curated_facts.append(_condition_row(
        "cd1", display_value="Hypertension", normalized_key="i10",
        normalized_code="I10", coding_system="ICD10",
        human_edited_fields=["display_value"], aliases=[],
    ))
    reconcile_curated("HN1", _extraction(
        problems=[PatientFact(type="condition", value="high blood pressure",
                              normalizedCode="I10", codingSystem="ICD10")],
    ))
    row = next(r for r in fake_store.curated_facts if r["id"] == "cd1")
    assert "high blood pressure" in (row.get("aliases") or [])
    relabel = [
        (q, p) for q, p in stub_neo4j
        if p.get("oldValue") == "high blood pressure" and p.get("newValue") == "Hypertension"
    ]
    assert relabel, "coded rename re-mention should relabel the old AI-value node"


# ---- rebuild applies the curated layer as a post-pass -----------------------

def test_rebuild_applies_curated_relabel_and_propagate(app_client, fake_store, stub_neo4j):
    fake_store.patients["HN1"] = {"patient_id": "HN1", "name": "Jane"}
    fake_store.encounters["E1"] = {
        "encounter_id": "E1", "patient_id": "HN1", "type": "admission",
        "date_time": "2026-04-01T08:00:00+00:00", "department": "IM", "provider": "Dr A",
    }
    fake_store.documents["D1"] = {
        "document_id": "D1", "patient_id": "HN1", "encounter_id": "E1",
        "format": "text", "version": "1",
    }
    fake_store.facts.append({
        "id": "f-1", "patient_id": "HN1", "encounter_id": "E1", "document_id": "D1",
        "type": "condition", "value": "high blood pressure", "normalized_code": None,
        "review_status": "ai_suggested", "extra": {}, "confidence": 0.9,
        "created_at": "2026-04-01T08:00:00Z",
    })
    # Curated row was renamed away from the AI value the fact still carries.
    fake_store.curated_facts.append(_condition_row(
        "rb1", display_value="Hypertension", normalized_key="hypertension",
        human_edited_fields=["display_value"], aliases=["high blood pressure"],
    ))
    r = app_client.post("/api/patient/HN1/graph/rebuild")
    assert r.status_code == 200, r.text
    relabel = [
        (q, p) for q, p in stub_neo4j
        if p.get("oldValue") == "high blood pressure" and p.get("newValue") == "Hypertension"
    ]
    assert relabel, "rebuild post-pass must relabel the stale old-value node"
    # propagate_curated_to_graph carries value + reviewStatus/recordState params
    # (relabel carries oldValue/newValue) — assert the curated values were pushed.
    curated_prop = [
        (q, p) for q, p in stub_neo4j
        if "Condition" in q and p.get("value") == "Hypertension" and "reviewStatus" in p
    ]
    assert curated_prop, "rebuild post-pass must propagate curated values to the graph"
