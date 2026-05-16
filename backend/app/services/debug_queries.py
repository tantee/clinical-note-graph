from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.db.postgres import db_session


def _range_clauses(start: str | None, end: str | None) -> tuple[str, dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if start:
        where.append("created_at >= :start")
        params["start"] = start
    if end:
        where.append("created_at <= :end")
        params["end"] = end
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return clause, params


def summary(*, start: str | None, end: str | None) -> dict[str, Any]:
    w, p = _range_clauses(start, end)
    with db_session() as s:
        row = s.execute(text(
            "SELECT COUNT(*) AS total_calls, "
            "COALESCE(SUM(total_tokens),0) AS total_tokens, "
            "COALESCE(SUM(cost_usd),0) AS total_cost_usd, "
            "COALESCE(AVG(latency_ms),0) AS avg_latency_ms, "
            "COUNT(*) FILTER (WHERE error IS NOT NULL) AS failures "
            f"FROM ai_outputs{w}"
        ), p).mappings().first()
    out = dict(row) if row else {}
    # Coerce Decimals/Numerics to float so the route serialises cleanly.
    for k, v in list(out.items()):
        if v is None:
            out[k] = 0
        elif hasattr(v, "__float__") and not isinstance(v, (int, bool)):
            out[k] = float(v)
    return out


def by_model(*, start: str | None, end: str | None) -> list[dict[str, Any]]:
    w, p = _range_clauses(start, end)
    with db_session() as s:
        rows = s.execute(text(
            "SELECT model, COUNT(*) AS calls, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens),0) AS completion_tokens, "
            "COALESCE(SUM(cost_usd),0) AS cost_usd, "
            "COALESCE(AVG(latency_ms),0) AS avg_latency_ms "
            f"FROM ai_outputs{w} GROUP BY model ORDER BY cost_usd DESC"
        ), p).mappings().all()
    return [dict(r) for r in rows]


def by_day(*, start: str | None, end: str | None) -> list[dict[str, Any]]:
    w, p = _range_clauses(start, end)
    with db_session() as s:
        rows = s.execute(text(
            "SELECT date_trunc('day', created_at) AS day, call_type, "
            "COALESCE(SUM(cost_usd),0) AS cost_usd, "
            "COUNT(*) AS calls "
            f"FROM ai_outputs{w} GROUP BY 1, 2 ORDER BY 1"
        ), p).mappings().all()
    return [dict(r) for r in rows]


def list_calls(*, start: str | None, end: str | None, model: str | None,
               status: str | None, q: str | None, limit: int = 50,
               offset: int = 0) -> list[dict[str, Any]]:
    where, p = _range_clauses(start, end)
    extra: list[str] = []
    if model:
        extra.append("model = :model")
        p["model"] = model
    if status == "failed":
        extra.append("error IS NOT NULL")
    elif status == "ok":
        extra.append("error IS NULL")
    if q:
        extra.append("(error ILIKE :q OR model ILIKE :q)")
        p["q"] = f"%{q}%"
    if extra:
        where = (where + (" AND " if where else " WHERE ") + " AND ".join(extra))
    p["lim"] = limit
    p["off"] = offset
    with db_session() as s:
        rows = s.execute(text(
            f"SELECT id::text, created_at, job_id::text, patient_id, document_id, model, call_type, "
            f"prompt_tokens, completion_tokens, total_tokens, latency_ms, cost_usd, error "
            f"FROM ai_outputs{where} ORDER BY created_at DESC LIMIT :lim OFFSET :off"
        ), p).mappings().all()
    return [dict(r) for r in rows]


def get_call(call_id: str) -> dict[str, Any] | None:
    with db_session() as s:
        row = s.execute(text(
            "SELECT * FROM ai_outputs WHERE id = CAST(:id AS uuid)"
        ), {"id": call_id}).mappings().first()
    return dict(row) if row else None
