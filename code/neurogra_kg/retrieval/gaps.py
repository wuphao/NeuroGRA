"""Turn unknown conditions into explicit patient or knowledge tasks."""

from ..contracts.domain import DataRequest, Gap
from ..utils import identity, mentions_term
from ..terminology import entity_names


def identify_gaps(groups, question, graph_statements=None):
    gaps, data_requests = [], []
    for item in [*groups["SATISFIED"], *groups["UNKNOWN"]]:
        if item.statement.status == "source_checked" and "semantic_verification_partial" in item.statement.issues:
            gaps.append(Gap(gap_id=identity("gap", [item.statement.statement_id, "source"]), kind="source_evidence",
                statement_id=item.statement.statement_id, missing_items=["complete_source_support"],
                reason="statement_has_only_partial_semantic_source_check"))
    for item in groups["UNKNOWN"]:
        statement = item.statement
        if statement.condition_status == "unresolved":
            gaps.append(Gap(gap_id=identity("gap", [statement.statement_id, "condition"]), kind="knowledge_condition",
                            statement_id=statement.statement_id, missing_items=["complete_conditions"],
                            reason="statement_condition_scope_unresolved"))
        for detail in item.condition_results:
            if detail.state != "UNKNOWN":
                continue
            kind = "patient_data" if detail.missing_fields or detail.conflicts else "knowledge_condition"
            gap = Gap(gap_id=identity("gap", [statement.statement_id, detail.condition_id, kind]), kind=kind,
                      statement_id=statement.statement_id,
                      missing_items=detail.missing_fields or detail.conflicts or [detail.condition_id],
                      reason=detail.reason, status="requires_patient_data" if kind == "patient_data" else "open")
            gaps.append(gap)
            if kind == "patient_data":
                data_requests.append(DataRequest(gap_id=gap.gap_id, fields=gap.missing_items, reason=gap.reason))
    if not groups["SATISFIED"] and not groups["UNKNOWN"]:
        gaps.append(Gap(gap_id=identity("gap", [question, "connection"]), kind="graph_connection",
                        missing_items=[question], reason="no_candidate_supporting_evidence"))
    if graph_statements is not None:
        from itertools import combinations
        entities = {e.entity_id: e for s in graph_statements for e in [s.subject, s.object]}
        mentioned = sorted(id for id, e in entities.items()
            if any(mentions_term(question, name) for name in entity_names(e)))
        connected = {frozenset([s.subject.entity_id, s.object.entity_id]) for s in graph_statements}
        for left, right in combinations(mentioned, 2):
            if frozenset([left, right]) not in connected:
                gaps.append(Gap(gap_id=identity("gap", [question, left, right, "connection"]),
                    kind="graph_connection", missing_items=[entities[left].name, entities[right].name],
                    reason="queried_entities_have_no_source_checked_direct_connection"))
    return gaps, data_requests
