"""Shared schema helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    """Base model that rejects unknown fields and assignment type drift."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


class SchemaModel(StrictBaseModel):
    """Base schema with version and creation timestamp."""

    schema_version: str = "1"
    created_at: datetime = Field(default_factory=utc_now)


class SourceRole(StrEnum):
    CLINICAL_GUIDELINE = "clinical_guideline"
    CLINICAL_RESEARCH = "clinical_research"
    METHOD_REFERENCE = "method_reference"


class ExtractionMethod(StrEnum):
    NATIVE_TEXT = "native_text"
    OCR = "ocr"
    MANUAL_TRANSCRIPTION = "manual_transcription"


JsonDict = dict[str, Any]
