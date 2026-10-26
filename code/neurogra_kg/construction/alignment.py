"""Candidate scopes, score fusion and explicit constraint selection."""

from dataclasses import dataclass
from ..contracts.tasks import AlignmentOutput
from .sources import resolve_quotes


def align_candidate_pairs(llm, pairs, payload, blocks, budget, batch_size=8):
    """Bound the output size while preserving every pair and its full context."""
    decisions = []
    for start in range(0, len(pairs), batch_size):
        batch = pairs[start:start + batch_size]
        output = llm.execute("L2", {**payload, "candidate_pairs": [pair.payload() for pair in batch]},
                             AlignmentOutput, budget)
        expected = {(pair.statement_id, pair.condition_id) for pair in batch}
        actual = [(item.statement_id, item.condition_id) for item in output.decisions]
        if len(actual) != len(expected) or set(actual) != expected:
            raise ValueError("alignment_decision_pair_mismatch")
        for decision in output.decisions:
            resolve_quotes(decision.evidence_refs, blocks)
        decisions.extend(output.decisions)
    return AlignmentOutput(decisions=decisions)


@dataclass
class CandidatePair:
    statement_id: str
    condition_id: str
    structure_score: float
    entity_score: float
    fixed_reference: bool = False
    exclusive_group: str | None = None

    def payload(self):
        return {"statement_id": self.statement_id, "condition_id": self.condition_id,
                "structure_score": self.structure_score, "fixed_reference": self.fixed_reference}


def candidate_pairs(statements, conditions, blocks, edges):
    explicit = {(edge.source, edge.target) for edge in edges
                if edge.relation == "REFERS_TO" and edge.certainty == "explicit"}
    continued = {(edge.source, edge.target) for edge in edges if edge.relation == "CONTINUES"}
    scoped_notes = {(edge.source, edge.target): edge.certainty for edge in edges
                    if edge.relation in {"NOTE_OF", "REFERS_TO"}}
    scoped_notes.update({(edge.target, edge.source): edge.certainty for edge in edges if edge.relation == "HEADER_OF"})
    result = []
    for statement in statements:
        statement_blocks = {ref.block_id for ref in statement.source_refs}
        for condition in conditions:
            condition_blocks = {ref.block_id for ref in condition.source_refs}
            score = 0.0
            fixed = False
            for sb in statement_blocks:
                for cb in condition_blocks:
                    a, b = blocks[sb], blocks[cb]
                    if sb == cb:
                        if a.kind == "table" and a.cells:
                            from .table_chunks import referenced_rows
                            statement_rows, _ = referenced_rows(a, [ref for ref in statement.source_refs if ref.block_id == sb])
                            condition_rows, header_rows = referenced_rows(b, [ref for ref in condition.source_refs if ref.block_id == cb])
                            if statement_rows and condition_rows and not statement_rows & condition_rows and not header_rows:
                                continue
                        score = max(score, .95)
                    elif (sb, cb) in explicit:
                        fixed = True
                        score = 1.0
                    elif (sb, cb) in scoped_notes:
                        score = max(score, .9 if scoped_notes[(sb, cb)] == "explicit" else .7)
                    elif b.kind == "footnote":
                        # A local footnote with explicit owners cannot bind unrelated blocks.
                        owners = {source for source, target in explicit if target == cb}
                        if owners and sb not in owners:
                            continue
                        if a.page == b.page and not owners:
                            score = max(score, .4)
                    elif a.table_id and a.table_id == b.table_id:
                        score = max(score, .9)
                    elif a.kind == "table" and b.table_id == sb:
                        score = max(score, .9)
                    elif cb in a.section_path:
                        score = max(score, .85)
                    elif a.section_path and a.section_path == b.section_path and abs(a.page - b.page) <= 1:
                        score = max(score, .65)
                    elif (sb, cb) in continued or (cb, sb) in continued:
                        score = max(score, .55)
                    elif a.page == b.page and abs(a.order - b.order) <= 2:
                        score = max(score, .45)
            if score:
                terms = [statement.subject_text, statement.object_text]
                entity_score = sum(term.casefold() in condition.condition_text.casefold() for term in terms) / 2
                result.append(CandidatePair(statement.local_id, condition.local_id, score, entity_score, fixed))
    return result


def select_bindings(pairs, decisions, weights, threshold):
    lookup = {(decision.statement_id, decision.condition_id): decision for decision in decisions}
    if len(lookup) != len(decisions):
        raise ValueError("duplicate_alignment_decision")
    expected = {(pair.statement_id, pair.condition_id) for pair in pairs}
    if set(lookup) != expected:
        raise ValueError("alignment_decision_pair_mismatch")
    selected, trace, exclusive = [], [], {}
    for pair in pairs:
        decision = lookup[(pair.statement_id, pair.condition_id)]
        semantic = {"direct": 1.0, "shared": .9, "unrelated": 0,
                    "contradicted": 0, "unresolved": 0}[decision.relation]
        score = weights["structure"] * pair.structure_score + weights["entity"] * pair.entity_score + weights["semantic"] * semantic
        eligible = decision.relation in {"direct", "shared"} and bool(decision.evidence_refs)
        chosen = eligible and (pair.fixed_reference or score > threshold)
        trace.append({"statement_id": pair.statement_id, "condition_id": pair.condition_id,
                      "score": score, "relation": decision.relation, "selected": chosen,
                      "fixed_reference": pair.fixed_reference, "reason": decision.reason})
        if chosen:
            if pair.exclusive_group:
                previous = exclusive.get(pair.exclusive_group)
                if previous is None or score > previous[0]:
                    exclusive[pair.exclusive_group] = (score, pair)
            else:
                selected.append(pair)
    selected.extend(pair for _, pair in exclusive.values())
    chosen_keys = {(pair.statement_id, pair.condition_id) for pair in selected}
    for item in trace:
        item["selected"] = (item["statement_id"], item["condition_id"]) in chosen_keys
    # Objective is separable once invalid scopes are removed: choose positive gains,
    # with max gain per mutually exclusive group. Shared conditions remain many-to-many.
    return selected, trace
