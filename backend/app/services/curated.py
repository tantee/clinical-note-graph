"""Curated longitudinal layer for temporal problems & medications.

Two halves:
  * Pure merge logic (this task) — identity keys, bound normalization, mapping AI
    extraction objects to curated-row dicts, and the merge rule that never clobbers
    human-edited fields. No I/O, exhaustively unit-tested.
  * DB + graph layer (Task 6) — reconcile_curated, CRUD, propagate_curated_to_graph.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.schemas.extraction import MedicationChange, PatientFact

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
