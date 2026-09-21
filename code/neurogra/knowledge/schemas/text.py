"""Text chunk, citation, and retrieval schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from neurogra.knowledge.schemas.common import ExtractionMethod, SchemaModel, SourceRole
from neurogra.knowledge.schemas.source import BBox


class Citation(SchemaModel):
    document_id: str
    title: str | None = None
    source_role: SourceRole
    parse_id: str
    span_id: str
    page_no: int = Field(ge=1)
    printed_page_label: str | None = None
    bbox: BBox | None = None
    quote: str
    extraction_method: ExtractionMethod


class Chunk(SchemaModel):
    chunk_id: str
    chunk_build_id: str
    document_id: str
    span_ids: list[str]
    parent_chunk_id: str | None = None
    role: Literal["parent", "child"]
    original_text: str
    retrieval_text: str
    section_path: list[str] = Field(default_factory=list)
    token_count: int = Field(ge=0)
    tokenizer_version: str
    text_review_status: Literal["pending", "approved", "rejected"] = "pending"
    linked_clause_ids: list[str] = Field(default_factory=list)


class SearchHit(SchemaModel):
    chunk_id: str
    score: float
    matched_by: list[Literal["bm25", "vector", "graph"]]
    original_text: str
    retrieval_text: str
    context_text: str | None = None
    citations: list[Citation]
    clause_ids: list[str] = Field(default_factory=list)
    source_role: SourceRole
