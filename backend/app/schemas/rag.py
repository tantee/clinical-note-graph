"""Pydantic models for the RAG + patient-search endpoints."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RagAskMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class RagAskRequest(BaseModel):
    patientId: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1, max_length=2000)
    mode: Literal["one_shot", "chat"] = "one_shot"
    history: list[RagAskMessage] = Field(default_factory=list, max_length=20)
    topK: int = Field(default=8, ge=1, le=20)


class RagCitation(BaseModel):
    n: int
    refType: str
    refId: str
    content: str
    score: float
    cited: bool


class RagAskResponse(BaseModel):
    patientId: str
    question: str
    answer: str
    citations: list[RagCitation]
    modelUsed: str
    embeddingModel: str
    latencyMs: int
    costUsd: float | None = None


class PatientSearchSnippet(BaseModel):
    refType: str
    refId: str
    content: str
    score: float


class PatientSearchHit(BaseModel):
    patientId: str
    name: str | None = None
    score: float
    snippets: list[PatientSearchSnippet]


class PatientSearchResponse(BaseModel):
    query: str
    embeddingModel: str
    latencyMs: int
    results: list[PatientSearchHit]
