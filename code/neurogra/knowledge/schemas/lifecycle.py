"""Task, review, and release lifecycle schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from neurogra.knowledge.schemas.common import JsonDict, SchemaModel


class ReviewDecision(SchemaModel):
    decision_id: str
    object_type: Literal["chunk", "clause_revision", "document"]
    object_id: str
    expected_revision_id: str | None = None
    action: Literal["approve", "amend", "reject"]
    revised_payload: JsonDict | None = None
    reviewer_id: str
    reason: str | None = None
    decided_at: datetime


class Task(SchemaModel):
    task_id: str
    run_id: str
    stage: str
    input_ids: list[str] = Field(default_factory=list)
    cache_key: str
    status: Literal["pending", "running", "succeeded", "failed", "blocked", "skipped"]
    attempt: int = Field(default=0, ge=0)
    output_artifacts: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ReleaseManifest(SchemaModel):
    release_id: str
    document_ids: list[str]
    parse_ids: list[str]
    clause_revision_ids: list[str]
    chunk_ids: list[str]
    terminology_version: str
    parser_versions: dict[str, str] = Field(default_factory=dict)
    prompt_hash: str | None = None
    extraction_model: str | None = None
    embedding_model: str | None = None
    embedding_dimension: int | None = Field(default=None, ge=1)
    tokenizer_version: str | None = None
    config_hash: str
    artifact_checksums: dict[str, str] = Field(default_factory=dict)
    bm25_path: str | None = None
    qdrant_collection: str | None = None
    neo4j_release_id: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    checks: dict[str, bool] = Field(default_factory=dict)
