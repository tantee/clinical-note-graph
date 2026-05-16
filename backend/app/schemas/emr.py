from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import EncounterType


class PatientPayload(BaseModel):
    patientId: str
    name: str | None = None
    gender: str | None = None
    birthDate: date | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EncounterPayload(BaseModel):
    encounterId: str | None = None
    type: EncounterType
    dateTime: datetime
    department: str | None = None
    provider: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourcePayload(BaseModel):
    system: str | None = None
    documentId: str | None = None
    version: str | None = None


class EMRIngestRequest(BaseModel):
    patient: PatientPayload
    encounter: EncounterPayload
    format: Literal["text", "json", "fhir"] = "text"
    content: str | dict[str, Any]
    source: SourcePayload = Field(default_factory=SourcePayload)


class EMRIngestResponse(BaseModel):
    jobId: str
    status: str
    patientId: str
    encounterId: str
    documentId: str
    summary: dict[str, Any] | None = None
