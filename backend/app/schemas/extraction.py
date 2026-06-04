from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import CodingSystem, ReviewStatus


# Allowed severities and clinical status values for conditions / problems.
# Kept as Literal so the model's output gets validated cleanly; None is the
# safe default — a smaller model can omit these without failing validation.
Severity = Literal["mild", "moderate", "severe", "critical", "unknown"]
ClinicalStatus = Literal[
    "active", "resolved", "in_remission", "recurrent",
    "suspected", "ruled_out", "inactive",
]
StartQualifier = Literal["exact", "estimated", "before", "unknown"]
StopQualifier = Literal["exact", "estimated", "ongoing", "unknown"]


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
    # Temporality + severity additions. All optional — leave null when the
    # source EMR doesn't say. The graph layer paints node borders / styling
    # off these so the same condition with "severe / active" stands out
    # from a "resolved" mention.
    severity: Severity | None = None
    status: ClinicalStatus | None = None
    onsetDate: datetime | None = None
    resolvedDate: datetime | None = None
    # Qualifier + free-text hedges for the onset/resolved dates above
    onsetQualifier: StartQualifier | None = None
    resolvedQualifier: StopQualifier | None = None
    onsetText: str | None = None
    resolvedText: str | None = None
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
    startDate: datetime | None = None
    startQualifier: StartQualifier | None = None
    stopDate: datetime | None = None
    stopQualifier: StopQualifier | None = None
    startText: str | None = None
    stopText: str | None = None
    schedule: str | None = None
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


FactType = Literal["condition", "medication", "observation", "procedure", "allergy", "plan"]

# Inter-fact relationships the AI extractor is asked to declare explicitly.
# Each value is the canonical *clinical* relationship name; the graph layer
# maps them to Cypher relationship types directly so the LLM doesn't see
# Neo4j-specific names. Keep this list curated — too many edge types make
# the rendered graph noisier, not clearer.
RelationKind = Literal[
    # Medication / plan → condition
    "treats", "addresses",
    # Observation → condition
    "monitors", "diagnostic_of",
    # Condition → condition
    "causes", "due_to", "complication_of", "co_occurs", "related_to",
    # Observation → observation
    "panel_member_of",
    # Generic chronological / sequence
    "precedes", "follows",
]


class FactRelationship(StrictBase):
    """An AI-declared link between two facts that aren't naturally siblings.

    The graph layer uses these to draw edges between specific fact nodes
    instead of routing everything through Patient or Encounter. The LLM
    decides the relationship; we don't second-guess it (other than schema
    validation). When the LLM omits the list entirely, the older heuristic
    and co-occurrence paths still produce reasonable defaults.

    Match `sourceValue` / `targetValue` against the corresponding fact's
    `value` (conditions / procedures / allergies / plans) or `name`
    (medications / observations) — case-insensitive in the graph layer.
    """

    sourceType: FactType
    sourceValue: str
    targetType: FactType
    targetValue: str
    relation: RelationKind
    evidenceText: str | None = None
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
    # AI-declared inter-fact relationships. Optional — when the model omits
    # them the existing heuristics (TREATS, CO_OCCURS thresholding, the
    # observation→condition substring map) still produce a usable graph.
    relationships: list[FactRelationship] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
