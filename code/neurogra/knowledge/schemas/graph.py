"""Entity, condition, and clause schemas."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, StrictBool, StrictStr, field_validator, model_validator

from neurogra.knowledge.schemas.common import SchemaModel, StrictBaseModel
from neurogra.knowledge.schemas.source import EvidenceRef


class Entity(SchemaModel):
    entity_id: str
    canonical_name: str
    entity_type: Literal["disease", "syndrome", "phenotype", "examination", "biomarker"]
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    terminology_version: str
    status: Literal["pending", "approved", "retired"] = "pending"


class EntityMention(SchemaModel):
    mention_id: str
    text: str
    predicted_type: Literal["disease", "syndrome", "phenotype", "examination", "biomarker"]
    evidence: EvidenceRef
    selected_entity_id: str | None = None
    candidate_entity_ids: list[str] = Field(default_factory=list)
    link_method: str
    link_status: Literal["resolved", "ambiguous", "local_pending"]


AtomValue: TypeAlias = StrictStr | StrictBool | Decimal | list[StrictStr | Decimal] | None


class AtomCondition(StrictBaseModel):
    kind: Literal["ATOM"] = "ATOM"
    field: str
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "exists"]
    value: AtomValue
    evidence_refs: list[EvidenceRef]
    unit: str | None = None
    method: str | None = None
    population: str | None = None
    time_window: str | None = None

    @field_validator("evidence_refs")
    @classmethod
    def evidence_refs_required(cls, value: list[EvidenceRef]) -> list[EvidenceRef]:
        if not value:
            raise ValueError("ATOM condition requires at least one evidence reference")
        return value

    @model_validator(mode="after")
    def operator_value_must_match(self) -> "AtomCondition":
        if self.operator == "exists" and not isinstance(self.value, bool):
            raise ValueError("exists operator requires a boolean value")
        if self.operator == "in" and not isinstance(self.value, list):
            raise ValueError("in operator requires a list value")
        if self.operator in {"gt", "gte", "lt", "lte"} and not isinstance(self.value, Decimal):
            raise ValueError("numeric comparison operators require Decimal value")
        return self


class AndCondition(StrictBaseModel):
    kind: Literal["AND"] = "AND"
    children: list["ConditionNode"]

    @field_validator("children")
    @classmethod
    def at_least_two_children(cls, value: list["ConditionNode"]) -> list["ConditionNode"]:
        if len(value) < 2:
            raise ValueError("AND condition requires at least two children")
        return value


class OrCondition(StrictBaseModel):
    kind: Literal["OR"] = "OR"
    children: list["ConditionNode"]

    @field_validator("children")
    @classmethod
    def at_least_two_children(cls, value: list["ConditionNode"]) -> list["ConditionNode"]:
        if len(value) < 2:
            raise ValueError("OR condition requires at least two children")
        return value


class NotCondition(StrictBaseModel):
    kind: Literal["NOT"] = "NOT"
    child: "ConditionNode"


class TextCondition(StrictBaseModel):
    kind: Literal["TEXT"] = "TEXT"
    text: str
    evidence_refs: list[EvidenceRef]
    reason: str

    @field_validator("evidence_refs")
    @classmethod
    def evidence_refs_required(cls, value: list[EvidenceRef]) -> list[EvidenceRef]:
        if not value:
            raise ValueError("TEXT condition requires at least one evidence reference")
        return value


ConditionNode = Annotated[
    AtomCondition | AndCondition | OrCondition | NotCondition | TextCondition,
    Field(discriminator="kind"),
]


class ValidationIssue(StrictBaseModel):
    code: str
    severity: Literal["error", "warning", "info"]
    object_id: str
    field_path: str | None = None
    message: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    resolver: str | None = None


class ClauseRevision(SchemaModel):
    clause_id: str
    revision_id: str
    candidate_id: str
    subject_entity_id: str
    object_entity_id: str
    predicate: Literal["supports", "weakens", "associated_with", "distinguishes", "limits"]
    direction: Literal["supporting", "weakening", "neutral", "limiting"]
    diagnostic_level: Literal[
        "cognitive_state",
        "syndrome",
        "etiology",
        "biological",
        "pathological",
        "unspecified",
    ] = "unspecified"
    modality: Literal["required", "recommended", "permitted", "prohibited", "uncertain", "unspecified"]
    condition: ConditionNode | None = None
    exceptions: list[ConditionNode] = Field(default_factory=list)
    condition_executable: bool
    evidence_refs: list[EvidenceRef]
    field_evidence: dict[str, list[EvidenceRef]] = Field(default_factory=dict)
    assertion_text: str
    review_status: Literal["pending", "approved", "rejected"] = "pending"
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    claim_group_id: str | None = None
    extraction_run_id: str

    @field_validator("evidence_refs")
    @classmethod
    def evidence_refs_must_not_be_empty(cls, value: list[EvidenceRef]) -> list[EvidenceRef]:
        if not value:
            raise ValueError("ClauseRevision.evidence_refs must not be empty")
        return value

    @model_validator(mode="after")
    def direction_must_match_predicate(self) -> "ClauseRevision":
        allowed = {
            "supports": {"supporting"},
            "weakens": {"weakening"},
            "associated_with": {"neutral"},
            "distinguishes": {"supporting", "weakening", "neutral"},
            "limits": {"limiting"},
        }
        if self.direction not in allowed[self.predicate]:
            raise ValueError("direction is incompatible with predicate")
        if self.condition_executable and self.condition is not None:
            if _contains_text_condition(self.condition) or any(
                _contains_text_condition(condition) for condition in self.exceptions
            ):
                raise ValueError("TEXT conditions are not executable")
        return self


def _contains_text_condition(condition: ConditionNode) -> bool:
    if isinstance(condition, TextCondition):
        return True
    if isinstance(condition, NotCondition):
        return _contains_text_condition(condition.child)
    if isinstance(condition, AndCondition | OrCondition):
        return any(_contains_text_condition(child) for child in condition.children)
    return False


AndCondition.model_rebuild()
OrCondition.model_rebuild()
NotCondition.model_rebuild()
