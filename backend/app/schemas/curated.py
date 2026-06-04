from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.extraction import StartQualifier, StopQualifier

CuratedType = Literal["condition", "medication"]


class CuratedItem(BaseModel):
    """Response shape for one curated row. camelCase field names (emitted to the
    frontend); validation_alias reads the snake_case DB dict."""
    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: CuratedType
    displayValue: str = Field(validation_alias="display_value")
    normalizedCode: str | None = Field(default=None, validation_alias="normalized_code")
    codingSystem: str | None = Field(default=None, validation_alias="coding_system")
    startDate: str | None = Field(default=None, validation_alias="start_date")
    startQualifier: str = Field(validation_alias="start_qualifier")
    stopDate: str | None = Field(default=None, validation_alias="stop_date")
    stopQualifier: str = Field(validation_alias="stop_qualifier")
    startText: str | None = Field(default=None, validation_alias="start_text")
    stopText: str | None = Field(default=None, validation_alias="stop_text")
    scheduleText: str | None = Field(default=None, validation_alias="schedule_text")
    status: str | None = None
    recordState: str = Field(validation_alias="record_state")
    reviewStatus: str = Field(validation_alias="review_status")
    origin: str
    humanEditedFields: list[str] = Field(default_factory=list, validation_alias="human_edited_fields")


class CuratedList(BaseModel):
    items: list[CuratedItem]


class CuratedCreate(BaseModel):
    type: CuratedType
    displayValue: str
    normalizedCode: str | None = None
    codingSystem: str | None = None
    startDate: str | None = None
    startQualifier: StartQualifier | None = None
    stopDate: str | None = None
    stopQualifier: StopQualifier | None = None
    startText: str | None = None
    stopText: str | None = None
    scheduleText: str | None = None
    status: str | None = None


class CuratedPatch(BaseModel):
    """All optional — only supplied fields are touched and marked human-edited."""
    displayValue: str | None = None
    startDate: str | None = None
    startQualifier: StartQualifier | None = None
    stopDate: str | None = None
    stopQualifier: StopQualifier | None = None
    startText: str | None = None
    stopText: str | None = None
    scheduleText: str | None = None
    status: str | None = None
