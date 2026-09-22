"""Minimal retrieval evaluation over JSONL annotations."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from neurogra.knowledge.retrieval.service import search
from neurogra.knowledge.schemas.common import SchemaModel
from neurogra.knowledge.storage.sqlite import Repository


class EvaluationResult(SchemaModel):
    evaluated: int = 0
    not_evaluated: int = 0
    recall_at_k: float | None = None
    errors: list[str] = Field(default_factory=list)


def evaluate_retrieval(repository: Repository, dataset_path: Path, top_k: int = 5, release_id: str | None = None) -> EvaluationResult:
    hits = 0
    total = 0
    not_evaluated = 0
    errors: list[str] = []
    for line_no, line in enumerate(dataset_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        query = payload.get("query")
        relevant_span_ids = set(payload.get("relevant_span_ids", []))
        if not query or not relevant_span_ids:
            not_evaluated += 1
            errors.append(f"line_{line_no}:missing_query_or_relevant_span_ids")
            continue
        response = search(repository, query, top_k=top_k, release_id=release_id)
        returned_span_ids = {citation.span_id for hit in response.hits for citation in hit.citations}
        hits += int(bool(returned_span_ids & relevant_span_ids))
        total += 1
    return EvaluationResult(
        evaluated=total,
        not_evaluated=not_evaluated,
        recall_at_k=(hits / total if total else None),
        errors=errors,
    )
