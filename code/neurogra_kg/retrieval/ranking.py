"""Applicability gates are not overridden by relevance scores."""

from ..contracts.domain import EvidenceItem
from ..utils import identity
from .applicability import evaluate_condition


def rank_evidence(statements, scores, matched, request, release_id, llm, budget, limit=True):
    groups = {"SATISFIED": [], "UNKNOWN": [], "VIOLATED": []}
    maximum = max(scores.values(), default=1) or 1
    for statement in statements:
        if statement.statement_id not in scores:
            continue
        if statement.condition_status == "explicit_unrestricted":
            state, details = "SATISFIED", []
        elif statement.condition_status == "unresolved":
            state, details = "UNKNOWN", []
        else:
            state, details = evaluate_condition(statement.condition, request.facts, request.reference_time, llm, budget)
        relevance = scores[statement.statement_id] / maximum
        purpose = float(request.purpose in statement.purposes)
        condition_score = {"SATISFIED": 1.0, "UNKNOWN": .5, "VIOLATED": 0.0}[state]
        components = {"relevance": relevance, "purpose": purpose, "condition": condition_score}
        groups[state].append(EvidenceItem(evidence_id=identity("evidence", [release_id, statement.statement_id,
                                                                       request.model_dump(mode="json")]),
            statement=statement, state=state, condition_results=details,
            matched_by=matched[statement.statement_id], score=.5 * relevance + .2 * purpose + .3 * condition_score,
            score_components=components, release_id=release_id))
    for state in groups:
        groups[state].sort(key=lambda item: (-item.score, item.statement.statement_id))
        if limit:
            groups[state] = groups[state][:request.top_k]
    return groups
