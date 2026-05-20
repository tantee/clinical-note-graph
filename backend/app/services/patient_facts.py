from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.db.postgres import db_session


# Fact types that should be deduplicated when aggregating at the patient level.
# Each row collapses to one entry per (type, normalized_code or lower(value));
# the highest-confidence mention wins, with the latest date_time / created_at
# as the tie-breaker. Observations and plans deliberately stay un-deduped —
# the same lab repeated across visits is a longitudinal trend, not noise.
_PATIENT_DEDUPE_TYPES: frozenset[str] = frozenset({
    "condition", "medication", "allergy", "diagnosis_candidate", "coding_candidate",
})


def _dedupe_key(fact: dict[str, Any]) -> str:
    """Stable de-dupe key: prefer normalized_code, fall back to lower-cased value
    so 'tamoxifen' and 'Tamoxifen' collapse into one row."""
    code = (fact.get("normalized_code") or "").strip()
    if code:
        return f"code:{code.lower()}"
    return f"val:{(fact.get('value') or '').strip().lower()}"


def _dedupe_patient_facts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse same-condition / same-med rows that came from multiple mentions
    in the same document or across encounters. Keeps the highest-confidence
    representative and accumulates evidence text from the duplicates so the
    reviewer can still see every mention."""
    out: dict[str, dict[str, Any]] = {}
    for f in rows:
        if not f.get("value"):
            continue  # don't try to dedupe rows without a name to key on
        key = _dedupe_key(f)
        existing = out.get(key)
        if existing is None:
            out[key] = dict(f)
            continue
        # Pick the higher-confidence row as the representative; ties break on
        # created_at (later wins — reflects "latest review").
        f_conf = f.get("confidence") or 0.0
        e_conf = existing.get("confidence") or 0.0
        rep = f if (f_conf, str(f.get("created_at") or "")) > (e_conf, str(existing.get("created_at") or "")) else existing
        loser = existing if rep is f else f
        merged = dict(rep)
        # Roll up evidence text uniquely (preserve order, drop blanks).
        seen: set[str] = set()
        evid: list[str] = []
        for src in (rep.get("evidence_text"), loser.get("evidence_text")):
            if src and src not in seen:
                evid.append(src)
                seen.add(src)
        if evid:
            merged["evidence_text"] = " · ".join(evid)
        out[key] = merged
    return list(out.values())


def gather_patient_facts(patient_id: str, *, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Aggregate everything we know about a patient from Postgres in one round-trip per table."""
    where_clauses = ["patient_id = :pid"]
    params: dict[str, Any] = {"pid": patient_id}
    if start:
        where_clauses.append("(date_time IS NULL OR date_time >= :start)")
        params["start"] = start
    if end:
        where_clauses.append("(date_time IS NULL OR date_time <= :end)")
        params["end"] = end
    where = " AND ".join(where_clauses)

    with db_session() as s:
        patient = s.execute(text("SELECT * FROM patients WHERE patient_id = :pid"), {"pid": patient_id}).mappings().first()
        encounters = s.execute(
            text("SELECT * FROM encounters WHERE patient_id = :pid ORDER BY date_time ASC"),
            {"pid": patient_id},
        ).mappings().all()
        facts = s.execute(text(f"SELECT * FROM facts WHERE {where} ORDER BY created_at ASC"), params).mappings().all()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for f in facts:
        d = dict(f)
        d["id"] = str(d["id"])
        d["confidence"] = float(d["confidence"]) if d["confidence"] is not None else None
        grouped.setdefault(d["type"], []).append(d)

    for fact_type in list(grouped):
        if fact_type in _PATIENT_DEDUPE_TYPES:
            grouped[fact_type] = _dedupe_patient_facts(grouped[fact_type])

    return {
        "patient": dict(patient) if patient else None,
        "encounters": [dict(e) for e in encounters],
        "problems": grouped.get("condition", []),
        "medications": grouped.get("medication", []),
        "observations": grouped.get("observation", []),
        "procedures": grouped.get("procedure", []),
        "allergies": grouped.get("allergy", []),
        "plans": grouped.get("plan", []),
        "diagnoses": grouped.get("diagnosis_candidate", []),
        "codingCandidates": grouped.get("coding_candidate", []),
    }


def gather_encounter_facts(encounter_id: str) -> dict[str, Any]:
    """Aggregate facts for a single encounter plus background patient context.

    Returns:
      encounter:     {encounterId, type, dateTime, department, provider}
      thisEncounter: {problems, medications, observations, procedures,
                      plans, allergies, diagnoses, codingCandidates}
                     <- facts WHERE encounter_id = :eid AND review_status <> 'rejected'
      background:    {chronicProblems, homeMedications, knownAllergies}
                     <- latest fact per normalized_code (or value if no code)
                       across all OTHER encounters; rejected facts excluded;
                       limited to types in (condition, medication, allergy).
      documents:     [{documentId, format, version, ...}]

    Raises LookupError if the encounter does not exist.
    """
    with db_session() as s:
        enc = s.execute(
            text("SELECT * FROM encounters WHERE encounter_id = :eid"),
            {"eid": encounter_id},
        ).mappings().first()
        if not enc:
            raise LookupError(f"encounter {encounter_id!r} not found")

        patient_id = enc["patient_id"]
        this_rows = s.execute(
            text(
                "SELECT * FROM facts "
                "WHERE encounter_id = :eid AND review_status <> 'rejected' "
                "ORDER BY date_time NULLS LAST, created_at ASC"
            ),
            {"eid": encounter_id},
        ).mappings().all()
        bg_rows = s.execute(
            text(
                "SELECT * FROM facts "
                "WHERE patient_id = :pid AND (encounter_id IS NULL OR encounter_id <> :eid) "
                "AND review_status <> 'rejected' "
                "AND type IN ('condition', 'medication', 'allergy') "
                "ORDER BY date_time NULLS LAST, created_at ASC"
            ),
            {"pid": patient_id, "eid": encounter_id},
        ).mappings().all()
        docs = s.execute(
            text("SELECT * FROM documents WHERE encounter_id = :eid"),
            {"eid": encounter_id},
        ).mappings().all()

    def _norm(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            d = dict(r)
            if d.get("id") is not None:
                d["id"] = str(d["id"])
            if d.get("confidence") is not None:
                d["confidence"] = float(d["confidence"])
            out.append(d)
        return out

    this_grouped: dict[str, list[dict[str, Any]]] = {}
    for f in _norm(this_rows):
        this_grouped.setdefault(f["type"], []).append(f)

    # Background: dedupe — keep one row per normalized_code (or value if no code).
    # Rows come in ASC order, so the last write wins for "latest mention".
    bg_by_key: dict[str, dict[str, Any]] = {}
    bg_by_type: dict[str, list[str]] = {"condition": [], "medication": [], "allergy": []}
    for f in _norm(bg_rows):
        key = f"{f['type']}|{f.get('normalized_code') or f['value']}"
        if key not in bg_by_key:
            bg_by_type[f["type"]].append(key)
        bg_by_key[key] = f

    return {
        "encounter": {
            "encounterId": enc["encounter_id"],
            "patientId": enc["patient_id"],
            "type": enc["type"],
            "dateTime": str(enc["date_time"]) if enc.get("date_time") else None,
            "department": enc.get("department"),
            "provider": enc.get("provider"),
        },
        "thisEncounter": {
            "problems": this_grouped.get("condition", []),
            "medications": this_grouped.get("medication", []),
            "observations": this_grouped.get("observation", []),
            "procedures": this_grouped.get("procedure", []),
            "plans": this_grouped.get("plan", []),
            "allergies": this_grouped.get("allergy", []),
            "diagnoses": this_grouped.get("diagnosis_candidate", []),
            "codingCandidates": this_grouped.get("coding_candidate", []),
        },
        "background": {
            "chronicProblems": [bg_by_key[k] for k in bg_by_type["condition"]],
            "homeMedications": [bg_by_key[k] for k in bg_by_type["medication"]],
            "knownAllergies": [bg_by_key[k] for k in bg_by_type["allergy"]],
        },
        "documents": [
            {
                "documentId": d["document_id"],
                "encounterId": d.get("encounter_id"),
                "format": d.get("format"),
                "version": d.get("version"),
            }
            for d in docs
        ],
    }
