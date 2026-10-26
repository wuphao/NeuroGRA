"""Versioned configuration for bounded, source-preserving model views."""
from pydantic import BaseModel, ConfigDict, Field


class ContextPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: str = 'clinical_context_v1'
    soft_chars: int = Field(default=20000, ge=1000)
    safety_tokens: int = Field(default=2048, ge=0)
    max_batch_items: int = Field(default=35, ge=1)
    evidence_chars: int = Field(default=4500, ge=0)
    receipt_chars: int = Field(default=1600, ge=0)
    background_chars: int = Field(default=2200, ge=0)
    previous_chars: int = Field(default=2200, ge=0)
    source_context_chars: int = Field(default=2200, ge=0)

