from __future__ import annotations

from typing import Literal

CodingSystem = Literal["ICD10", "SNOMEDCT", "LOINC", "RxNorm", "local"]
ReviewStatus = Literal["ai_suggested", "human_confirmed", "rejected"]
FactType = Literal[
    "condition",
    "medication",
    "observation",
    "procedure",
    "allergy",
    "plan",
    "diagnosis_candidate",
    "coding_candidate",
]
EncounterType = Literal[
    "admission",
    "opd",
    "progress_note",
    "discharge_summary",
    "lab",
    "imaging",
    "operation_note",
    "other",
]
