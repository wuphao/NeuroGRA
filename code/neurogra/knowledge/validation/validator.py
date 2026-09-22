"""Validation for normalized clause revisions before review and release."""

from __future__ import annotations

import re

from pydantic import Field

from neurogra.knowledge.schemas.common import SchemaModel
from neurogra.knowledge.schemas.graph import ClauseRevision, ValidationIssue
from neurogra.knowledge.schemas.source import SourceSpan
from neurogra.knowledge.storage.sqlite import Repository
from neurogra.knowledge.utils import stable_hash


class ValidationReport(SchemaModel):
    revision_id: str
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)


def validate_clause(revision: ClauseRevision, repository: Repository) -> ValidationReport:
    issues: list[ValidationIssue] = []
    if not repository.entity_exists(revision.subject_entity_id):
        issues.append(_issue("missing_subject_entity", "error", revision.revision_id, "subject_entity_id"))
    if not repository.entity_exists(revision.object_entity_id):
        issues.append(_issue("missing_object_entity", "error", revision.revision_id, "object_entity_id"))
    if revision.subject_entity_id == revision.object_entity_id:
        issues.append(_issue("self_relation", "warning", revision.revision_id, "object_entity_id"))

    for ordinal, evidence in enumerate(revision.evidence_refs):
        span = repository.get_span(evidence.span_id)
        if span is None:
            issues.append(_issue("missing_evidence_span", "error", revision.revision_id, f"evidence_refs[{ordinal}]"))
            continue
        if not _quote_matches_span(evidence.quote, span):
            issues.append(_issue("evidence_quote_not_found", "error", revision.revision_id, f"evidence_refs[{ordinal}]", [evidence]))

    subject_type = repository.entity_type(revision.subject_entity_id)
    object_type = repository.entity_type(revision.object_entity_id)
    if revision.predicate == "distinguishes" and {subject_type, object_type} != {"disease"}:
        issues.append(_issue("invalid_distinguishes_type", "error", revision.revision_id, "predicate"))

    claim_group_id = stable_hash(
        {
            "subject": revision.subject_entity_id,
            "predicate": revision.predicate,
            "object": revision.object_entity_id,
            "condition": revision.condition,
            "exceptions": revision.exceptions,
        },
        "claimgroup",
    )
    updated = revision.model_copy(update={"validation_issues": issues, "claim_group_id": claim_group_id})
    repository.update_clause_revision(updated)
    return ValidationReport(revision_id=revision.revision_id, issues=issues)


def validate_revisions(revisions: list[ClauseRevision], repository: Repository) -> list[ValidationReport]:
    return [validate_clause(revision, repository) for revision in revisions]


def _quote_matches_span(quote: str, span: SourceSpan) -> bool:
    if quote in span.original_text or quote in span.normalized_text:
        return True
    normalized_quote = _compact_text(quote)
    return normalized_quote in _compact_text(span.original_text) or normalized_quote in _compact_text(span.normalized_text)


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _issue(
    code: str,
    severity: str,
    object_id: str,
    field_path: str | None = None,
    evidence_refs: list | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        object_id=object_id,
        field_path=field_path,
        message=code.replace("_", " "),
        evidence_refs=evidence_refs or [],
    )
