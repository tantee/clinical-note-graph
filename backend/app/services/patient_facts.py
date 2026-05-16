from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.db.postgres import db_session


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
