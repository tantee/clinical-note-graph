from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import text

from app.db.helpers import j
from app.db.postgres import db_session
from app.services.ai_provider import get_ai_provider

logger = logging.getLogger(__name__)

_EMBED_CONCURRENCY = 8


def _pgvector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


async def embed_and_store(*, patient_id: str, ref_type: str, ref_id: str, content: str, metadata: dict[str, Any] | None = None, job_id: str | None = None) -> None:
    provider = get_ai_provider()
    try:
        vec, _rec = await provider.embed(
            content,
            job_id=job_id,
            patient_id=patient_id,
            ref_id=ref_id,
        )
    except Exception:
        return
    if not vec:
        return
    with db_session() as s:
        s.execute(
            text(
                """
                INSERT INTO embeddings (patient_id, ref_type, ref_id, content, embedding, metadata)
                VALUES (:p, :rt, :ri, :c, CAST(:e AS vector), CAST(:m AS jsonb))
                """
            ),
            {
                "p": patient_id, "rt": ref_type, "ri": ref_id, "c": content,
                "e": _pgvector_literal(vec), "m": j(metadata or {}),
            },
        )


async def embed_and_store_many(*, patient_id: str, items: list[dict[str, Any]], job_id: str | None = None) -> int:
    """Embed many texts concurrently (bounded) and write the resulting rows in one transaction."""
    if not items:
        return 0
    provider = get_ai_provider()
    sem = asyncio.Semaphore(_EMBED_CONCURRENCY)

    async def embed_one(item: dict[str, Any]) -> dict[str, Any] | None:
        async with sem:
            try:
                vec, _rec = await provider.embed(
                    item["content"],
                    job_id=job_id,
                    patient_id=patient_id,
                    ref_id=item.get("ref_id"),
                )
            except Exception as exc:
                logger.warning("embedding failed for %s: %s", item.get("ref_id"), exc)
                return None
        if not vec:
            return None
        return {
            "p": patient_id,
            "rt": item["ref_type"],
            "ri": item["ref_id"],
            "c": item["content"],
            "e": _pgvector_literal(vec),
            "m": j(item.get("metadata") or {}),
        }

    embedded = [r for r in await asyncio.gather(*[embed_one(i) for i in items]) if r]
    if not embedded:
        return 0
    with db_session() as s:
        s.execute(
            text(
                """
                INSERT INTO embeddings (patient_id, ref_type, ref_id, content, embedding, metadata)
                VALUES (:p, :rt, :ri, :c, CAST(:e AS vector), CAST(:m AS jsonb))
                """
            ),
            embedded,
        )
    return len(embedded)


async def vector_search(query: str, *, patient_id: str | None, limit: int = 10) -> list[dict[str, Any]]:
    provider = get_ai_provider()
    try:
        vec, _rec = await provider.embed(query)
    except Exception:
        return []
    if not vec:
        return []
    sql = """
        SELECT ref_type, ref_id, content, patient_id,
               1 - (embedding <=> CAST(:e AS vector)) AS similarity
        FROM embeddings
        WHERE (CAST(:p AS TEXT) IS NULL OR patient_id = :p)
        ORDER BY embedding <=> CAST(:e AS vector)
        LIMIT :lim
    """
    with db_session() as s:
        rows = s.execute(text(sql), {"e": _pgvector_literal(vec), "p": patient_id, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]
