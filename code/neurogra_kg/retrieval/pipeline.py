"""Read-only retrieval against a pinned immutable release."""

from ..contracts.domain import RetrievalResult
from ..storage.releases import load_release
from ..utils import identity
from .gaps import identify_gaps
from .ranking import rank_evidence
from .recall import recall


def retrieve(manifest, request, config, embedding, graph, llm, budget):
    statements, blocks, resources, data = load_release(manifest)
    # In-memory graph is an explicit developer adapter and requires replay per process.
    if type(graph).__name__ == "MemoryGraphStore":
        graph.publish(manifest["release_id"], statements, blocks)
    scores, matched, warnings, statuses = recall(manifest, resources, statements, request, config, embedding, graph, budget)
    groups = rank_evidence(statements, scores, matched, request, manifest["release_id"], llm, budget)
    for group in groups.values():
        for item in group:
            annotation = item.statement.zh_annotation
            if annotation and annotation.status != "verified":
                warnings.append("chinese_expression_unavailable:" + item.statement.statement_id)
    gaps, data_requests = identify_gaps(groups, request.question, statements)
    return RetrievalResult(run_id=request.run_id or identity("query", [request.model_dump(mode="json"), manifest["release_id"]]),
        release_id=manifest["release_id"], applicable=groups["SATISFIED"], unknown=groups["UNKNOWN"],
        excluded=groups["VIOLATED"], gaps=gaps, data_requests=data_requests,
        degraded=bool(warnings), warnings=warnings, backend_status=statuses)
