"""Release-bound retrieval evaluation. Provisional labels never imply clinical validation."""
import json
from pathlib import Path
from .schemas import RetrievalRequest
from .retrieval import KnowledgeService, pin_release, readonly_repository, knowledge_settings
from .service import create_context
from .utils import write_json


def retrieval_metrics(retrieved, relevant, k):
    relevant = set(relevant)
    if not relevant:
        raise ValueError('unlabelled_query')
    found = set(retrieved[:k]) & relevant
    return {'hit_at_k': float(bool(found)), 'recall_at_k': len(found) / len(relevant)}


def evaluate_retrieval(config, query_path, k=5):
    if k < 1 or k > 50:
        raise ValueError('invalid_k')
    dataset = json.loads(Path(query_path).read_text(encoding='utf-8-sig'))
    release = pin_release(config)
    if dataset['release_id'] != release:
        raise ValueError('evaluation_release_mismatch')
    queries = dataset['queries']
    if not queries or len({q['id'] for q in queries}) != len(queries):
        raise ValueError('empty_or_duplicate_queries')
    if {q['split'] for q in queries} != {'dev', 'heldout'}:
        raise ValueError('requires_dev_and_heldout')
    from .vector import release_chunks
    _, db = knowledge_settings(config)
    with readonly_repository(db) as repository:
        allowed = {s for chunk in release_chunks(repository, release) for s in chunk.span_ids}
        for query in queries:
            if not query['relevant_span_ids'] or not set(query['relevant_span_ids']) <= allowed:
                raise ValueError('invalid_relevance_labels')
    store, run_id = create_context(config)
    service = KnowledgeService(config, release, store, run_id)
    rows = []
    modes = {'bm25': ['bm25'], 'vector': ['vector'], 'hybrid': ['bm25', 'vector', 'graph']}
    try:
        for q in queries:
            for mode, backends in modes.items():
                bundle = service.search_knowledge(RetrievalRequest(request_id=q['id'] + '_' + mode,
                    question=q['query'], candidate_terms=q.get('candidate_terms', []), top_k=k,
                    requested_backends=backends, knowledge_release_id=release))
                spans = [e.span_id for e in bundle.items]
                rows.append({'query_id': q['id'], 'split': q['split'], 'mode': mode,
                    **retrieval_metrics(spans, q['relevant_span_ids'], k), 'status': bundle.status,
                    'backend_status': bundle.backend_status, 'retrieved_span_ids': spans,
                    'bundle_id': bundle.bundle_id, 'retrieval_metadata': bundle.retrieval_metadata})
        aggregate = []
        for split in ('dev', 'heldout'):
            for mode in modes:
                subset = [r for r in rows if r['split'] == split and r['mode'] == mode]
                aggregate.append({'split': split, 'mode': mode, 'queries': len(subset),
                    'hit_at_k': sum(r['hit_at_k'] for r in subset) / len(subset),
                    'recall_at_k': sum(r['recall_at_k'] for r in subset) / len(subset),
                    'failed_queries': sum(r['status'] == 'failed' for r in subset)})
        result = {'run_id': run_id, 'release_id': release, 'k': k, 'dataset_id': dataset['dataset_id'],
            'annotation_status': dataset['annotation_status'], 'aggregate': aggregate, 'queries': rows,
            'limitations': ['标签为来源锚定的工程小样本，未经临床专家独立审定；Recall仅针对已标注相关集合，不能声称真实检索质量或临床效果提升。']}
        path = config.resolve(config.output_root) / run_id / 'retrieval_evaluation.json'
        write_json(path, result)
        store.save(run_id, 'retrieval_evaluation', 'main', result)
        store.status(run_id, 'completed')
        return path, result
    except Exception:
        store.status(run_id, 'failed')
        raise
