"""Human review export and structured decision import."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import Field

from neurogra.knowledge.schemas.common import SchemaModel
from neurogra.knowledge.schemas.lifecycle import ReviewDecision
from neurogra.knowledge.storage.sqlite import Repository
from neurogra.knowledge.validation.validator import validate_clause


class ReviewBatch(SchemaModel):
    batch_id: str
    chunk_build_id: str
    decisions: list[ReviewDecision]


class ReviewApplyResult(SchemaModel):
    applied_count: int = 0
    blocked_count: int = 0
    issues: list[str] = Field(default_factory=list)


def export_review_batch(repository: Repository, chunk_build_id: str, reviewer_id: str) -> ReviewBatch:
    now = datetime.now(timezone.utc)
    decisions: list[ReviewDecision] = []
    for chunk in repository.get_chunks_for_build(chunk_build_id):
        if chunk.role == "child" and chunk.text_review_status == "pending":
            decisions.append(
                ReviewDecision(
                    decision_id=f"review_chunk_{chunk.chunk_id}",
                    object_type="chunk",
                    object_id=chunk.chunk_id,
                    action="approve",
                    reviewer_id=reviewer_id,
                    reason="default export decision; edit before import if needed",
                    decided_at=now,
                )
            )
    for revision in repository.get_clause_revisions_for_chunk_build(chunk_build_id):
        if revision.review_status == "pending":
            decisions.append(
                ReviewDecision(
                    decision_id=f"review_clause_{revision.revision_id}",
                    object_type="clause_revision",
                    object_id=revision.revision_id,
                    expected_revision_id=revision.revision_id,
                    action="approve",
                    reviewer_id=reviewer_id,
                    reason="default export decision; edit before import if needed",
                    decided_at=now,
                )
            )
    return ReviewBatch(
        batch_id=f"review_{chunk_build_id}",
        chunk_build_id=chunk_build_id,
        decisions=decisions,
    )


def apply_decisions(repository: Repository, decisions: list[ReviewDecision]) -> ReviewApplyResult:
    result = ReviewApplyResult()
    for decision in decisions:
        try:
            if decision.object_type == "chunk":
                if decision.action == "approve":
                    status = "approved"
                elif decision.action == "reject":
                    status = "rejected"
                else:
                    result.blocked_count += 1
                    result.issues.append(f"{decision.object_id}:amend_not_implemented")
                    continue
                repository.update_chunk_review_status(decision.object_id, status)
                repository.save_review_decision(decision)
                result.applied_count += 1
            elif decision.object_type == "clause_revision":
                revision = repository.get_clause_revision(decision.object_id)
                if decision.expected_revision_id and decision.expected_revision_id != revision.revision_id:
                    result.blocked_count += 1
                    result.issues.append(f"{decision.object_id}:revision_mismatch")
                    continue
                if decision.action == "approve":
                    report = validate_clause(revision, repository)
                    if report.has_errors:
                        result.blocked_count += 1
                        result.issues.append(f"{decision.object_id}:validation_errors")
                        continue
                    status = "approved"
                elif decision.action == "reject":
                    status = "rejected"
                else:
                    result.blocked_count += 1
                    result.issues.append(f"{decision.object_id}:amend_not_implemented")
                    continue
                repository.update_clause_review_status(decision.object_id, status)
                repository.save_review_decision(decision)
                result.applied_count += 1
        except ValueError as exc:
            result.blocked_count += 1
            result.issues.append(str(exc))
    return result
