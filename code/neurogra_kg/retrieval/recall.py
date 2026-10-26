"""Independent ranked channels and RRF merging at statement identity."""

from pathlib import Path

from ..storage.indexes import LocalVectorStore, lexical_search
from ..utils import mentions_term
from ..terminology import entity_names, expand_query


def recall(manifest, resources, statements, request, config, embedding, graph, budget):
    channels, warnings, statuses = {}, [], {}
    entities = [entity for s in statements for entity in [s.subject, s.object]]
    query = expand_query(request.question + " " + " ".join(fact.field + " " + str(fact.value) for fact in request.facts), entities)
    for name in dict.fromkeys(request.backends):
        try:
            budget.remaining()
            if name == "lexical":
                channels[name] = [id for id, _ in lexical_search(resources, query, config.lexical_top_k)]
            elif name == "vector":
                index = LocalVectorStore(Path(manifest["directory"]), embedding, budget)
                index.validate_binding(resources, manifest["release_id"])
                channels[name] = [id for id, _ in index.query(query, config.vector_top_k)]
            else:
                terms = [str(fact.value) for fact in request.facts if isinstance(fact.value, str)]
                terms.extend([entity.name for statement in statements for entity in [statement.subject, statement.object]
                              if any(mentions_term(query, name) for name in entity_names(entity))])
                channels[name] = ["statement:" + id for id in graph.neighbors(manifest["release_id"],
                    sorted(set(terms)) or [request.question], config.graph_max_hops, config.graph_top_k)]
            statuses[name] = "ok" if channels[name] else "empty"
        except Exception as exc:
            if not config.allow_explicit_degradation:
                raise
            warnings.append(name + ":" + type(exc).__name__)
            statuses[name] = "failed"
    lookup = {row["id"]: row for row in resources}
    scores, matched = {}, {}
    for channel, resource_ids in channels.items():
        unique = []
        for resource_id in resource_ids:
            for statement_id in lookup.get(resource_id, {}).get("statement_ids", []):
                if statement_id not in unique:
                    unique.append(statement_id)
        for rank, statement_id in enumerate(unique, start=1):
            scores[statement_id] = scores.get(statement_id, 0) + 1 / (config.rrf_k + rank)
            matched.setdefault(statement_id, []).append(channel)
    return scores, matched, warnings, statuses
