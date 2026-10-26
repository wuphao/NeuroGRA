"""A predicted edge only becomes evidence through an extracted full statement."""

from ..construction.sources import validate_ref, condition_leaves


def verify_candidate(candidate, statements, blocks):
    accepted = []
    for statement in statements:
        if statement.status != "approved":
            continue
        if (statement.subject.entity_id, statement.predicate, statement.object.entity_id) != (
                candidate["h"], candidate["r"], candidate["t"]):
            continue
        refs = [*statement.source_refs, *[ref for leaf in condition_leaves(statement.condition) for ref in leaf.source_refs],
                *[ref for values in statement.field_evidence.values() for ref in values]]
        for ref in refs:
            validate_ref(ref, blocks)
        accepted.append(statement)
    return accepted
