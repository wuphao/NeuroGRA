"""Validated domain objects shared by build, retrieval and storage."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


Language = Literal["zh", "en", "mixed", "und"]


class ChineseAnnotation(Contract):
    text: str = ""
    status: Literal["unverified", "verified", "failed"] = "unverified"
    generator_binding: dict = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def verified_has_text(self):
        if self.status == "verified" and not self.text.strip():
            raise ValueError("verified_chinese_expression_requires_text")
        return self


class SourceRef(Contract):
    block_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_range(self):
        if self.end <= self.start:
            raise ValueError("source range must be nonempty")
        return self


class Document(Contract):
    document_id: str
    checksum: str
    source_path: str
    snapshot_path: str
    title: str
    version: str
    language: Language = "und"


class TableCell(Contract):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    text: str
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    is_header: bool = False


class Block(Contract):
    block_id: str
    document_id: str
    page: int = Field(ge=1)
    page_kind: Literal["physical", "unavailable"] = "physical"
    source_locator: str | None = None
    order: int = Field(ge=0)
    kind: Literal["heading", "paragraph", "list_item", "table", "table_cell",
                  "caption", "footnote", "figure"]
    text: str
    bbox: tuple[float, float, float, float] | None = None
    section_path: list[str] = Field(default_factory=list)
    heading_level: int | None = Field(default=None, ge=1)
    marker: str | None = None
    reference_markers: list[str] = Field(default_factory=list)
    fragment_of: str | None = None
    source_start: int | None = Field(default=None, ge=0)
    source_end: int | None = Field(default=None, ge=1)
    table_id: str | None = None
    cells: list[TableCell] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class StructureEdge(Contract):
    source: str
    target: str
    relation: Literal["CONTAINS", "NEXT", "REFERS_TO", "HEADER_OF", "NOTE_OF", "CONTINUES"]
    certainty: Literal["explicit", "heuristic"]
    reason: str


class ParsedDocument(Contract):
    document: Document
    blocks: list[Block]
    edges: list[StructureEdge] = Field(default_factory=list)
    parser: str
    parser_version: str
    issues: list[str] = Field(default_factory=list)


class Entity(Contract):
    entity_id: str
    name: str
    entity_type: Literal["disease", "syndrome", "phenotype", "examination", "biomarker", "other"]
    aliases: list[str] = Field(default_factory=list)
    status: Literal["resolved", "ambiguous", "local"] = "local"
    candidate_ids: list[str] = Field(default_factory=list)
    label_zh: str | None = None
    label_en: str | None = None


class AtomCondition(Contract):
    kind: Literal["ATOM"] = "ATOM"
    condition_id: str
    field: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "exists"]
    value: str | float | bool | list[str | float]
    unit: str | None = None
    method: str | None = None
    time_scope: str | None = None
    population: str | None = None
    source_refs: list[SourceRef] = Field(min_length=1)
    zh_annotation: ChineseAnnotation | None = None

    @model_validator(mode="after")
    def operator_matches(self):
        if self.operator == "exists" and not isinstance(self.value, bool):
            raise ValueError("exists requires bool")
        if self.operator == "in" and not isinstance(self.value, list):
            raise ValueError("in requires list")
        if self.operator in {"gt", "gte", "lt", "lte"} and (
            not isinstance(self.value, (int, float)) or isinstance(self.value, bool)
        ):
            raise ValueError("ordered comparison requires a number")
        return self


class TextCondition(Contract):
    kind: Literal["TEXT"] = "TEXT"
    condition_id: str
    text: str
    source_refs: list[SourceRef] = Field(min_length=1)
    reason: str
    zh_annotation: ChineseAnnotation | None = None


class AllCondition(Contract):
    kind: Literal["ALL"] = "ALL"
    children: list["Condition"] = Field(min_length=2)


class AnyCondition(Contract):
    kind: Literal["ANY"] = "ANY"
    children: list["Condition"] = Field(min_length=2)


class NotCondition(Contract):
    kind: Literal["NOT"] = "NOT"
    child: "Condition"


Condition = Annotated[AtomCondition | TextCondition | AllCondition | AnyCondition | NotCondition,
                      Field(discriminator="kind")]
AllCondition.model_rebuild()
AnyCondition.model_rebuild()
NotCondition.model_rebuild()

Predicate = Literal["supports", "weakens", "associated_with", "distinguishes", "limits"]
Purpose = Literal["diagnostic_assessment", "cognitive_assessment", "imaging_interpretation",
                  "biomarker_interpretation", "differential_diagnosis", "follow_up"]
State = Literal["SATISFIED", "VIOLATED", "UNKNOWN"]


class KnowledgeStatement(Contract):
    statement_id: str
    subject: Entity
    predicate: Predicate
    object: Entity
    assertion_text: str
    assertion_language: Language = "und"
    zh_annotation: ChineseAnnotation | None = None
    condition: Condition | None = None
    condition_status: Literal["explicit_unrestricted", "constrained", "unresolved"] = "unresolved"
    purposes: list[Purpose] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(min_length=1)
    field_evidence: dict[str, list[SourceRef]] = Field(default_factory=dict)
    status: Literal["draft", "source_checked", "approved", "rejected"] = "draft"
    revision: int = Field(default=1, ge=1)
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def condition_consistent(self):
        if self.condition_status == "constrained" and self.condition is None:
            raise ValueError("constrained statement requires a condition")
        if self.condition_status == "explicit_unrestricted" and self.condition is not None:
            raise ValueError("unrestricted statement cannot contain conditions")
        return self


class PatientFact(Contract):
    fact_id: str
    field: str
    value: str | float | bool | list[str | float]
    unit: str | None = None
    polarity: Literal["affirmed", "negated", "uncertain"] = "affirmed"
    observed_at: datetime | None = None
    method: str | None = None
    source_ref: SourceRef


class ConditionResult(Contract):
    condition_id: str
    state: State
    method: Literal["deterministic", "llm_textual", "unresolved"]
    used_fact_ids: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    reason: str


class EvidenceItem(Contract):
    evidence_id: str
    statement: KnowledgeStatement
    state: State
    condition_results: list[ConditionResult] = Field(default_factory=list)
    matched_by: list[str] = Field(default_factory=list)
    score: float
    score_components: dict[str, float] = Field(default_factory=dict)
    release_id: str
    temporary: bool = False


class Gap(Contract):
    gap_id: str
    kind: Literal["patient_data", "knowledge_condition", "source_evidence", "graph_connection"]
    statement_id: str | None = None
    missing_items: list[str]
    priority: int = Field(default=1, ge=0)
    status: Literal["open", "resolved", "requires_patient_data", "pending_review", "unresolved"] = "open"
    reason: str


class DataRequest(Contract):
    gap_id: str
    fields: list[str]
    reason: str


class DocumentInput(Contract):
    path: Path
    title: str | None = None
    version: str = "1"
    language: Language = "und"


class BuildRequest(Contract):
    documents: list[DocumentInput] = Field(min_length=1)
    corpus_version: str
    build_id: str | None = None
    max_seconds: float = Field(default=3600, gt=0)
    max_llm_calls: int = Field(default=1000, ge=1, le=100000)
    max_embedding_calls: int = Field(default=1000, ge=1, le=100000)


class BuildResult(Contract):
    build_id: str
    status: Literal["running", "completed", "partial", "interrupted", "failed"]
    statement_ids: list[str] = Field(default_factory=list)
    pending_ids: list[str] = Field(default_factory=list)
    rejected_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    completed_units: int = Field(default=0, ge=0)
    failed_units: int = Field(default=0, ge=0)
    current_document_id: str | None = None
    current_unit_id: str | None = None
    current_document_units: int = Field(default=0, ge=0)
    progress_event: str | None = None


class RetrievalRequest(Contract):
    facts: list[PatientFact] = Field(default_factory=list)
    purpose: Purpose
    question: str = Field(min_length=1)
    reference_time: datetime
    release_id: str | None = None
    top_k: int = Field(default=10, ge=1, le=100)
    max_supplement_rounds: int = Field(default=2, ge=0, le=10)
    max_seconds: float = Field(default=120, gt=0)
    run_id: str | None = None
    backends: list[Literal["lexical", "vector", "graph"]] = Field(default_factory=lambda: ["lexical", "vector", "graph"], min_length=1)
    max_llm_calls: int = Field(default=20, ge=0, le=100)


class RetrievalResult(Contract):
    run_id: str
    release_id: str
    applicable: list[EvidenceItem] = Field(default_factory=list)
    unknown: list[EvidenceItem] = Field(default_factory=list)
    excluded: list[EvidenceItem] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    data_requests: list[DataRequest] = Field(default_factory=list)
    supplementation_trace: list[dict] = Field(default_factory=list)
    degraded: bool = False
    warnings: list[str] = Field(default_factory=list)
    backend_status: dict[str, str] = Field(default_factory=dict)
