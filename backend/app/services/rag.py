"""RAG (retrieval-augmented Q&A) + patient-search service.

This module hosts:
- parse_cited_indices(markdown) — extracts the set of [N] indices the LLM
  referenced in its answer.
- build_citations(chunks, answer) — pairs each retrieved chunk with a 1-based
  index and a `cited` flag indicating whether the LLM referenced it.
- ask(req) — top-level RAG orchestrator (added in Task 3).
- search_patients(q, limit) — patient-search orchestrator (added in Task 3).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

from app.config import Settings
from app.db.postgres import db_session
from app.schemas.rag import (
    PatientSearchHit, PatientSearchResponse, PatientSearchSnippet,
    RagAskRequest, RagAskResponse, RagCitation,
)
from app.services.ai_provider import get_ai_provider
from app.services.embeddings import _pgvector_literal, vector_search
from app.services.runtime_config import effective as effective_settings


_CHAT_HISTORY_MAX_TURNS = 6
_CHAT_HISTORY_MAX_CHARS = 3000


def parse_cited_indices(markdown: str) -> set[int]:
    """Return the set of [N] indices referenced in the LLM answer.

    Only matches contiguous digit runs inside square brackets. '[abc]' and
    '[12.5]' are NOT cited; '[1]' and '[42]' ARE.
    """
    return {int(m.group(1)) for m in re.finditer(r"\[(\d+)\]", markdown or "")}


def build_citations(chunks: list[dict[str, Any]], answer: str) -> list[RagCitation]:
    """Pair each retrieved chunk with a 1-based citation number and a `cited`
    flag indicating whether the LLM's answer references it via [N]."""
    cited = parse_cited_indices(answer)
    out: list[RagCitation] = []
    for i, c in enumerate(chunks):
        n = i + 1
        content = (c.get("content") or "")
        out.append(RagCitation(
            n=n,
            refType=str(c.get("ref_type") or ""),
            refId=str(c.get("ref_id") or ""),
            content=content[:300],
            score=float(c.get("similarity") or 0.0),
            cited=(n in cited),
        ))
    return out


def _trim_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Cap history at 6 turns AND ≤ 3000 chars total content. Drops oldest first."""
    out = list(history)
    while len(out) > _CHAT_HISTORY_MAX_TURNS:
        out.pop(0)
    while sum(len(t.get("content", "")) for t in out) > _CHAT_HISTORY_MAX_CHARS and out:
        out.pop(0)
    return out


async def ask(req: RagAskRequest) -> RagAskResponse:
    """RAG orchestrator: verify patient, retrieve chunks, call LLM, build citations."""
    settings: Settings = effective_settings()

    # 1. Verify the patient exists.
    with db_session() as s:
        row = s.execute(
            text("SELECT patient_id FROM patients WHERE patient_id = :pid"),
            {"pid": req.patientId},
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Patient not found")

    # 2. Retrieve top-K chunks (vector_search already returns `similarity`).
    chunks = await vector_search(req.question, patient_id=req.patientId, limit=req.topK)
    if not chunks:
        raise HTTPException(
            status_code=422,
            detail="No embeddings for this patient; ingest a note first.",
        )

    # 3. Trim history (chat mode only) — defensive even in one_shot.
    history = _trim_history(
        [{"role": m.role, "content": m.content} for m in req.history]
    ) if req.mode == "chat" else []

    # 4. Call the LLM.
    t0 = asyncio.get_running_loop().time()
    provider = get_ai_provider()
    answer, rec = await provider.rag_ask(
        question=req.question,
        chunks=chunks,
        history=history,
        patient_id=req.patientId,
    )
    latency_ms = int((asyncio.get_running_loop().time() - t0) * 1000)

    # 5. Build citations.
    citations = build_citations(chunks, answer)

    return RagAskResponse(
        patientId=req.patientId,
        question=req.question,
        answer=answer,
        citations=citations,
        modelUsed=rec.model,
        embeddingModel=settings.AI_EMBEDDING_MODEL,
        latencyMs=latency_ms,
        costUsd=float(rec.cost_usd) if rec.cost_usd is not None else None,
    )


async def search_patients(
    q: str,
    limit: int = 10,
    min_score: float = 0.35,
) -> PatientSearchResponse:
    """Free-text → ranked patient list by max-similarity of any embedding.

    `min_score` filters out patients whose best match is too weak to be
    useful (cosine similarity is in [0, 1] after the `1 - distance`
    transform). 0.35 is a conservative threshold for text-embedding-3-small
    — strong matches sit around 0.5+, weak coincidental term overlap lands
    around 0.2-0.3 and is more confusing than helpful. Callers can override
    via the `minScore` query param (set to 0 to disable).
    """
    settings: Settings = effective_settings()
    t0 = asyncio.get_running_loop().time()
    provider = get_ai_provider()
    try:
        qvec, _rec = await provider.embed(q)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Embedding upstream error: {exc}")
    if not qvec:
        raise HTTPException(status_code=502, detail="Embedding returned empty vector")

    sql = """
        WITH ranked AS (
          SELECT
            e.patient_id,
            1 - (e.embedding <=> CAST(:qvec AS vector)) AS score,
            e.content,
            e.ref_type, e.ref_id,
            ROW_NUMBER() OVER (
              PARTITION BY e.patient_id
              ORDER BY e.embedding <=> CAST(:qvec AS vector) ASC
            ) AS rn
          FROM embeddings e
          WHERE e.patient_id IS NOT NULL
        )
        SELECT
          r.patient_id,
          p.name,
          MAX(r.score) AS score,
          JSON_AGG(JSON_BUILD_OBJECT(
            'refType', r.ref_type, 'refId', r.ref_id,
            'content', LEFT(r.content, 300), 'score', r.score
          ) ORDER BY r.score DESC) FILTER (WHERE r.rn <= 3) AS top_snippets
        FROM ranked r
        LEFT JOIN patients p ON p.patient_id = r.patient_id
        GROUP BY r.patient_id, p.name
        HAVING MAX(r.score) >= :min_score
        ORDER BY MAX(r.score) DESC
        LIMIT :limit
    """
    with db_session() as s:
        rows = s.execute(
            text(sql),
            {"qvec": _pgvector_literal(qvec), "limit": limit, "min_score": min_score},
        ).mappings().all()

    results = [
        PatientSearchHit(
            patientId=r["patient_id"],
            name=r.get("name"),
            score=float(r["score"]),
            snippets=[
                PatientSearchSnippet(
                    refType=s["refType"],
                    refId=s["refId"],
                    content=s["content"],
                    score=float(s["score"]),
                )
                for s in (r.get("top_snippets") or [])
            ],
        )
        for r in rows
    ]
    latency_ms = int((asyncio.get_running_loop().time() - t0) * 1000)
    return PatientSearchResponse(
        query=q,
        embeddingModel=settings.AI_EMBEDDING_MODEL,
        latencyMs=latency_ms,
        results=results,
    )
