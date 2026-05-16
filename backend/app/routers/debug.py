from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.services import debug_queries

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/summary")
def get_summary(start: str | None = None, end: str | None = None) -> dict[str, Any]:
    return debug_queries.summary(start=start, end=end)


@router.get("/by-model")
def get_by_model(start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    return debug_queries.by_model(start=start, end=end)


@router.get("/by-day")
def get_by_day(start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    return debug_queries.by_day(start=start, end=end)


@router.get("/ai-calls")
def get_calls(
    start: str | None = None,
    end: str | None = None,
    model: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    return debug_queries.list_calls(
        start=start, end=end, model=model, status=status, q=q, limit=limit, offset=offset,
    )


@router.get("/ai-calls.csv")
def ai_calls_csv(
    start: str | None = None,
    end: str | None = None,
    model: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> StreamingResponse:
    rows = debug_queries.list_calls(
        start=start, end=end, model=model, status=status, q=q, limit=10_000, offset=0,
    )

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([
            "id", "created_at", "job_id", "patient_id", "model", "call_type",
            "prompt_tokens", "completion_tokens", "total_tokens", "latency_ms",
            "cost_usd", "error",
        ])
        yield buf.getvalue()
        buf.seek(0); buf.truncate()
        for r in rows:
            w.writerow([
                r.get("id"), r.get("created_at"), r.get("job_id"), r.get("patient_id"),
                r.get("model"), r.get("call_type"), r.get("prompt_tokens"),
                r.get("completion_tokens"), r.get("total_tokens"), r.get("latency_ms"),
                r.get("cost_usd"), r.get("error"),
            ])
            yield buf.getvalue()
            buf.seek(0); buf.truncate()

    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ai-calls.csv"},
    )


@router.get("/ai-calls/{call_id}")
def get_call(call_id: str) -> dict[str, Any]:
    row = debug_queries.get_call(call_id)
    if not row:
        raise HTTPException(status_code=404, detail="AI call not found")
    return row
