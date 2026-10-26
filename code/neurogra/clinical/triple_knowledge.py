"""Read-only ordinary (head, predicate, tail) projection of a pinned KG release.

Retrieval never sees assertion text, purposes, condition trees or patient facts.
Source excerpts remain available for the same downstream human/LLM review.
"""
from neurogra_kg.construction.sources import condition_leaves
from neurogra_kg.storage.indexes import lexical_search
from neurogra_kg.terminology import entity_names
from .schemas import EvidenceItem
from .utils import identity


def project_statements(statements):
    edges = {}
    for statement in sorted(statements, key=lambda s: s.statement_id):
        if statement.status not in {'approved', 'source_checked'}:
            continue
        key = (statement.subject.entity_id, statement.predicate, statement.object.entity_id)
        if key not in edges:
            edges[key] = {'id': identity('triple', key), 'head_id': key[0], 'predicate': key[1],
                'tail_id': key[2], 'head': statement.subject.name, 'tail': statement.object.name,
                'head_names': [], 'tail_names': [], 'statement_ids': [], 'source_refs': []}
        edge = edges[key]
        edge['head_names'] = sorted(set(edge['head_names']) | set(entity_names(statement.subject)))
        edge['tail_names'] = sorted(set(edge['tail_names']) | set(entity_names(statement.object)))
        edge['statement_ids'].append(statement.statement_id)
        # Preserve raw source provenance, without exposing condition operators/results.
        refs = [*statement.source_refs, *[ref for leaf in condition_leaves(statement.condition) for ref in leaf.source_refs]]
        for ref in refs:
            value = ref.model_dump(mode='json')
            if value not in edge['source_refs']:
                edge['source_refs'].append(value)
    return sorted(edges.values(), key=lambda e: e['id'])


def rank_triples(edges, question, backend, top_k, max_hops=2):
    resources = [{'id': e['id'], 'text': ' '.join([*e['head_names'], e['predicate'], *e['tail_names']])} for e in edges]
    ranking = lexical_search(resources, question, max(top_k * 3, 3))
    if backend == 'bm25':
        return [key for key, _ in ranking[:top_k]]
    if backend != 'graph':
        return []
    by_id = {e['id']: e for e in edges}
    seeds = [key for key, _ in ranking[:3]]
    frontier = {by_id[key][side] for key in seeds for side in ('head_id', 'tail_id')}
    found = dict.fromkeys(seeds)
    for _ in range(max_hops):
        next_frontier = set()
        for edge in edges:
            if frontier & {edge['head_id'], edge['tail_id']}:
                found[edge['id']] = None
                next_frontier.update((edge['head_id'], edge['tail_id']))
        frontier = next_frontier
    return list(found)[:top_k]


def triple_evidence(edges, ranked, blocks, documents, release_id, backend):
    by_id = {e['id']: e for e in edges}
    result = {}
    for edge_id in ranked:
        edge = by_id[edge_id]
        for ref in edge['source_refs']:
            block = blocks[ref['block_id']]
            if block.text[ref['start']:ref['end']] != ref['quote']:
                raise ValueError('triple_source_quote_mismatch')
            evidence_id = identity('triple_evidence', [release_id, edge_id, ref])
            doc = documents[block.document_id]
            result[evidence_id] = EvidenceItem(evidence_id=evidence_id,
                statement_id=edge_id, clause_ids=edge['statement_ids'],
                graph_triples=[[edge['head'], edge['predicate'], edge['tail']]],
                document_id=block.document_id, span_id=block.block_id,
                title=doc['title'], quote=ref['quote'], context=block.text,
                page=block.page if block.page_kind == 'physical' else None,
                page_kind=block.page_kind, source_locator=block.source_locator,
                source_language=doc.get('language', 'und'), version=release_id,
                matched_by=['kg_plain_triples_' + backend], review_provenance='source_verified_triple_projection',
                applicability='unknown', allowed_use='background',
                limitations=['普通三元组未执行结构化条件判断；适用性需根据患者资料与原文核查。'])
    return list(result.values())


def retrieve_triples(config, release_id, question, backend, top_k):
    if backend not in {'bm25', 'graph'}:
        return 'not_configured', [], {'representation': 'plain_triples', 'backend': backend}
    from .knowledge_adapter import service, settings
    from neurogra_kg.storage.releases import load_release
    with service(config) as kg:
        manifest = kg.get_release(release_id)
        statements, blocks, _, data = load_release(manifest)
    edges = project_statements(statements)
    ranked = rank_triples(edges, question, backend, top_k, settings(config).retrieval.graph_max_hops)
    items = triple_evidence(edges, ranked, blocks,
        {d['document_id']: d for d in data['documents']}, manifest['release_id'], backend)
    return ('ok' if items else 'empty'), items, {'representation': 'plain_triples',
        'release_id': manifest['release_id'], 'projection_hash': identity('projection', edges),
        'triple_count': len(edges), 'ranked_triples': ranked,
        'condition_evaluation': 'not_performed', 'supplementation': 'disabled', 'llm_calls_allowed': 0}
