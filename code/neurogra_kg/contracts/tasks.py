"""LLM output schemas. Source offsets are resolved by code, never by the model."""

from typing import Annotated, Literal
from pydantic import Field, model_validator

from .domain import Contract, Predicate, Purpose, State


class QuoteRef(Contract):
    block_id: str
    quote: str = Field(min_length=1)
    occurrence: int = Field(ge=0)


class StatementDraft(Contract):
    subject_text: str
    subject_type: Literal["disease", "syndrome", "phenotype", "examination", "biomarker", "other"]
    predicate: Predicate
    object_text: str
    object_type: Literal["disease", "syndrome", "phenotype", "examination", "biomarker", "other"]
    assertion_text: str
    purposes: list[Purpose]
    condition_status: Literal["explicit_unrestricted", "unresolved"]
    source_refs: list[QuoteRef] = Field(min_length=1)
    field_evidence: dict[str, list[QuoteRef]]


class ConditionDraft(Contract):
    condition_text: str
    category: Literal["population", "method", "threshold", "time", "prerequisite", "exclusion", "other"]
    scope_hint: str
    source_refs: list[QuoteRef] = Field(min_length=1)


class ExtractedStatement(StatementDraft):
    """Code-assigned candidate used downstream, never an LLM output schema."""
    local_id: str


class ExtractedCondition(ConditionDraft):
    local_id: str


class ExtractionDraft(Contract):
    statements: list[StatementDraft]
    conditions: list[ConditionDraft]
    issues: list[str]


class ExtractionOutput(Contract):
    statements: list[ExtractedStatement]
    conditions: list[ExtractedCondition]
    issues: list[str]


class AlignmentDecision(Contract):
    statement_id: str
    condition_id: str
    relation: Literal["direct", "shared", "unrelated", "contradicted", "unresolved"]
    scope: Literal["local", "section", "table", "footnote", "cross_page", "unknown"]
    evidence_refs: list[QuoteRef]
    reason: str


class AlignmentOutput(Contract):
    decisions: list[AlignmentDecision]


class LogicAtom(Contract):
    node_id: str
    kind: Literal["ATOM"]
    condition_id: str
    field: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "exists"]
    value: str | float | bool | list[str | float]
    unit: str | None
    method: str | None
    time_scope: str | None
    population: str | None
    source_refs: list[QuoteRef] = Field(min_length=1)


class LogicText(Contract):
    node_id: str
    kind: Literal["TEXT"]
    condition_id: str
    text: str
    source_refs: list[QuoteRef] = Field(min_length=1)


class LogicGroup(Contract):
    node_id: str
    kind: Literal["ALL", "ANY", "NOT"]
    child_ids: list[str]


LogicNode = Annotated[LogicAtom | LogicText | LogicGroup, Field(discriminator="kind")]


class LogicOutput(Contract):
    statement_id: str
    root_id: str | None
    nodes: list[LogicNode]
    atom_mappings: dict[str, str]
    unresolved_items: list[str]
    logic_evidence: list[QuoteRef]


class LogicAtomDraft(Contract):
    kind: Literal["ATOM"]
    condition_id: str
    field: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "exists"]
    value: str | float | bool | list[str | float]
    unit: str | None
    method: str | None
    time_scope: str | None
    population: str | None
    source_refs: list[QuoteRef] = Field(min_length=1)


class LogicTextDraft(Contract):
    kind: Literal["TEXT"]
    condition_id: str
    text: str
    source_refs: list[QuoteRef] = Field(min_length=1)


class LogicGroupDraft(Contract):
    kind: Literal["ALL", "ANY", "NOT"]
    children: list["LogicTreeDraft"] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def valid_group_size(self):
        if self.kind in {"ALL", "ANY"} and len(self.children) < 2:
            raise ValueError("ALL/ANY require at least two children; use the leaf directly for a single condition")
        if self.kind == "NOT" and len(self.children) != 1:
            raise ValueError("NOT requires exactly one child")
        return self


LogicTreeDraft = Annotated[LogicAtomDraft | LogicTextDraft | LogicGroupDraft,
                           Field(discriminator="kind")]
LogicGroupDraft.model_rebuild()


class LogicDraft(Contract):
    statement_id: str
    root: LogicTreeDraft | None
    unresolved_items: list[str]
    logic_evidence: list[QuoteRef]


class FieldCheck(Contract):
    field: Literal["subject", "relation", "object", "conditions", "logic", "purpose", "source_scope"]
    state: Literal["supported", "unsupported", "insufficient"]
    evidence_refs: list[QuoteRef]
    reason: str


class VerificationOutput(Contract):
    verdict: Literal["supported", "partial", "unsupported", "insufficient"]
    checks: list[FieldCheck]
    issues: list[str]


class ApplicabilityOutput(Contract):
    condition_id: str
    state: State
    used_fact_ids: list[str]
    missing_fields: list[str]
    conflicts: list[str]
    evidence_refs: list[QuoteRef]
    reason: str


class SearchQuery(Contract):
    text: str
    target_terms: list[str]
    document_scope: list[str]
    expected_evidence: str


class PlanningOutput(Contract):
    gap_id: str
    queries: list[SearchQuery] = Field(max_length=3)
    stop_reason: str | None


class ExtractedFact(Contract):
    field: str
    value: str | float | bool | list[str | float]
    unit: str | None
    polarity: Literal["affirmed", "negated", "uncertain"]
    observed_at: str | None
    method: str | None
    source_refs: list[QuoteRef] = Field(min_length=1)


class FactOutput(Contract):
    facts: list[ExtractedFact]
    issues: list[str]


class ProbeOutput(Contract):
    ok: bool
    source_refs: list[QuoteRef]


class LocalizationItem(Contract):
    target: str
    text_zh: str = Field(min_length=1)
    source_refs: list[QuoteRef] = Field(min_length=1)


class LocalizationOutput(Contract):
    items: list[LocalizationItem]


class LocalizationCheck(Contract):
    target: str
    equivalent: bool
    source_refs: list[QuoteRef] = Field(min_length=1)
    reason: str


class LocalizationVerification(Contract):
    checks: list[LocalizationCheck]


TASK_SCHEMAS = {
    "L0": FactOutput, "L1": ExtractionDraft, "L2": AlignmentOutput,
    "L3": LogicDraft, "L4": VerificationOutput, "L5": ApplicabilityOutput,
    "L6": PlanningOutput, "L7": LocalizationOutput, "L8": LocalizationVerification, "probe": ProbeOutput,
}
