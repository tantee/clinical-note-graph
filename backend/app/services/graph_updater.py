"""Idempotent, longitudinal Neo4j upserts.

All writes for a single document happen inside one session, using `UNWIND $rows`
so the cost is O(1) Cypher round-trips per fact type instead of O(N).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.db.neo4j_client import neo4j_session, run_cypher
from app.schemas.extraction import ClinicalExtractionResult
from app.utils.datetime import iso

logger = logging.getLogger(__name__)


_CONSTRAINTS_LOCK = threading.Lock()
_CONSTRAINTS_READY = False


def ensure_constraints(force: bool = False) -> None:
    """Idempotent constraints/indexes. Safe to call multiple times but only does work once."""
    global _CONSTRAINTS_READY
    if _CONSTRAINTS_READY and not force:
        return
    with _CONSTRAINTS_LOCK:
        if _CONSTRAINTS_READY and not force:
            return
        queries = [
            "CREATE CONSTRAINT patient_id IF NOT EXISTS FOR (p:Patient) REQUIRE p.patientId IS UNIQUE",
            "CREATE CONSTRAINT encounter_id IF NOT EXISTS FOR (e:Encounter) REQUIRE e.encounterId IS UNIQUE",
            "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.documentId IS UNIQUE",
            "CREATE INDEX condition_value IF NOT EXISTS FOR (c:Condition) ON (c.patientId, c.value)",
            "CREATE INDEX medication_name IF NOT EXISTS FOR (m:Medication) ON (m.patientId, m.name)",
            "CREATE INDEX observation_name IF NOT EXISTS FOR (o:Observation) ON (o.patientId, o.name)",
        ]
        with neo4j_session() as session:
            for q in queries:
                session.run(q)
        _CONSTRAINTS_READY = True


def _root_params(patient: dict[str, Any], encounter: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    return {
        "patientId": patient["patientId"],
        "name": patient.get("name"),
        "gender": patient.get("gender"),
        "birthDate": str(patient.get("birthDate")) if patient.get("birthDate") else None,
        "encounterId": encounter["encounterId"],
        "encounterType": encounter.get("type"),
        "encounterDt": iso(encounter.get("dateTime")),
        "department": encounter.get("department"),
        "provider": encounter.get("provider"),
        "documentId": document["documentId"],
        "sourceSystem": document.get("sourceSystem"),
        "version": document.get("version") or "1",
        "format": document.get("format"),
    }


def update_graph_for_document(
    patient: dict[str, Any],
    encounter: dict[str, Any],
    document: dict[str, Any],
    extraction: ClinicalExtractionResult,
) -> dict[str, int]:
    ensure_constraints()
    pid = patient["patientId"]
    enc_id = encounter["encounterId"]
    doc_id = document["documentId"]

    conditions = [
        {
            "value": f.value, "code": f.normalizedCode, "system": f.codingSystem,
            "reviewStatus": f.reviewStatus, "confidence": f.confidence, "evidence": f.evidenceText,
        }
        for f in extraction.problems
    ]
    medications = [
        {
            "name": m.name, "rxNorm": m.rxNorm, "action": m.action,
            "dose": m.dose, "route": m.route, "frequency": m.frequency,
            "indication": m.indication, "evidence": m.evidenceText,
        }
        for m in extraction.medications
    ]
    observations = [
        {
            "name": o.name, "loinc": o.loinc, "value": o.value, "unit": o.unit,
            "abnormalFlag": o.abnormalFlag, "dt": iso(o.dateTime),
        }
        for o in extraction.observations
    ]
    procedures = [
        {"value": p.value, "code": p.normalizedCode, "system": p.codingSystem}
        for p in extraction.procedures
    ]
    allergies = [{"value": a.value} for a in extraction.allergies]
    plans = [
        {"description": p.description, "category": p.category, "addresses": p.addressesCondition}
        for p in extraction.plans
    ]
    coding_candidates = [
        {"system": c.system, "code": c.code, "display": c.display, "forCondition": c.forCondition, "confidence": c.confidence}
        for c in extraction.codingCandidates
    ]

    params = _root_params(patient, encounter, document)
    counts = {
        "conditions": len(conditions), "medications": len(medications),
        "observations": len(observations), "procedures": len(procedures),
        "allergies": len(allergies), "plans": len(plans), "codingCandidates": len(coding_candidates),
    }

    with neo4j_session() as s:
        s.run(_CYPHER_ROOT, params)
        if conditions:
            s.run(_CYPHER_CONDITIONS, {**params, "rows": conditions})
        if medications:
            s.run(_CYPHER_MEDICATIONS, {**params, "rows": medications})
        if observations:
            s.run(_CYPHER_OBSERVATIONS, {**params, "rows": observations})
        if procedures:
            s.run(_CYPHER_PROCEDURES, {**params, "rows": procedures})
        if allergies:
            s.run(_CYPHER_ALLERGIES, {**params, "rows": allergies})
        if plans:
            s.run(_CYPHER_PLANS, {**params, "rows": plans})
        if coding_candidates:
            s.run(_CYPHER_CODING, {**params, "rows": coding_candidates})

    return counts


_CYPHER_ROOT = """
MERGE (p:Patient {patientId: $patientId})
  ON CREATE SET p.createdAt = datetime()
  SET p.name = coalesce($name, p.name),
      p.gender = coalesce($gender, p.gender),
      p.birthDate = coalesce($birthDate, p.birthDate),
      p.updatedAt = datetime()
MERGE (e:Encounter {encounterId: $encounterId})
  ON CREATE SET e.createdAt = datetime()
  SET e.type = $encounterType, e.dateTime = datetime($encounterDt),
      e.department = $department, e.provider = $provider
MERGE (p)-[:HAS_ENCOUNTER]->(e)
MERGE (d:Document {documentId: $documentId})
  ON CREATE SET d.createdAt = datetime()
  SET d.sourceSystem = $sourceSystem, d.version = $version, d.format = $format
MERGE (e)-[:HAS_DOCUMENT]->(d)
"""

_CYPHER_CONDITIONS = """
MATCH (e:Encounter {encounterId: $encounterId}), (d:Document {documentId: $documentId})
UNWIND $rows AS r
MERGE (c:Condition {patientId: $patientId, value: r.value})
  ON CREATE SET c.firstSeen = datetime()
  SET c.normalizedCode = coalesce(r.code, c.normalizedCode),
      c.codingSystem = coalesce(r.system, c.codingSystem),
      c.lastSeen = datetime(),
      c.reviewStatus = coalesce(c.reviewStatus, r.reviewStatus),
      c.confidence = coalesce(c.confidence, r.confidence)
MERGE (e)-[:MENTIONS]->(c)
MERGE (d)-[ext:EXTRACTED]->(c)
  SET ext.evidence = r.evidence, ext.confidence = r.confidence,
      ext.createdAt = coalesce(ext.createdAt, datetime())
"""

_CYPHER_MEDICATIONS = """
MATCH (e:Encounter {encounterId: $encounterId}), (d:Document {documentId: $documentId})
UNWIND $rows AS r
MERGE (med:Medication {patientId: $patientId, name: r.name})
  ON CREATE SET med.firstSeen = datetime()
  SET med.rxNorm = coalesce(r.rxNorm, med.rxNorm),
      med.lastSeen = datetime(),
      med.lastAction = r.action,
      med.dose = coalesce(r.dose, med.dose),
      med.route = coalesce(r.route, med.route),
      med.frequency = coalesce(r.frequency, med.frequency)
MERGE (e)-[rel:PRESCRIBED]->(med)
  SET rel.action = r.action, rel.evidence = r.evidence,
      rel.createdAt = coalesce(rel.createdAt, datetime())
MERGE (d)-[:EXTRACTED]->(med)
WITH med, r
OPTIONAL MATCH (c:Condition {patientId: $patientId})
  WHERE r.indication IS NOT NULL AND toLower(c.value) CONTAINS toLower(r.indication)
FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END |
  MERGE (med)-[:TREATS]->(c)
)
"""

_CYPHER_OBSERVATIONS = """
MATCH (e:Encounter {encounterId: $encounterId}), (d:Document {documentId: $documentId})
UNWIND $rows AS r
MERGE (obs:Observation {patientId: $patientId, name: r.name, dateTime: coalesce(datetime(r.dt), datetime())})
  SET obs.loinc = coalesce(r.loinc, obs.loinc),
      obs.value = r.value,
      obs.unit = r.unit,
      obs.abnormalFlag = r.abnormalFlag
MERGE (e)-[:HAS_OBSERVATION]->(obs)
MERGE (d)-[:EXTRACTED]->(obs)
"""

_CYPHER_PROCEDURES = """
MATCH (e:Encounter {encounterId: $encounterId}), (d:Document {documentId: $documentId})
UNWIND $rows AS r
MERGE (n:Procedure {patientId: $patientId, value: r.value})
  SET n.normalizedCode = coalesce(r.code, n.normalizedCode),
      n.codingSystem = coalesce(r.system, n.codingSystem),
      n.lastSeen = datetime()
MERGE (e)-[:PERFORMED]->(n)
MERGE (d)-[:EXTRACTED]->(n)
"""

_CYPHER_ALLERGIES = """
MATCH (p:Patient {patientId: $patientId}), (d:Document {documentId: $documentId})
UNWIND $rows AS r
MERGE (n:Allergy {patientId: $patientId, value: r.value})
  SET n.lastSeen = datetime()
MERGE (p)-[:HAS_ALLERGY]->(n)
MERGE (d)-[:EXTRACTED]->(n)
"""

_CYPHER_PLANS = """
MATCH (e:Encounter {encounterId: $encounterId}), (d:Document {documentId: $documentId})
UNWIND $rows AS r
MERGE (pl:Plan {patientId: $patientId, description: r.description})
  SET pl.category = r.category, pl.lastSeen = datetime()
MERGE (e)-[:HAS_PLAN]->(pl)
MERGE (d)-[:EXTRACTED]->(pl)
WITH pl, r
OPTIONAL MATCH (c:Condition {patientId: $patientId})
  WHERE r.addresses IS NOT NULL AND toLower(c.value) CONTAINS toLower(r.addresses)
FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END |
  MERGE (pl)-[:ADDRESSES]->(c)
)
"""

_CYPHER_CODING = """
UNWIND $rows AS r
MERGE (cc:CodingCandidate {patientId: $patientId, system: r.system, code: r.code})
  SET cc.display = r.display, cc.lastSeen = datetime(), cc.confidence = r.confidence
WITH cc, r
OPTIONAL MATCH (c:Condition {patientId: $patientId})
  WHERE toLower(c.value) CONTAINS toLower(r.forCondition)
     OR toLower(r.forCondition) CONTAINS toLower(c.value)
FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END |
  MERGE (cc)-[:CODES]->(c)
)
"""


def _jsonable(value: Any) -> Any:
    """Recursively convert neo4j temporal types (and their containers) to JSON-friendly values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    iso_method = getattr(value, "iso_format", None) or getattr(value, "isoformat", None)
    if callable(iso_method):
        return iso_method()
    return str(value)


def fetch_patient_graph(patient_id: str) -> dict[str, Any]:
    rows = run_cypher(
        """
        MATCH (p:Patient {patientId: $pid})
        OPTIONAL MATCH (p)-[:HAS_ENCOUNTER]->(e:Encounter)
        OPTIONAL MATCH (e)-[:HAS_DOCUMENT]->(d:Document)
        OPTIONAL MATCH (e)-[:MENTIONS]->(c:Condition)
        OPTIONAL MATCH (e)-[:HAS_OBSERVATION]->(o:Observation)
        OPTIONAL MATCH (e)-[:PRESCRIBED]->(m:Medication)
        OPTIONAL MATCH (e)-[:HAS_PLAN]->(pl:Plan)
        OPTIONAL MATCH (p)-[:HAS_ALLERGY]->(a:Allergy)
        RETURN p, collect(DISTINCT e) AS encounters, collect(DISTINCT d) AS docs,
               collect(DISTINCT c) AS conditions, collect(DISTINCT o) AS observations,
               collect(DISTINCT m) AS medications, collect(DISTINCT pl) AS plans,
               collect(DISTINCT a) AS allergies
        """,
        {"pid": patient_id},
    )
    if not rows:
        return {"nodes": [], "edges": []}
    row = rows[0]
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(label: str, item: dict[str, Any], key: str) -> str | None:
        if not item:
            return None
        clean = _jsonable(item)
        nid = f"{label}:{clean.get(key)}"
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, "label": label, "data": clean})
        return nid

    pid = add("Patient", row["p"], "patientId")
    for e in row["encounters"]:
        eid = add("Encounter", e, "encounterId")
        if pid and eid:
            edges.append({"from": pid, "to": eid, "type": "HAS_ENCOUNTER"})
    for c in row["conditions"]:
        cid = add("Condition", c, "value")
        if pid and cid:
            edges.append({"from": pid, "to": cid, "type": "HAS_CONDITION"})
    for m in row["medications"]:
        mid = add("Medication", m, "name")
        if pid and mid:
            edges.append({"from": pid, "to": mid, "type": "ON_MEDICATION"})
    for o in row["observations"]:
        oid = add("Observation", o, "name")
        if pid and oid:
            edges.append({"from": pid, "to": oid, "type": "HAS_OBSERVATION"})
    for pl in row["plans"]:
        plid = add("Plan", pl, "description")
        if pid and plid:
            edges.append({"from": pid, "to": plid, "type": "HAS_PLAN"})
    for a in row["allergies"]:
        aid = add("Allergy", a, "value")
        if pid and aid:
            edges.append({"from": pid, "to": aid, "type": "HAS_ALLERGY"})
    return {"nodes": nodes, "edges": edges}
