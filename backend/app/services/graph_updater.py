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


def wipe_patient_subgraph(patient_id: str) -> dict[str, int]:
    """Delete the patient's encounters, documents, and all derived nodes
    (conditions, medications, observations, …) plus their relationships,
    leaving the `Patient` node intact so re-ingest doesn't have to recreate
    it. Used by the rebuild endpoint to clean up duplicate nodes accumulated
    from prior incremental writes — the Observation Cypher MERGE keys on
    `dateTime` which defaults to `datetime()` (NOW) when missing, so every
    ingest with un-timestamped readings creates fresh nodes.

    Returns the labels that were targeted so the caller can confirm the
    wipe ran. Counts aren't returned to keep the path simple (no RETURN
    queries to round-trip).
    """
    ensure_constraints()
    deleted_labels: list[str] = []
    with neo4j_session() as s:
        # Encounters + documents link from the Patient via HAS_ENCOUNTER /
        # HAS_DOCUMENT and don't carry a patientId property of their own.
        s.run(
            "MATCH (p:Patient {patientId: $pid})-[:HAS_ENCOUNTER|HAS_DOCUMENT*1..2]->(n) "
            "WHERE n:Encounter OR n:Document "
            "DETACH DELETE n",
            {"pid": patient_id},
        )
        deleted_labels.extend(["Encounter", "Document"])
        # Per-fact nodes carry patientId; one query per label keeps a single
        # failure from rolling back the whole wipe.
        for label in (
            "Condition", "Medication", "Observation", "Procedure",
            "Allergy", "Plan", "CodingCandidate",
        ):
            s.run(
                f"MATCH (n:{label} {{patientId: $pid}}) DETACH DELETE n",
                {"pid": patient_id},
            )
            deleted_labels.append(label)
    return {"labels": deleted_labels}


def backfill_graph_for_document(
    patient: dict[str, Any],
    encounter: dict[str, Any],
    document: dict[str, Any],
    facts: list[dict[str, Any]],
) -> dict[str, int]:
    """Push raw Postgres fact rows for one document into Neo4j.

    This is the recovery path for the silent-graph-upsert-failure mode: if
    Neo4j was unhealthy during ingest, the patient ends up with facts in
    Postgres but an empty graph. The original `update_graph_for_document`
    needs a fully-formed `ClinicalExtractionResult`, which we don't have
    after the AI call has been audited and discarded. This function operates
    on the same raw rows that `gather_patient_facts` returns and reuses the
    same Cypher constants, so the resulting graph is structurally identical
    to what a fresh ingest would have produced.

    `patient`, `encounter`, and `document` should be dicts with the same
    keys the original function expects (`patientId`, `encounterId`,
    `documentId`, …); the caller is responsible for mapping from Postgres
    snake_case row keys.
    """
    ensure_constraints()

    def _by(type_: str) -> list[dict[str, Any]]:
        return [f for f in facts if f.get("type") == type_]

    def _extra(f: dict[str, Any], k: str) -> Any:
        return (f.get("extra") or {}).get(k)

    conditions = [
        {
            "value": f["value"], "code": f.get("normalized_code"),
            "system": f.get("coding_system"),
            "reviewStatus": f.get("review_status") or "ai_suggested",
            "confidence": f.get("confidence"), "evidence": f.get("evidence_text"),
        }
        for f in _by("condition")
    ]
    medications = [
        {
            "name": f["value"], "rxNorm": _extra(f, "rxNorm"),
            "action": _extra(f, "action") or "continue",
            "dose": _extra(f, "dose"), "route": _extra(f, "route"),
            "frequency": _extra(f, "frequency"),
            "indication": _extra(f, "indication"),
            "evidence": f.get("evidence_text"),
        }
        for f in _by("medication")
    ]
    observations = [
        {
            "name": f["value"], "loinc": f.get("normalized_code"),
            "value": _extra(f, "value") or "",
            "unit": _extra(f, "unit"),
            "abnormalFlag": _extra(f, "abnormalFlag"),
            "dt": iso(f.get("date_time")),
        }
        for f in _by("observation")
    ]
    procedures = [
        {"value": f["value"], "code": f.get("normalized_code"), "system": f.get("coding_system")}
        for f in _by("procedure")
    ]
    allergies = [{"value": f["value"]} for f in _by("allergy")]
    plans = [
        {"description": f["value"], "category": _extra(f, "category") or "other",
         "addresses": _extra(f, "addressesCondition")}
        for f in _by("plan")
    ]
    coding_candidates = [
        {"system": f.get("coding_system") or _extra(f, "system"),
         "code": f.get("normalized_code") or _extra(f, "code"),
         "display": _extra(f, "display") or f["value"],
         "forCondition": _extra(f, "forCondition") or f["value"],
         "confidence": f.get("confidence")}
        for f in _by("coding_candidate")
        if f.get("value") and (f.get("normalized_code") or _extra(f, "code"))
    ]

    params = _root_params(patient, encounter, document)
    counts = {
        "conditions": len(conditions), "medications": len(medications),
        "observations": len(observations), "procedures": len(procedures),
        "allergies": len(allergies), "plans": len(plans),
        "codingCandidates": len(coding_candidates),
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
    """Legacy entry point — equivalent to fetch_graph(patient_id) with the
    default patient-level parameters. Kept so existing callers don't break."""
    return fetch_graph(patient_id)


def fetch_graph(
    patient_id: str,
    *,
    scope: str = "patient",
    encounter_ids: list[str] | None = None,
    dedupe: bool | None = None,
    include_encounters: bool | None = None,
    include_documents: bool = False,
    review_status: str = "hide_rejected",
) -> dict[str, Any]:
    """Build the subgraph for the requested scope.

    Defaults:
      scope="patient"        → dedupe=True, include_encounters=False
      scope in {"encounter", "encounters"} →
                             dedupe=True if len(encounter_ids)>1 else False,
                             include_encounters=True
    """
    if scope not in ("patient", "encounter", "encounters"):
        raise ValueError(f"unknown scope {scope!r}")
    encounter_ids = encounter_ids or []
    if scope in ("encounter", "encounters") and not encounter_ids:
        raise ValueError("encounter_ids required when scope is encounter or encounters")
    if include_encounters is None:
        include_encounters = scope != "patient"
    if dedupe is None:
        dedupe = scope == "patient" or len(encounter_ids) > 1

    cypher, params = _graph_cypher(
        patient_id, scope, encounter_ids,
        include_documents=include_documents, review_status=review_status,
    )
    rows = run_cypher(cypher, params)
    if not rows:
        return {"nodes": [], "edges": []}
    graph = _materialize_rows(rows[0], include_encounters=include_encounters,
                              include_documents=include_documents)
    if dedupe:
        graph = _dedupe_nodes_edges(graph)
    return graph


def _graph_cypher(
    patient_id: str, scope: str, encounter_ids: list[str],
    *, include_documents: bool, review_status: str,
) -> tuple[str, dict[str, Any]]:
    """Build a single Cypher query with parameters. Filters facts by
    review_status via a Cypher WHERE clause on each OPTIONAL MATCH."""
    fact_filter = ""
    if review_status == "hide_rejected":
        fact_filter = " WHERE coalesce(n.reviewStatus, 'ai_suggested') <> 'rejected'"
    elif review_status == "confirmed":
        fact_filter = " WHERE n.reviewStatus = 'human_confirmed'"
    # 'all' → no filter

    enc_match = "MATCH (p)-[:HAS_ENCOUNTER]->(e:Encounter)"
    if scope in ("encounter", "encounters"):
        enc_match += " WHERE e.encounterId IN $eids"

    parts = [
        "MATCH (p:Patient {patientId: $pid})",
        enc_match,
        "OPTIONAL MATCH (e)-[:MENTIONS]->(c:Condition)" + fact_filter.replace("n.", "c."),
        "OPTIONAL MATCH (e)-[:HAS_OBSERVATION]->(o:Observation)" + fact_filter.replace("n.", "o."),
        "OPTIONAL MATCH (e)-[:PRESCRIBED]->(m:Medication)" + fact_filter.replace("n.", "m."),
        "OPTIONAL MATCH (e)-[:HAS_PLAN]->(pl:Plan)" + fact_filter.replace("n.", "pl."),
        "OPTIONAL MATCH (p)-[:HAS_ALLERGY]->(a:Allergy)" + fact_filter.replace("n.", "a."),
    ]
    if include_documents:
        parts.append("OPTIONAL MATCH (e)-[:HAS_DOCUMENT]->(d:Document)")

    parts.append(
        "RETURN p, "
        "collect(DISTINCT e) AS encounters, "
        + ("collect(DISTINCT d) AS docs, " if include_documents else "")
        + "collect(DISTINCT c) AS conditions, "
        "collect(DISTINCT o) AS observations, "
        "collect(DISTINCT m) AS medications, "
        "collect(DISTINCT pl) AS plans, "
        "collect(DISTINCT a) AS allergies"
    )
    cypher = "\n        ".join(parts)
    return cypher, {"pid": patient_id, "eids": encounter_ids}


def _materialize_rows(row: dict[str, Any], *, include_encounters: bool,
                      include_documents: bool) -> dict[str, Any]:
    """Convert a single Cypher row into the {nodes, edges} response shape.

    Uses encounter-aware node ids (Condition:val:e<eid>) so the dedupe step
    can find duplicates that came from different encounters."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(label: str, item: dict[str, Any], natural_key: str, source_eid: str | None = None) -> str | None:
        if not item:
            return None
        clean = _jsonable(item)
        suffix = f":{source_eid}" if source_eid else ""
        nid = f"{label}:{clean.get(natural_key)}{suffix}"
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, "label": label, "data": clean})
        return nid

    pid = add("Patient", row["p"], "patientId")
    enc_ids: dict[str, str] = {}  # encounterId → node id
    for e in (row.get("encounters") or []):
        if not e:
            continue
        eid_str = (e.get("encounterId") or "")
        if include_encounters:
            enc_node_id = add("Encounter", e, "encounterId")
        else:
            # Build enc_ids for fact-attachment lookup without emitting the node.
            clean = _jsonable(e)
            enc_node_id = f"Encounter:{clean.get('encounterId')}"
        if enc_node_id is None:
            continue
        enc_ids[eid_str] = enc_node_id
        if include_encounters and pid:
            edges.append({"from": pid, "to": enc_node_id, "type": "HAS_ENCOUNTER"})

    # Facts. Each fact carries `encounterId` when known so its id is unique
    # per encounter; the dedupe pass collapses by clinical key afterwards.
    def fact_eid(f: dict[str, Any]) -> str | None:
        return f.get("encounterId") if f else None

    for c in (row.get("conditions") or []):
        cid = add("Condition", c, "value", source_eid=fact_eid(c))
        if cid:
            attach_from = enc_ids.get(fact_eid(c)) if include_encounters else pid
            if attach_from:
                edges.append({"from": attach_from, "to": cid, "type": "HAS_CONDITION"})
    for m in (row.get("medications") or []):
        mid = add("Medication", m, "name", source_eid=fact_eid(m))
        if mid:
            attach_from = enc_ids.get(fact_eid(m)) if include_encounters else pid
            if attach_from:
                edges.append({"from": attach_from, "to": mid, "type": "ON_MEDICATION"})
    for o in (row.get("observations") or []):
        oid = add("Observation", o, "name", source_eid=fact_eid(o))
        if oid:
            attach_from = enc_ids.get(fact_eid(o)) if include_encounters else pid
            if attach_from:
                edges.append({"from": attach_from, "to": oid, "type": "HAS_OBSERVATION"})
    for pl in (row.get("plans") or []):
        plid = add("Plan", pl, "description", source_eid=fact_eid(pl))
        if plid:
            attach_from = enc_ids.get(fact_eid(pl)) if include_encounters else pid
            if attach_from:
                edges.append({"from": attach_from, "to": plid, "type": "HAS_PLAN"})
    for a in (row.get("allergies") or []):
        aid = add("Allergy", a, "value")
        if aid and pid:
            edges.append({"from": pid, "to": aid, "type": "HAS_ALLERGY"})

    if include_documents:
        for d in (row.get("docs") or []):
            did = add("Document", d, "documentId")
            if did:
                eid_attach = enc_ids.get(d.get("encounterId")) if include_encounters else pid
                if eid_attach:
                    edges.append({"from": eid_attach, "to": did, "type": "HAS_DOCUMENT"})

    # Strip the encounter-suffix off node ids when we don't need it
    # (i.e., when encounters aren't included, facts come from patient directly).
    # The dedupe pass will collapse all same-key facts anyway, so suffix is
    # only meaningful pre-dedupe. Leave the suffix; dedupe handles it.
    return {"nodes": nodes, "edges": edges}


def _dedupe_key(node: dict[str, Any]) -> tuple | None:
    """Return a hashable dedupe key, or None for node labels we don't dedupe.

    Conditions: collapse by normalized_code; fall back to lowercased value.
    Medications: collapse by rxNorm; fall back to lowercased name. A different
        rxNorm for the same generic name stays separate (different formulations).
    Allergies: same rule as Conditions.
    Observations: NOT deduped — same name at different times is informative.
    Documents / Plans / Procedures / Patient / Encounter: pass through.
    """
    label = node.get("label")
    data = node.get("data") or {}
    if label == "Condition":
        return ("Condition", data.get("normalized_code") or str(data.get("value", "")).casefold())
    if label == "Medication":
        return ("Medication", data.get("rxNorm") or str(data.get("name", "")).casefold())
    if label == "Allergy":
        return ("Allergy", data.get("normalized_code") or str(data.get("value", "")).casefold())
    return None


def _dedupe_nodes_edges(graph: dict[str, Any]) -> dict[str, Any]:
    """Collapse duplicate fact nodes across encounters; rewrite edges to the
    surviving node id. Pure function — does NOT touch the network/Neo4j."""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    canonical_id_by_key: dict[tuple, str] = {}
    id_remap: dict[str, str] = {}
    kept_nodes: list[dict[str, Any]] = []

    for node in nodes:
        key = _dedupe_key(node)
        if key is None:
            kept_nodes.append(node)
            continue
        canonical = canonical_id_by_key.get(key)
        if canonical is None:
            canonical_id_by_key[key] = node["id"]
            kept_nodes.append(node)
        else:
            id_remap[node["id"]] = canonical  # drop this node; rewrite its inbound edges

    rewritten_edges: list[dict[str, Any]] = []
    for edge in edges:
        rewritten_edges.append({
            **edge,
            "from": id_remap.get(edge["from"], edge["from"]),
            "to": id_remap.get(edge["to"], edge["to"]),
        })

    return {"nodes": kept_nodes, "edges": rewritten_edges}
