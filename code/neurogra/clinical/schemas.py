"""Versioned contracts. Only external PatientInput permits arbitrary fields."""
from __future__ import annotations
from datetime import date, datetime

from typing import Any, Literal
from pydantic import (AliasChoices, AliasPath, BaseModel, ConfigDict, Field, JsonValue,
                      StrictStr, field_validator, model_validator)

Domain = Literal["history", "cognition", "laboratory", "imaging", "background", "unknown"]
Role = Literal["history", "cognition", "laboratory", "imaging"]
ROLES = ("history", "cognition", "laboratory", "imaging")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)
    schema_version: Literal["1"] = "1"


def is_rwe_patient_json(raw):
    return isinstance(raw, dict) and (raw.get('schema_version') == '2.0' or
                                     ('patient' in raw and 'forms' in raw))


class PatientInput(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    patient_id: StrictStr = Field(alias="患者ID", validation_alias=AliasChoices(
        "患者ID", AliasPath("patient", "patient_number")))

    @model_validator(mode="before")
    @classmethod
    def validate_rwe_export(cls, raw):
        if not is_rwe_patient_json(raw):
            return raw
        patient = raw.get('patient')
        if not isinstance(patient, dict) or not isinstance(patient.get('patient_number'), str):
            raise ValueError('RWE JSON requires patient.patient_number as a string')
        if not patient['patient_number'].strip():
            raise ValueError('RWE patient_number must not be blank')
        if '患者ID' in raw and raw['患者ID'] != patient['patient_number']:
            raise ValueError('patient_identity_conflict')
        forms = raw.get('forms')
        if not isinstance(forms, dict):
            raise ValueError('RWE JSON forms must be an object')
        for key, form in forms.items():
            if not isinstance(form, dict) or not isinstance(form.get('records'), list):
                raise ValueError(f'RWE form {key!r} requires a records list')
            for row in form['records']:
                if not isinstance(row, dict):
                    raise ValueError(f'RWE form {key!r} contains a non-object record')
                for identity_key in ('patient_id', 'patientId'):
                    if identity_key in row and ('patient_id' not in patient or
                                               str(row[identity_key]) != str(patient['patient_id'])):
                        raise ValueError('patient_identity_conflict')
        for key in ('project', 'demographics', 'imaging', 'data_quality'):
            if raw.get(key) is not None and not isinstance(raw[key], dict):
                raise ValueError(f'RWE JSON {key} must be an object')
        for image in (raw.get('imaging') or {}).values():
            if not isinstance(image, dict):
                raise ValueError('RWE imaging entries must be objects')
        for key in ('warnings', 'missing_forms'):
            value = (raw.get('data_quality') or {}).get(key, [])
            if not isinstance(value, list):
                raise ValueError(f'RWE data_quality.{key} must be a list')
        return raw

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
    patient_metadata: dict[str, JsonValue] = Field(default_factory=dict)
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
    patient_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    records: list[SourceRecord]
    observations: list[Observation] = Field(default_factory=list)
    images: list[ImageAsset] = Field(default_factory=list)
    inventory: list[InventoryItem] = Field(default_factory=list)
    processing: dict[str, str] = Field(default_factory=dict)
    timeline: list[str] = Field(default_factory=list)
    conflict_groups: list[list[str]] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class TaskBudget(Contract):
    max_llm_calls: int = Field(default=3, ge=0, le=64)
    max_retrieval_calls: int = Field(default=2, ge=0)
    timeout_seconds: float = Field(default=120, gt=0, le=3600)


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


class ClaimContent(Contract):
    """Model-authored content; identity is injected by the caller."""
    text: str
    diagnosis_label: str | None = None  # Only an explicit diagnostic interpretation; must occur in text.
    kind: Literal["description", "interpretation", "model_classification"]
    level: str
    observation_ids: list[str]
    evidence_ids: list[str] = Field(default_factory=list)
    tool_result_ids: list[str] = Field(default_factory=list)
    strength: Literal["descriptive", "tentative", "conditional", "supported"]
    limitations: list[str] = Field(default_factory=list)
    rationale: str = ''
    applicability: str = ''


class Claim(ClaimContent):
    claim_id: str


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
    coverage: dict[str, JsonValue] = Field(default_factory=dict)
    material_hash: str | None = None
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
    requests: list["RequestContent"] = Field(default_factory=list)


class RequestContent(Contract):
    """Model request content; the main runtime owns request/action IDs."""
    type: Literal["patient_data", "knowledge", "source_context", "imaging_tool"]
    question: str = Field(min_length=1, max_length=1800)
    requested_fields: list[str] = Field(default_factory=list, max_length=32)
    source_refs: list[str] = Field(default_factory=list, max_length=2000)
    form_key: str | None = None
    cursor: str | None = None
    page_size: int | None = Field(default=None, ge=1, le=100)
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode='after')
    def valid_date_range(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('invalid_date_range')
        return self
    purpose: Literal["diagnostic_assessment", "cognitive_assessment", "imaging_interpretation", "biomarker_interpretation", "differential_diagnosis", "follow_up"] = "diagnostic_assessment"
    reason: str = Field(min_length=1, max_length=1000)
    priority: Literal["high", "normal", "low"] = "normal"


class DataFetchResult(Contract):
    request_id: str
    patient_id: str
    status: Literal["found", "not_recorded", "not_performed", "source_unavailable", "unsupported"]
    records: list[SourceRecord] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    field_status: dict[str, str] = Field(default_factory=dict)
    source: Literal["local", "rwe"] = "local"
    error: str | None = None
    source_path: str | None = None
    source_checksum: str | None = None
    next_cursor: str | None = None
    total_count: int | None = None


AgentResult.model_rebuild()


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
    purpose: Literal["diagnostic_assessment", "cognitive_assessment", "imaging_interpretation", "biomarker_interpretation", "differential_diagnosis", "follow_up"] = "diagnostic_assessment"
    reference_time: datetime | None = None


class EvidenceItem(Contract):
    evidence_id: str
    graph_triples: list[list[str]] = Field(default_factory=list)
    explanation_zh: str | None = None
    source_language: Literal["zh", "en", "mixed", "und"] = "und"
    statement_id: str | None = None
    condition_assessment: list[dict[str, JsonValue]] = Field(default_factory=list)
    document_id: str
    span_id: str
    clause_ids: list[str] = Field(default_factory=list)
    title: str | None = None
    quote: str
    context: str | None = None
    page: int | None = None
    page_kind: Literal["physical", "unavailable"] | None = None
    source_locator: str | None = None
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


class ClaimDependency(Contract):
    task_id: str
    result_id: str
    result_version: int
    claim_id: str


class DiagnosticStep(Contract):
    topic: str
    finding: str
    interpretation: str
    observation_ids: list[str] = Field(min_length=1, max_length=16)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class DiagnosticAssessment(Contract):
    primary_label: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    steps: list[DiagnosticStep] = Field(min_length=2, max_length=5)
    # A working assessment never upgrades a background citation to established diagnostic evidence.
    status: Literal['working_hypothesis'] = 'working_hypothesis'
    verified: bool = False


DiagnosisStatus = Literal['working_diagnosis', 'conditional_diagnosis', 'indeterminate', 'technical_failure']


class DiagnosisIssue(Contract):
    quote: str
    kind: Literal['patient_fact', 'medical_rule', 'applicability', 'wording']
    basis_ids: list[str]
    reason: str
    severity: Literal['minor', 'major', 'critical']


class DiagnosisLayer(Contract):
    level: Literal['syndrome', 'etiology']
    status: DiagnosisStatus
    label: str | None = None
    research_class: Literal['CN', 'MCI', 'DEMENTIA', 'AD', 'OTHER', 'UNRESOLVED'] | None = None
    reasoning: str = ''
    observation_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    missing_conditions: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    review_status: Literal['passed', 'rejected', 'unavailable', 'not_required'] = 'not_required'
    review_issues: list[DiagnosisIssue] = Field(default_factory=list)
    technical_errors: list[str] = Field(default_factory=list)


class DiagnosticOutcome(Contract):
    version: str = 'layered_diagnosis_v2'
    status: DiagnosisStatus
    assessment_time: str | None = None
    primary_working_label: str | None = None
    layers: list[DiagnosisLayer]
    conclusion: str
    evaluation_class: Literal['CN', 'MCI', 'AD'] | None = None
    evaluation_class_mapping_reason: str = ''
    task_mode: Literal['follow_up', 'blinded_classification'] = 'follow_up'
    input_scope: str = ''
    selection_manifest: dict[str, JsonValue] = Field(default_factory=dict)


class FinalReport(Contract):
    diagnostic_outcome: DiagnosticOutcome | None = None
    diagnostic_assessment: DiagnosticAssessment | None = None
    report_template_version: str = ''  # Empty for historical reports.
    report_id: str | None = None  # Historical reports remain readable; new reports are numbered by code.
    natural_language_report: str = ''
    narrative_method: str = ''
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
