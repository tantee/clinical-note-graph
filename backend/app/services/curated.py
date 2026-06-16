"""Curated longitudinal layer for temporal problems & medications.

Two halves:
  * Pure merge logic (this task) — identity keys, bound normalization, mapping AI
    extraction objects to curated-row dicts, and the merge rule that never clobbers
    human-edited fields. No I/O, exhaustively unit-tested.
  * DB + graph layer (Task 6) — reconcile_curated, CRUD, propagate_curated_to_graph.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from app.db import neo4j_client
from app.db.helpers import audit
from app.db.postgres import db_session
from app.schemas.extraction import ClinicalExtractionResult, MedicationChange, PatientFact

logger = logging.getLogger(__name__)


class CuratedIdentityConflict(Exception):
    """Raised when a rename would collide with another curated row's identity."""

# Curated columns the AI is allowed to populate/refresh. Order is irrelevant.
AI_FILLABLE_FIELDS: tuple[str, ...] = (
    "display_value", "normalized_code", "coding_system",
    "start_date", "start_qualifier", "stop_date", "stop_qualifier",
    "start_text", "stop_text", "schedule_text", "status",
)


def normalized_key(code: str | None, value: str | None) -> str:
    """Identity key: lower(code) when a code is present, else lower(value)."""
    code = (code or "").strip()
    if code:
        return code.lower()
    return (value or "").strip().lower()


def _iso_date(value: Any) -> str | None:
    """Coerce a datetime/date/str to an ISO date string (YYYY-MM-DD) or None.

    datetime is truncated to its date component (time is intentionally dropped)."""
    if value is None:
        return None
    if isinstance(value, datetime):   # must precede date — datetime IS-A date
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    return s[:10] if s else None      # tolerate "2026-01-01T..." strings


def normalize_bounds(
    start_date: Any, start_qualifier: str | None,
    stop_date: Any, stop_qualifier: str | None,
) -> tuple[str | None, str, str | None, str]:
    """Apply default qualifiers and resolve impossible combinations.

    - Missing qualifier -> 'exact' if a date is present, else 'unknown'.
    - stop_qualifier 'ongoing' clears any stop_date (an ongoing item has no end).
    """
    start_date = _iso_date(start_date)
    stop_date = _iso_date(stop_date)
    start_q = start_qualifier or ("exact" if start_date else "unknown")
    stop_q = stop_qualifier or ("exact" if stop_date else "unknown")
    if stop_q == "ongoing":
        stop_date = None
    return start_date, start_q, stop_date, stop_q


def ai_item_from_condition(p: PatientFact) -> dict[str, Any]:
    s_date, s_q, e_date, e_q = normalize_bounds(
        p.onsetDate, p.onsetQualifier, p.resolvedDate, p.resolvedQualifier
    )
    return {
        "type": "condition",
        "normalized_key": normalized_key(p.normalizedCode, p.value),
        "display_value": p.value,
        "normalized_code": p.normalizedCode,
        "coding_system": p.codingSystem,
        "start_date": s_date, "start_qualifier": s_q,
        "stop_date": e_date, "stop_qualifier": e_q,
        "start_text": p.onsetText, "stop_text": p.resolvedText,
        "schedule_text": None,
        "status": p.status,
    }


def ai_item_from_medication(m: MedicationChange) -> dict[str, Any]:
    s_date, s_q, e_date, e_q = normalize_bounds(
        m.startDate, m.startQualifier, m.stopDate, m.stopQualifier
    )
    coding_system = "RxNorm" if m.rxNorm else None
    return {
        "type": "medication",
        "normalized_key": normalized_key(m.rxNorm, m.name),
        "display_value": m.name,
        "normalized_code": m.rxNorm,
        "coding_system": coding_system,
        "start_date": s_date, "start_qualifier": s_q,
        "stop_date": e_date, "stop_qualifier": e_q,
        "start_text": m.startText, "stop_text": m.stopText,
        "schedule_text": m.schedule,
        "status": m.action,
    }


def merge_curated(
    existing: dict[str, Any] | None, ai: dict[str, Any], *, resurface: bool
) -> tuple[dict[str, Any], bool]:
    """Compute the curated row to persist.

    Returns (row, is_new). When existing is None -> fresh ai_suggested row.
    Otherwise merge field-by-field: human-edited fields are preserved verbatim;
    every other AI-fillable field takes the AI value when AI supplies one
    (non-None), else keeps the existing value. Resurface flips a dismissed row
    back to active/ai_suggested while keeping the merged (human-preserving) values.
    """
    if existing is None:
        row = {
            "type": ai["type"],
            "normalized_key": ai["normalized_key"],
            "record_state": "active",
            "review_status": "ai_suggested",
            "origin": "ai",
            "human_edited_fields": [],
            "aliases": [],
        }
        for f in AI_FILLABLE_FIELDS:
            row[f] = ai.get(f)
        return row, True   # is_new=True for a brand-new identity

    edited = set(existing.get("human_edited_fields") or [])
    row = {**existing, "human_edited_fields": list(existing.get("human_edited_fields") or [])}
    for f in AI_FILLABLE_FIELDS:
        if f in edited:
            continue                       # human wins — never overwrite
        ai_val = ai.get(f)
        if ai_val is not None:
            row[f] = ai_val                # fill-empty + refresh in one rule
    if resurface:
        row["record_state"] = "active"
        row["review_status"] = "ai_suggested"
    return row, False


# --- SQL (named constants; the test FakeStore matches them by substring) ------

_SELECT_BY_IDENTITY = text("""
SELECT * FROM curated_facts
WHERE patient_id = :pid AND type = :type AND normalized_key = :nk
""")

_SELECT_ACTIVE_BY_TYPE = text("""
SELECT * FROM curated_facts
WHERE patient_id = :pid AND type = :type AND record_state = 'active'
ORDER BY display_value ASC
""")

_SELECT_DISMISSED_BY_TYPE = text("""
SELECT * FROM curated_facts
WHERE patient_id = :pid AND type = :type AND record_state = 'dismissed'
ORDER BY display_value ASC
""")

_SELECT_BY_ID = text("SELECT * FROM curated_facts WHERE id = CAST(:cid AS uuid)")

# Identity lookup that also matches a row whose `aliases` carry the key (a prior
# name). Exact normalized_key matches rank first; alias matches re-link a
# re-mention of an old name to the row it was renamed into. Compared
# case-insensitively because aliases store original-case display values while
# the incoming key is already lower-cased.
_SELECT_BY_IDENTITY_OR_ALIAS = text("""
SELECT * FROM curated_facts
WHERE patient_id = :pid AND type = :type
  AND (normalized_key = :nk
       OR EXISTS (SELECT a FROM jsonb_array_elements_text(aliases) AS a WHERE lower(a) = :nk))
ORDER BY (normalized_key = :nk) DESC, created_at ASC
LIMIT 1
""")

_SELECT_ALL_BY_PATIENT = text("""
SELECT * FROM curated_facts WHERE patient_id = :pid
""")

# Recovery: the AI extractions that were persisted at ingest time, oldest first.
# raw_output validates back into a ClinicalExtractionResult, so reconcile can be
# replayed without re-calling the model.
_SELECT_VALID_EXTRACTIONS = text("""
SELECT document_id, raw_output, created_at FROM ai_outputs
WHERE patient_id = :pid AND call_type = 'extract' AND valid = true
ORDER BY created_at ASC
""")

_INSERT = text("""
INSERT INTO curated_facts (
    patient_id, type, normalized_key, display_value, normalized_code, coding_system,
    start_date, start_qualifier, stop_date, stop_qualifier, start_text, stop_text,
    schedule_text, status, record_state, review_status, origin,
    human_edited_fields, aliases, last_evidence_fact_id, updated_by
) VALUES (
    :patient_id, :type, :normalized_key, :display_value, :normalized_code, :coding_system,
    :start_date, :start_qualifier, :stop_date, :stop_qualifier, :start_text, :stop_text,
    :schedule_text, :status, :record_state, :review_status, :origin,
    CAST(:human_edited_fields AS jsonb), CAST(:aliases AS jsonb), :last_evidence_fact_id, :updated_by
)
RETURNING id
""")

_UPDATE = text("""
UPDATE curated_facts SET
    normalized_key = :normalized_key,
    display_value = :display_value, normalized_code = :normalized_code,
    coding_system = :coding_system, start_date = :start_date,
    start_qualifier = :start_qualifier, stop_date = :stop_date,
    stop_qualifier = :stop_qualifier, start_text = :start_text, stop_text = :stop_text,
    schedule_text = :schedule_text, status = :status, record_state = :record_state,
    review_status = :review_status, human_edited_fields = CAST(:human_edited_fields AS jsonb),
    aliases = CAST(:aliases AS jsonb),
    last_evidence_fact_id = :last_evidence_fact_id, updated_by = :updated_by,
    updated_at = now()
WHERE id = CAST(:cid AS uuid)
""")


def _to_dict(row) -> dict[str, Any]:
    d = dict(row)
    if "id" in d and d["id"] is not None:
        d["id"] = str(d["id"])
    hef = d.get("human_edited_fields")
    if isinstance(hef, str):
        d["human_edited_fields"] = json.loads(hef)
    elif hef is None:
        d["human_edited_fields"] = []
    al = d.get("aliases")
    if isinstance(al, str):
        d["aliases"] = json.loads(al)
    elif al is None:
        d["aliases"] = []
    # Real Postgres returns date/datetime objects for date columns; coerce to
    # ISO strings so the whole service layer is uniformly string-based (keeps
    # FIX 1 change-detection correct and CuratedItem.startDate: str | None happy).
    for k in ("start_date", "stop_date"):
        if d.get(k) is not None:
            d[k] = _iso_date(d[k])
    return d


def list_curated(patient_id: str, type_: str, state: str = "active") -> list[dict[str, Any]]:
    """List curated rows for a patient by type. state='dismissed' returns the
    dismissed rows (drives the Restore UI); anything else returns active rows."""
    sql = _SELECT_DISMISSED_BY_TYPE if state == "dismissed" else _SELECT_ACTIVE_BY_TYPE
    with db_session() as s:
        rows = s.execute(sql, {"pid": patient_id, "type": type_}).mappings().all()
    return [_to_dict(r) for r in rows]


def get_curated(cid: str) -> dict[str, Any] | None:
    with db_session() as s:
        row = s.execute(_SELECT_BY_ID, {"cid": cid}).mappings().first()
    return _to_dict(row) if row else None


def _persist_merged(s, *, patient_id: str, existing: dict | None, row: dict,
                    is_new: bool, evidence_fact_id: str | None, updated_by: str | None) -> str:
    # FIX 3: always normalize bounds before persisting so that an AI re-mention
    # with stopQualifier='ongoing' clears a previously-set stop_date, regardless
    # of which call path (insert, update, reconcile) leads here.
    s_date, s_q, e_date, e_q = normalize_bounds(
        row.get("start_date"), row.get("start_qualifier"),
        row.get("stop_date"), row.get("stop_qualifier"),
    )
    payload = {
        "patient_id": patient_id,
        "type": row["type"],
        "normalized_key": row["normalized_key"],
        "display_value": row["display_value"],
        "normalized_code": row.get("normalized_code"),
        "coding_system": row.get("coding_system"),
        "start_date": s_date,
        "start_qualifier": s_q,
        "stop_date": e_date,
        "stop_qualifier": e_q,
        "start_text": row.get("start_text"),
        "stop_text": row.get("stop_text"),
        "schedule_text": row.get("schedule_text"),
        "status": row.get("status"),
        "record_state": row.get("record_state", "active"),
        "review_status": row.get("review_status", "ai_suggested"),
        "origin": row.get("origin", "ai"),
        "human_edited_fields": json.dumps(row.get("human_edited_fields") or []),
        "aliases": json.dumps(row.get("aliases") or []),
        "last_evidence_fact_id": evidence_fact_id,
        "updated_by": updated_by,
    }
    if is_new:
        res = s.execute(_INSERT, payload).mappings().first()
        if not res:
            raise RuntimeError("curated_facts INSERT returned no id")
        return str(res["id"])
    payload["cid"] = existing["id"]
    s.execute(_UPDATE, payload)
    return str(existing["id"])


def reconcile_curated(patient_id: str, extraction: ClinicalExtractionResult) -> None:
    """Upsert each AI problem/medication into curated_facts by identity, then push
    the resulting values into Neo4j. Best-effort and isolated per item — one failure
    is logged and never aborts the others or the ingest."""
    ai_items: list[dict[str, Any]] = []
    for p in getattr(extraction, "problems", []) or []:
        ai_items.append(ai_item_from_condition(p))
    for m in getattr(extraction, "medications", []) or []:
        ai_items.append(ai_item_from_medication(m))

    for ai in ai_items:
        try:
            ai_value = ai.get("display_value")
            with db_session() as s:
                # Match by identity OR alias: a re-mention of a name the row was
                # renamed away from re-links here instead of minting a duplicate.
                existing_row = s.execute(
                    _SELECT_BY_IDENTITY_OR_ALIAS,
                    {"pid": patient_id, "type": ai["type"], "nk": ai["normalized_key"]},
                ).mappings().first()
                existing = _to_dict(existing_row) if existing_row else None
                resurface = bool(existing) and existing.get("record_state") == "dismissed"
                merged, is_new = merge_curated(existing, ai, resurface=resurface)
                # When the human-locked display differs from the AI's value, the
                # AI value is a prior/alternate name for this row: record it so a
                # future re-mention re-links, and collapse its stale graph node.
                old_value = ai_value if ai_value and ai_value != merged["display_value"] else None
                if old_value:
                    aliases = list(merged.get("aliases") or [])
                    if old_value not in aliases:
                        aliases.append(old_value)
                    merged["aliases"] = aliases
                cid = _persist_merged(
                    s, patient_id=patient_id, existing=existing, row=merged,
                    is_new=is_new, evidence_fact_id=None, updated_by="ai",
                )
            merged["id"] = cid
            if old_value:
                relabel_curated_node(patient_id, ai["type"], old_value, merged["display_value"])
            propagate_curated_to_graph(patient_id, merged)
        except Exception:  # noqa: BLE001 — resilience matches graph-write behavior
            logger.exception("curated reconcile failed for %s", ai.get("normalized_key"))


def reconcile_curated_from_history(patient_id: str) -> dict[str, int]:
    """Rebuild the curated layer from stored AI extractions, no AI call.

    Recovery for patients ingested before `curated_facts` existed (reconcile
    failed silently then). Replays the most recent valid extraction per document
    through `reconcile_curated`, oldest document first — mirroring the
    `/graph/rebuild` recovery path. Returns counts of documents replayed and
    extractions skipped as unparseable."""
    with db_session() as s:
        rows = s.execute(_SELECT_VALID_EXTRACTIONS, {"pid": patient_id}).mappings().all()
    # Most recent extraction per document (ASC order -> last write wins).
    latest_by_doc: dict[str, Any] = {}
    order: list[str] = []
    for r in rows:
        doc = r.get("document_id") or ""
        if doc not in latest_by_doc:
            order.append(doc)
        latest_by_doc[doc] = r
    documents = 0
    skipped = 0
    for doc in order:
        raw = latest_by_doc[doc]["raw_output"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        try:
            extraction = ClinicalExtractionResult.model_validate(raw)
        except Exception:  # noqa: BLE001 — a non-extraction or stale shape is skipped
            skipped += 1
            continue
        reconcile_curated(patient_id, extraction)
        documents += 1
    return {"documents": documents, "skipped": skipped}


# --- Neo4j propagation -------------------------------------------------------

# Curated values OVERRIDE the AI-set node properties (spec: the graph render shows
# the curated row when one exists). We intentionally SET unconditionally rather than
# coalesce: a human who clears an onset/start date must be able to blank it in the
# graph too. reconcile mirrors every AI mention, so this does not lose AI dates.
_CYPHER_CONDITION = """
MERGE (c:Condition {patientId: $patientId, value: $value})
  ON CREATE SET c.firstSeen = datetime()
  SET c.onsetDate = $startDate, c.resolvedDate = $stopDate,
      c.startQualifier = $startQualifier, c.stopQualifier = $stopQualifier,
      c.status = coalesce($status, c.status),
      c.curatedReviewStatus = $reviewStatus,
      c.curatedRecordState = $recordState, c.lastSeen = datetime()
"""

_CYPHER_MEDICATION = """
MERGE (m:Medication {patientId: $patientId, name: $value})
  ON CREATE SET m.firstSeen = datetime()
  SET m.startDate = $startDate, m.stopDate = $stopDate,
      m.startQualifier = $startQualifier, m.stopQualifier = $stopQualifier,
      m.scheduleText = $scheduleText, m.lastAction = coalesce($status, m.lastAction),
      m.curatedReviewStatus = $reviewStatus,
      m.curatedRecordState = $recordState, m.lastSeen = datetime()
"""


# Node key property per type — Condition is keyed on `value`, Medication on `name`.
_NODE_KEY: dict[str, tuple[str, str]] = {
    "condition": ("Condition", "value"),
    "medication": ("Medication", "name"),
}


def _relabel_cypher(label: str, prop: str) -> str:
    # Rename the curated node in place when no node already holds the new value
    # (edges follow the node, so the evidence trail is preserved). If a node at
    # the new value already exists, that one is canonical and the stale old node
    # is removed — either way the graph never keeps an orphan after a rename.
    return f"""
MATCH (old:{label} {{patientId: $patientId, {prop}: $oldValue}})
OPTIONAL MATCH (dup:{label} {{patientId: $patientId, {prop}: $newValue}})
FOREACH (_ IN CASE WHEN dup IS NULL THEN [1] ELSE [] END | SET old.{prop} = $newValue)
WITH old, dup
WHERE dup IS NOT NULL
DETACH DELETE old
"""


def relabel_curated_node(patient_id: str, type_: str, old_value: str, new_value: str) -> None:
    """Move/cleanup the Neo4j node when a curated item is renamed so the old
    display value doesn't linger as an orphan. Best-effort."""
    if not old_value or old_value == new_value or type_ not in _NODE_KEY:
        return
    label, prop = _NODE_KEY[type_]
    params = {"patientId": patient_id, "oldValue": old_value, "newValue": new_value}
    try:
        neo4j_client.run_cypher(_relabel_cypher(label, prop), params)
    except Exception:  # noqa: BLE001
        logger.exception("curated graph relabel failed for %s -> %s", old_value, new_value)


def propagate_curated_to_graph(patient_id: str, row: dict[str, Any]) -> None:
    """Push curated values into the matching Neo4j node. Best-effort."""
    params = {
        "patientId": patient_id,
        "value": row["display_value"],
        "startDate": row.get("start_date"),
        "stopDate": row.get("stop_date"),
        "startQualifier": row.get("start_qualifier"),
        "stopQualifier": row.get("stop_qualifier"),
        "scheduleText": row.get("schedule_text"),
        "status": row.get("status"),
        "reviewStatus": row.get("review_status"),
        "recordState": row.get("record_state"),
    }
    cypher = _CYPHER_CONDITION if row["type"] == "condition" else _CYPHER_MEDICATION
    try:
        neo4j_client.run_cypher(cypher, params)
    except Exception:  # noqa: BLE001
        logger.exception("curated graph propagation failed for %s", row.get("display_value"))


def apply_curated_to_graph(patient_id: str) -> None:
    """Replay the whole curated layer onto the patient's graph.

    Used as a post-pass after `rebuild_graph` repopulates nodes from the raw
    `facts` rows (which still carry the AI's original text). For every curated
    row we relabel any node carrying a prior name (alias) onto the curated
    display value, then propagate the curated values — so the rebuilt graph
    reflects human curation (renames, dismissals) instead of diverging back to
    what the AI first extracted. Best-effort and isolated per row."""
    with db_session() as s:
        rows = s.execute(_SELECT_ALL_BY_PATIENT, {"pid": patient_id}).mappings().all()
    for r in (_to_dict(r) for r in rows):
        try:
            for alias in r.get("aliases") or []:
                relabel_curated_node(patient_id, r["type"], alias, r["display_value"])
            propagate_curated_to_graph(patient_id, r)
        except Exception:  # noqa: BLE001
            logger.exception("curated graph replay failed for %s", r.get("display_value"))


# --- CRUD helpers (Task 7) ---------------------------------------------------

# camelCase patch field -> curated column. Drives which columns a PATCH touches.
PATCH_FIELD_TO_COLUMN: dict[str, str] = {
    "displayValue": "display_value",
    "startDate": "start_date",
    "startQualifier": "start_qualifier",
    "stopDate": "stop_date",
    "stopQualifier": "stop_qualifier",
    "startText": "start_text",
    "stopText": "stop_text",
    "scheduleText": "schedule_text",
    "status": "status",
}

# Superset of PATCH_FIELD_TO_COLUMN including code fields available at create time.
CREATE_FIELD_TO_COLUMN: dict[str, str] = {
    **PATCH_FIELD_TO_COLUMN,
    "normalizedCode": "normalized_code",
    "codingSystem": "coding_system",
}

_UPDATE_STATE = text("""
UPDATE curated_facts SET record_state = :state, updated_at = now()
WHERE id = CAST(:cid AS uuid)
""")


def insert_curated(patient_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Manual human insert: origin=human, review_status=human_confirmed."""
    code = payload.get("normalizedCode")
    value = payload["displayValue"]
    s_date, s_q, e_date, e_q = normalize_bounds(
        payload.get("startDate"), payload.get("startQualifier"),
        payload.get("stopDate"), payload.get("stopQualifier"),
    )
    row = {
        "type": payload["type"],
        "normalized_key": normalized_key(code, value),
        "display_value": value,
        "normalized_code": code,
        "coding_system": payload.get("codingSystem"),
        "start_date": s_date, "start_qualifier": s_q,
        "stop_date": e_date, "stop_qualifier": e_q,
        "start_text": payload.get("startText"), "stop_text": payload.get("stopText"),
        "schedule_text": payload.get("scheduleText"), "status": payload.get("status"),
        "record_state": "active", "review_status": "human_confirmed", "origin": "human",
        "human_edited_fields": sorted(
            col for field, col in CREATE_FIELD_TO_COLUMN.items()
            if payload.get(field) is not None
        ),
    }
    with db_session() as s:
        cid = _persist_merged(
            s, patient_id=patient_id, existing=None, row=row, is_new=True,
            evidence_fact_id=None, updated_by="human",
        )
        audit(s, actor="human", action="CURATED_CREATE", target_type="curated_fact",
              target_id=cid, payload={"patientId": patient_id, "type": row["type"],
                                      "displayValue": value})
    persisted = get_curated(cid)
    if persisted:
        propagate_curated_to_graph(patient_id, persisted)
    return persisted


def update_curated(cid: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    """Apply a partial edit. Touched columns join human_edited_fields; review flips
    to human_confirmed. stop_qualifier=ongoing normalizes the stop date away."""
    existing = get_curated(cid)
    if existing is None:
        return None
    edited = set(existing.get("human_edited_fields") or [])
    changed: list[str] = []
    merged = dict(existing)
    # FIX 1: allow null clears (drop `is not None`); only mark ACTUALLY-changed fields.
    for field, col in PATCH_FIELD_TO_COLUMN.items():
        if field in patch and patch[field] != merged.get(col):
            merged[col] = patch[field]   # may be None -> clears the field
            edited.add(col)
            changed.append(col)
    s_date, s_q, e_date, e_q = normalize_bounds(
        merged.get("start_date"), merged.get("start_qualifier"),
        merged.get("stop_date"), merged.get("stop_qualifier"),
    )
    merged.update(start_date=s_date, start_qualifier=s_q, stop_date=e_date, stop_qualifier=e_q)
    merged["normalized_key"] = normalized_key(merged.get("normalized_code"), merged["display_value"])
    # FIX 5: pre-check for rename collision to avoid unique-index 500.
    if merged["normalized_key"] != existing.get("normalized_key"):
        with db_session() as s:
            clash = s.execute(_SELECT_BY_IDENTITY, {
                "pid": existing.get("patient_id"), "type": existing["type"],
                "nk": merged["normalized_key"],
            }).mappings().first()
        if clash and str(clash["id"]) != str(cid):
            raise CuratedIdentityConflict(merged["normalized_key"])
    merged["review_status"] = "human_confirmed"
    merged["human_edited_fields"] = sorted(edited)
    # A rename records the prior display value as an alias — the bridge that lets
    # reconcile re-link a future re-mention of the old name and lets the graph
    # post-pass collapse stale old-value nodes.
    old_value = existing.get("display_value")
    new_value = merged["display_value"]
    if old_value and old_value != new_value:
        aliases = list(merged.get("aliases") or [])
        if old_value not in aliases:
            aliases.append(old_value)
        merged["aliases"] = aliases
    with db_session() as s:
        _persist_merged(
            s, patient_id=existing.get("patient_id") or "", existing=existing,
            row=merged, is_new=False, evidence_fact_id=existing.get("last_evidence_fact_id"),
            updated_by="human",
        )
        audit(s, actor="human", action="CURATED_UPDATE", target_type="curated_fact",
              target_id=str(cid), payload={"patientId": existing.get("patient_id"),
                                           "changed": sorted(changed)})
    # graph-orphan: a rename leaves the old display-value node behind unless we
    # move/cleanup it before re-propagating the curated values to the new node.
    if old_value != new_value:
        relabel_curated_node(existing.get("patient_id") or "", existing["type"], old_value, new_value)
    persisted = get_curated(cid)
    if persisted:
        propagate_curated_to_graph(persisted.get("patient_id") or "", persisted)
    return persisted


def set_record_state(cid: str, state: str) -> dict[str, Any] | None:
    existing = get_curated(cid)
    if existing is None:
        return None
    with db_session() as s:
        s.execute(_UPDATE_STATE, {"cid": cid, "state": state})
        audit(s, actor="human",
              action="CURATED_DISMISS" if state == "dismissed" else "CURATED_RESTORE",
              target_type="curated_fact", target_id=str(cid),
              payload={"patientId": existing.get("patient_id"), "recordState": state})
    # FIX 4c: propagate the new record_state to the graph so dismissed nodes
    # are correctly reflected in Neo4j.
    persisted = get_curated(cid)
    if persisted:
        propagate_curated_to_graph(persisted.get("patient_id") or "", persisted)
    return persisted
