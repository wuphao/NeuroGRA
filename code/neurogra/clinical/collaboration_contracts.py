"""Versioned, scoped contracts for patient collaboration workflows.

These contracts describe proposed work and evidence links.  Possession of a
subject or source reference never grants permission to use it; the caller must
re-authorize and validate references against its current patient workspace.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import Claim, RequestContent, TaskBudget


class CollaborationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)
    schema_version: Literal["2"] = "2"


class SubjectScope(CollaborationContract):
    """Recorded scope, not an authorization credential."""

    source_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    primary_patient: str = Field(min_length=1, max_length=200)
    comparison_patients: list[str] = Field(default_factory=list, max_length=5)
    cohort_binding: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def distinct_patients(self):
        if (self.primary_patient in self.comparison_patients or
                len(set(self.comparison_patients)) != len(self.comparison_patients) or
                any(not item.strip() for item in self.comparison_patients)):
            raise ValueError("invalid_comparison_scope")
        if not self.source_id.strip() or not self.project_id.strip() or not self.primary_patient.strip():
            raise ValueError("invalid_subject_scope")
        return self

    @property
    def patient_keys(self) -> frozenset[str]:
        return frozenset([self.primary_patient, *self.comparison_patients])


class TaskDependency(CollaborationContract):
    task_id: str = Field(min_length=1, max_length=120)
    task_version: int = Field(default=1, ge=1)
    allow_partial: bool = False

    @property
    def key(self) -> tuple[str, int]:
        return self.task_id, self.task_version


TaskKind = Literal[
    "patient.read", "knowledge.search", "specialist.analyze", "evidence.review",
    "answer.synthesize", "dialogue.delegate", "scope.clarify", "comparison.run",
    "cohort.aggregate", "report.request", "imaging.predict",
]
TaskStatus = Literal[
    "pending", "ready", "running", "succeeded", "partial", "failed", "skipped",
    "cancelled", "stale", "interrupted",
]


class CollaborationTask(CollaborationContract):
    """One executable task, rather than one role in a fixed team."""

    task_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    task_version: int = Field(default=1, ge=1)
    kind: TaskKind
    agent_type: str | None = Field(default=None, min_length=1, max_length=80,
                                   pattern=r"^[A-Za-z0-9_-]+$")
    objective: str = Field(min_length=1, max_length=1800)
    case_version: int | None = Field(default=None, ge=1)
    patient_keys: list[str] = Field(default_factory=list, max_length=6)
    input_refs: list[str] = Field(default_factory=list, max_length=2000)
    form_keys: list[str] = Field(default_factory=list, max_length=64)
    depends_on: list[TaskDependency] = Field(default_factory=list, max_length=64)
    budget: TaskBudget = Field(default_factory=TaskBudget)

    @model_validator(mode="after")
    def valid_task(self):
        if not self.objective.strip():
            raise ValueError("empty_task_objective")
        if self.kind == "specialist.analyze" and self.agent_type is None:
            raise ValueError("specialist_type_required")
        if len(set(self.patient_keys)) != len(self.patient_keys) or any(
                not key.strip() for key in self.patient_keys):
            raise ValueError("duplicate_or_empty_patient")
        if len(set(self.form_keys)) != len(self.form_keys) or any(
                not key.strip() for key in self.form_keys):
            raise ValueError("duplicate_or_empty_form")
        if len(set(self.input_refs)) != len(self.input_refs) or any(
                not ref.strip() for ref in self.input_refs):
            raise ValueError("duplicate_or_empty_input_ref")
        dependencies = [item.key for item in self.depends_on]
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("duplicate_task_dependency")
        return self

    @property
    def key(self) -> tuple[str, int]:
        return self.task_id, self.task_version


class CollaborationGraph(CollaborationContract):
    graph_version: int = Field(default=1, ge=1)
    tasks: list[CollaborationTask] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def acyclic_and_closed(self):
        nodes = {task.key: task for task in self.tasks}
        if len(nodes) != len(self.tasks):
            raise ValueError("duplicate_task_version")
        pending = set(nodes)
        completed: set[tuple[str, int]] = set()
        while pending:
            ready = {key for key in pending if all(dep.key in completed
                                                    for dep in nodes[key].depends_on)}
            if not ready:
                raise ValueError("cyclic_or_unknown_task_dependency")
            pending.difference_update(ready)
            completed.update(ready)
        return self


class ClaimEvidenceLink(CollaborationContract):
    """A proposed relation; semantic support must be checked separately."""

    kind: Literal["patient", "knowledge", "user_hypothesis"]
    ref_id: str = Field(min_length=1, max_length=200)
    relation: Literal["supports", "contradicts", "background", "unknown"]
    patient_key: str | None = Field(default=None, min_length=1, max_length=200)
    applicability: Literal["satisfied", "unknown", "violated", "not_evaluated"] = "not_evaluated"

    @model_validator(mode="after")
    def supported_condition(self):
        if (self.kind == "knowledge" and self.relation == "supports" and
                self.applicability == "violated"):
            raise ValueError("inapplicable_evidence_cannot_support_claim")
        return self


class CollaborationClaim(CollaborationContract):
    task_id: str = Field(min_length=1, max_length=120)
    task_version: int = Field(default=1, ge=1)
    patient_keys: list[str] = Field(default_factory=list, max_length=6)
    claim: Claim
    links: list[ClaimEvidenceLink] = Field(default_factory=list, max_length=128)
    review_status: Literal["not_reviewed", "passed", "conditional", "rejected", "unavailable"] = "not_reviewed"

    @model_validator(mode="after")
    def links_match_claim(self):
        if len(set(self.patient_keys)) != len(self.patient_keys):
            raise ValueError("duplicate_claim_patient")
        for link in self.links:
            if link.patient_key is not None and link.patient_key not in self.patient_keys:
                raise ValueError("claim_link_outside_patient_scope")
            if link.kind == "patient" and link.ref_id not in self.claim.observation_ids:
                raise ValueError("claim_patient_link_not_referenced")
            if link.kind == "knowledge" and link.ref_id not in self.claim.evidence_ids:
                raise ValueError("claim_knowledge_link_not_referenced")
        return self


class ReviewIssue(CollaborationContract):
    claim_id: str = Field(min_length=1, max_length=200)
    kind: Literal["patient_fact", "medical_rule", "applicability", "logic", "missing", "wording"]
    severity: Literal["minor", "major", "critical"]
    reason: str = Field(min_length=1, max_length=1000)
    basis_ids: list[str] = Field(default_factory=list, max_length=32)
    request: RequestContent | None = None


def validate_task_scope(graph: CollaborationGraph, scope: SubjectScope, *, form_keys: set[str],
                        agent_types: set[str]) -> list[str]:
    """Check declared capabilities; callers must also check live permissions and refs."""

    errors: set[str] = set()
    for task in graph.tasks:
        if not set(task.patient_keys) <= scope.patient_keys:
            errors.add("patient_outside_scope")
        if task.kind in {"patient.read", "specialist.analyze", "comparison.run", "report.request", "imaging.predict"} and not task.patient_keys:
            errors.add("patient_scope_required")
        if task.kind == "comparison.run" and len(task.patient_keys) < 2:
            errors.add("comparison_requires_selected_patients")
        if task.kind == "report.request" and task.patient_keys != [scope.primary_patient]:
            errors.add("report_requires_primary_patient")
        if task.kind == "dialogue.delegate" and task.patient_keys != [scope.primary_patient]:
            errors.add("dialogue_requires_primary_patient")
        if not set(task.form_keys) <= form_keys:
            errors.add("form_outside_scope")
        if task.kind == "specialist.analyze" and task.agent_type not in agent_types:
            errors.add("unregistered_agent")
        if task.kind == "cohort.aggregate" and scope.cohort_binding is None:
            errors.add("cohort_scope_required")
    return sorted(errors)
