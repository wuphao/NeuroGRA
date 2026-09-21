"""Source document and provenance schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from neurogra.knowledge.schemas.common import ExtractionMethod, SchemaModel, SourceRole, StrictBaseModel

BBox = tuple[float, float, float, float]


class TableCell(StrictBaseModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    text: str
    bbox: BBox | None = None
    is_header: bool = False


class ParsedTable(StrictBaseModel):
    rows: int = Field(ge=1)
    cols: int = Field(ge=1)
    cells: list[TableCell]
    table_group_id: str | None = None


class Document(SchemaModel):
    document_id: str
    checksum: str
    source_path: str
    snapshot_path: str
    title: str | None = None
    source_role: SourceRole
    language: str | None = None
    publication_date: str | None = None
    source_version: str | None = None
    doi: str | None = None
    organization: str | None = None
    metadata_status: Literal["pending", "verified"] = "pending"
    source_family_id: str | None = None
    supersedes_document_id: str | None = None
    page_count: int | None = Field(default=None, ge=1)


class ParsedBlock(SchemaModel):
    block_id: str
    parse_id: str
    document_id: str
    page_no: int = Field(ge=1)
    printed_page_label: str | None = None
    reading_order: int = Field(ge=0)
    block_type: Literal["heading", "paragraph", "list_item", "table", "caption", "footnote", "figure"]
    raw_text: str
    bbox: BBox | None = None
    page_width: float | None = Field(default=None, gt=0)
    page_height: float | None = Field(default=None, gt=0)
    extraction_method: ExtractionMethod
    parser_quality: float | None = Field(default=None, ge=0, le=1)
    table: ParsedTable | None = None
    issues: list[str] = Field(default_factory=list)

    @field_validator("bbox")
    @classmethod
    def bbox_must_be_normalized(cls, value: BBox | None) -> BBox | None:
        if value is None:
            return None
        x0, y0, x1, y1 = value
        if not (0 <= x0 <= x1 <= 1 and 0 <= y0 <= y1 <= 1):
            raise ValueError("bbox must be normalized as [x0,y0,x1,y1] within [0,1]")
        return value

    @model_validator(mode="after")
    def table_blocks_require_table_payload(self) -> "ParsedBlock":
        if self.block_type == "table" and self.table is None:
            raise ValueError("table block requires table payload")
        return self


class SourceLocator(StrictBaseModel):
    block_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def end_must_follow_start(self) -> "SourceLocator":
        if self.end <= self.start:
            raise ValueError("locator.end must be greater than locator.start")
        return self


class EvidenceRef(StrictBaseModel):
    span_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str

    @model_validator(mode="after")
    def quote_must_match_non_empty_range(self) -> "EvidenceRef":
        if self.end <= self.start:
            raise ValueError("evidence.end must be greater than evidence.start")
        if not self.quote:
            raise ValueError("evidence.quote must be non-empty")
        return self


class NormalizationMapItem(StrictBaseModel):
    normalized_start: int = Field(ge=0)
    normalized_end: int = Field(ge=0)
    original_start: int = Field(ge=0)
    original_end: int = Field(ge=0)
    locator_ordinal: int | None = Field(default=None, ge=0)


class SourceSpan(SchemaModel):
    span_id: str
    parse_id: str
    document_id: str
    section_path: list[str] = Field(default_factory=list)
    locators: list[SourceLocator]
    original_text: str
    normalized_text: str
    normalization_map: list[NormalizationMapItem] = Field(default_factory=list)
    context_span_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)

    @field_validator("locators")
    @classmethod
    def locators_must_not_be_empty(cls, value: list[SourceLocator]) -> list[SourceLocator]:
        if not value:
            raise ValueError("SourceSpan.locators must not be empty")
        return value
