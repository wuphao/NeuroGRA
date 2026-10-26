"""Type-constrained, query-local unknown edge proposals."""

from ..utils import mentions_term


def predict_candidates(model, statements, query, max_edges):
    entities = {entity.entity_id: entity for statement in statements for entity in [statement.subject, statement.object]}
    known = {(statement.subject.entity_id, statement.predicate, statement.object.entity_id) for statement in statements}
    allowed_types = {(statement.subject.entity_type, statement.predicate, statement.object.entity_type) for statement in statements}
    if not set(model.entities) <= set(entities):
        raise ValueError("complex_entity_binding_mismatch")
    mentioned = {id for id, entity in entities.items() if any(mentions_term(query, name)
                 for name in [entity.name, *entity.aliases])}
    if not mentioned:
        return []
    proposals = []
    for h in model.entities:
        for t in model.entities:
            if h == t or not {h, t} & mentioned:
                continue
            for r in model.relations:
                if (h, r, t) in known or (entities[h].entity_type, r, entities[t].entity_type) not in allowed_types:
                    continue
                proposals.append({"h": h, "h_name": entities[h].name, "r": r, "t": t,
                    "t_name": entities[t].name, "score": model.score(h, r, t),
                    "model_id": model.manifest["model_id"], "release_id": model.manifest["release_id"],
                    "status": "unverified"})
    return sorted(proposals, key=lambda row: (-row["score"], row["h"], row["r"], row["t"]))[:max_edges]
