"""Versioned contracts. Only external PatientInput permits arbitrary fields."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictStr, field_validator

Domain = Literal["history", "cognition", "laboratory", "imaging", "background", "unknown"]
Role = Literal["history", "cognition", "laboratory", "imaging"]
ROLES = ("history", "cognition", "laboratory", "imaging")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)
    schema_version: Literal["1"] = "1"


class PatientInput(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    patient_id: StrictStr = Field(alias="患者ID")

    @field_validator("patient_id")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("患者ID must not be blank")
        return value


class Issue(Contract):
    code: str
    stage: str
    message: str
    retryable: bool = False
    affected_refs: list[str] = Field(default_factory=list)


class TimeValue(Contract):
    raw: str | None = None
    value: str | None = None
    precision: Literal["day", "month", "year", "text", "unknown"] = "unknown"


class SourceRecord(Contract):
    record_id: str
    kind: Literal["field", "attachment", "segment"]
    locator: dict[str, JsonValue] = Field(default_factory=dict)
    raw_value: JsonValue = None
    text: str = ""
    context: dict[str, JsonValue] = Field(default_factory=dict)
    path: str | None = None
    content_hash: str
    parse_status: str = "available"


class Attachment(Contract):
    attachment_id: str
    record_id: str
    original_path: str
    resolved_path: str | None = None
    context: dict[str, JsonValue] = Field(default_factory=dict)
    status: Literal["available", "missing", "forbidden", "invalid"]


class ImageAsset(Contract):
    asset_id: str
    modality: str | None = None
    time: TimeValue = Field(default_factory=TimeValue)
    path: str | None = None
    sequence: str | None = None
    tracer: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    status: str = "uninspected"


class IntakeResult(Contract):
    patient_id: str
    raw_snapshot: dict[str, JsonValue]
    raw_input_hash: str
    records: list[SourceRecord] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    images: list[ImageAsset] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class ParseResult(Contract):
    attachment_id: str
    parser: str
    parser_version: str = "1"
    status: Literal["ok", "partial", "requires_ocr", "unsupported", "failed"]
    segments: list[SourceRecord] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class Observation(Contract):
    observation_id: str
    name: str
    domains: list[Domain]
    value: JsonValue
    unit: str | None = None
    status: Literal["observed", "present", "absent", "not_recorded", "not_performed", "uncertain", "conflicting"]
    time: TimeValue = Field(default_factory=TimeValue)
    context: dict[str, JsonValue] = Field(default_factory=dict)
    source_refs: list[str]
    quote: str
    origin: Literal["provided", "extracted", "tool_result"] = "provided"
    kind: Literal["observation", "historical_diagnosis"] = "observation"


class InventoryItem(Contract):
    domain: Domain
    availability: Literal["available", "partial", "absent", "unreadable", "unclassified"]
    record_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CaseSnapshot(Contract):
    case_id: str
    patient_id: str
    version: int = Field(default=1, ge=1)
    raw_input_hash: str
    records: list[SourceRecord]
    observations: list[Observation] = Field(default_factory=list)
    images: list[ImageAsset] = Field(default_factory=list)
    inventory: list[InventoryItem] = Field(default_factory=list)
    processing: dict[str, str] = Field(default_factory=dict)
    timeline: list[str] = Field(default_factory=list)
    conflict_groups: list[list[str]] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class TaskBudget(Contract):
    max_llm_calls: int = Field(default=3, ge=0)
    max_retrieval_calls: int = Field(default=2, ge=0)
    timeout_seconds: float = Field(default=120, gt=0)


class AgentTask(Contract):
    task_id: str
    agent_type: Role
    case_version: int = Field(ge=1)
    input_refs: list[str]
    background_refs: list[str] = Field(default_factory=list)
    questions: list[str]
    why: str
    permitted_tools: list[Literal["read_case_record", "search_knowledge", "diamond_predict"]]
    budget: TaskBudget = Field(default_factory=TaskBudget)


class TaskPlan(Contract):
    tasks: list[AgentTask]
    skipped: dict[str, str] = Field(default_factory=dict)
    mode: Literal["model", "deterministic_fallback", "empty"] = "model"
    issues: list[Issue] = Field(default_factory=list)


class Claim(Contract):
    claim_id: str
    text: str
    kind: Literal["description", "interpretation", "model_classification"]
    level: str
    observation_ids: list[str]
    evidence_ids: list[str] = Field(default_factory=list)
    tool_result_ids: list[str] = Field(default_factory=list)
    strength: Literal["descriptive", "tentative", "conditional", "supported"]
    limitations: list[str] = Field(default_factory=list)


class SpecialistItem(Contract):
    observation_id: str
    name: str
    value: JsonValue
    quote: str
    unit: str | None = None
    time: TimeValue = Field(default_factory=TimeValue)
    status: str
    source_refs: list[str]
    context: dict[str, JsonValue] = Field(default_factory=dict)
    missing_context: list[str] = Field(default_factory=list)


class AgentResult(Contract):
    result_id: str
    task_id: str
    case_version: int
    version: int = 1
    status: Literal["completed", "partial", "skipped_no_data", "failed"]
    used_refs: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    scale_summaries: list[SpecialistItem] = Field(default_factory=list)
    cognitive_findings: list[str] = Field(default_factory=list)
    functional_findings: list[str] = Field(default_factory=list)
    interpretable_scope: list[str] = Field(default_factory=list)
    test_findings: list[SpecialistItem] = Field(default_factory=list)
    report_flags: list[SpecialistItem] = Field(default_factory=list)
    method_constraints: list[str] = Field(default_factory=list)
    comparable_series: list[list[str]] = Field(default_factory=list)
    report_findings: list[SpecialistItem] = Field(default_factory=list)
    model_results: list[str] = Field(default_factory=list)
    longitudinal_comparison: list[str] = Field(default_factory=list)
    discrepancies: list[str] = Field(default_factory=list)


class RetrievalRequest(Contract):
    request_id: str
    question: str = Field(min_length=1)
    patient_fact_refs: list[str] = Field(default_factory=list)
    candidate_terms: list[str] = Field(default_factory=list)
    known_conditions: list[str] = Field(default_factory=list)
    unknown_conditions: list[str] = Field(default_factory=list)
    requested_backends: list[Literal["bm25", "graph", "vector"]] = Field(default_factory=lambda: ["bm25", "graph", "vector"], min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    knowledge_release_id: str | None = None


class EvidenceItem(Contract):
    evidence_id: str
    document_id: str
    span_id: str
    clause_ids: list[str] = Field(default_factory=list)
    title: str | None = None
    quote: str
    context: str | None = None
    page: int | None = None
    version: str
    matched_by: list[str]
    review_provenance: str = "unknown"
    applicability: Literal["unknown", "applicable", "not_applicable"] = "unknown"
    allowed_use: Literal["background", "conditional_support", "direct_support"] = "background"
    limitations: list[str] = Field(default_factory=lambda: ["未核查当前患者的适用条件"])
    backend_ranks: dict[str, int] = Field(default_factory=dict)
    fusion_score: float | None = None


class EvidenceBundle(Contract):
    bundle_id: str
    request_id: str
    status: Literal["ok", "empty", "degraded", "failed"]
    backend_status: dict[str, str]
    items: list[EvidenceItem] = Field(default_factory=list)
    uncovered_questions: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    retrieval_metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ReviewRequest(Contract):
    request_id: str
    target_agent: Role
    target_result_id: str
    target_result_version: int
    claim_ids: list[str]
    question: str
    allowed_actions: list[str]
    success_criterion: str
    if_unresolved: str


class ReviewResponse(Contract):
    request_id: str
    resolution: Literal["corrected", "clarified", "maintained_with_evidence", "unresolved", "failed"]
    answers: list[str]
    revised_result: AgentResult | None = None


class ClaimDependency(Contract):
    task_id: str
    result_id: str
    result_version: int
    claim_id: str


class FinalReport(Contract):
    natural_language_report: str = ''
    narrative_status: Literal['not_generated', 'generated', 'unavailable'] = 'not_generated'
    narrative_source_ids: list[str] = Field(default_factory=list)
    patient_id: str
    run_id: str
    status: Literal["completed", "partial", "failed"]
    assessment_status: Literal["supported_tendency", "conditional", "unresolved", "insufficient_data"]
    summary: str
    claims: list[Claim]
    unresolved: list[str]
    citations: list[str]
    agent_results: list[AgentResult] = Field(default_factory=list)
    tool_result_ids: list[str] = Field(default_factory=list)
    claim_dependencies: dict[str, list[ClaimDependency]] = Field(default_factory=dict)


class Capability(Contract):
    name: str
    configured: bool
    available: bool
    verified: bool = False
    details: dict[str, JsonValue] = Field(default_factory=dict)


class CapabilityReport(Contract):
    components: list[Capability]
    issues: list[Issue] = Field(default_factory=list)
