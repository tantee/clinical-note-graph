"""Vector DB demo routes — RAG Q&A + free-text patient search."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.schemas.rag import (
    PatientSearchResponse, RagAskRequest, RagAskResponse,
)
from app.services.rag import ask, search_patients

router = APIRouter(prefix="/api", tags=["vector-demo"])


@router.post("/rag/ask", response_model=RagAskResponse)
async def rag_ask(req: RagAskRequest) -> RagAskResponse:
    return await ask(req)


@router.get("/search/patients", response_model=PatientSearchResponse)
async def patient_vector_search(
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(10, ge=1, le=50),
) -> PatientSearchResponse:
    return await search_patients(q, limit)
