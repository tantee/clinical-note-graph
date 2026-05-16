from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import CodingSystem
from app.schemas.extraction import CodingCandidate, DiagnosisCandidate


CodingStandard = Literal["ICD10", "SNOMEDCT", "LOINC", "RxNorm"]


class CodingSuggestRequest(BaseModel):
    standards: list[CodingStandard] = Field(default_factory=lambda: ["ICD10", "SNOMEDCT"])
    includeEvidence: bool = True


class CodingSuggestResponse(BaseModel):
    patientId: str
    primaryDiagnosis: DiagnosisCandidate | None = None
    secondaryDiagnoses: list[DiagnosisCandidate] = Field(default_factory=list)
    complications: list[DiagnosisCandidate] = Field(default_factory=list)
    comorbidities: list[DiagnosisCandidate] = Field(default_factory=list)
    codingCandidates: list[CodingCandidate] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "AI-assisted suggestion only. All codes require human coder review before billing or clinical use."
    )


class DateRange(BaseModel):
    start: str | None = None
    end: str | None = None


class SummaryRequest(BaseModel):
    type: Literal["brief", "detailed", "discharge", "problem_oriented", "timeline", "coding_support"] = "brief"
    dateRange: DateRange | None = None
    includeEvidence: bool = True


class SummaryResponse(BaseModel):
    patientId: str
    type: str
    markdown: str
    json_: dict[str, Any] = Field(alias="json", default_factory=dict)
    disclaimer: str = "AI-assisted output requires clinical review."

    model_config = {"populate_by_name": True}


class ExportRequest(BaseModel):
    patientId: str
    exportType: Literal[
        "summary",
        "coding",
        "graph",
        "markdown_vault",
        "fhir_bundle",
        "custom",
    ] = "summary"
    profileId: str | None = None


class ExportProfilePayload(BaseModel):
    profileId: str
    name: str
    config: dict[str, Any]
