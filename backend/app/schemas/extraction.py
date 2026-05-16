from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import CodingSystem, ReviewStatus


class StrictBase(BaseModel):
    """Strict schema base: rejects unknown fields so AI output is rigorously validated."""

    model_config = ConfigDict(extra="forbid")


class PatientFact(StrictBase):
    id: str | None = None
    type: str = Field(..., description="e.g. condition, observation, medication, procedure, allergy, plan")
    value: str
    normalizedCode: str | None = None
    codingSystem: CodingSystem | None = None
    dateTime: datetime | None = None
    sourceDocumentId: str | None = None
    evidenceText: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reviewStatus: ReviewStatus = "ai_suggested"
    extra: dict[str, Any] = Field(default_factory=dict)


class DiagnosisCandidate(StrictBase):
    condition: str
    icd10: str | None = None
    snomed: str | None = None
    rationale: str | None = None
    evidenceText: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    role: Literal["primary", "secondary", "complication", "comorbidity", "candidate"] = "candidate"


class MedicationChange(StrictBase):
    name: str
    rxNorm: str | None = None
    action: Literal["start", "continue", "stop", "modify", "hold"] = "start"
    dose: str | None = None
    route: str | None = None
    frequency: str | None = None
    indication: str | None = None
    evidenceText: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ObservationResult(StrictBase):
    name: str
    loinc: str | None = None
    value: str
    unit: str | None = None
    refRange: str | None = None
    abnormalFlag: Literal["L", "H", "N", "C", None] = None
    dateTime: datetime | None = None
    evidenceText: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class PlanItem(StrictBase):
    description: str
    addressesCondition: str | None = None
    category: Literal["medication", "procedure", "lab", "imaging", "consult", "education", "followup", "other"] = "other"
    evidenceText: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class CodingCandidate(StrictBase):
    code: str
    system: CodingSystem
    display: str
    forCondition: str
    rationale: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class GraphUpdateInstruction(StrictBase):
    op: Literal["upsert_condition", "upsert_medication", "upsert_observation", "upsert_procedure", "upsert_allergy", "link", "upsert_plan"]
    payload: dict[str, Any]


class MarkdownUpdateInstruction(StrictBase):
    path: str
    op: Literal["create_or_update_section", "append", "replace"]
    section: str | None = None
    content: str


class ClinicalExtractionResult(StrictBase):
    """The strict schema the AI must conform to."""

    patientId: str
    encounterId: str | None = None
    documentId: str | None = None
    summary: str = ""
    problems: list[PatientFact] = Field(default_factory=list)
    medications: list[MedicationChange] = Field(default_factory=list)
    observations: list[ObservationResult] = Field(default_factory=list)
    procedures: list[PatientFact] = Field(default_factory=list)
    allergies: list[PatientFact] = Field(default_factory=list)
    plans: list[PlanItem] = Field(default_factory=list)
    diagnoses: list[DiagnosisCandidate] = Field(default_factory=list)
    codingCandidates: list[CodingCandidate] = Field(default_factory=list)
    graphUpdates: list[GraphUpdateInstruction] = Field(default_factory=list)
    markdownUpdates: list[MarkdownUpdateInstruction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
