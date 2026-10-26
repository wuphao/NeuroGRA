"""Offline integrity audit of a completed PDF-folder build; never calls a model or DB."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'code'))
from neurogra.benchmarks.medgraphrag.pipeline import load_knowledge, knowledge_graph
from neurogra.benchmarks.medgraphrag.runtime import Config, LocalModel, write_json
from neurogra.benchmarks.patient_view import digest


def audit(root):
    root = Path(root).resolve()
    summary = json.loads((root / 'summary.json').read_text(encoding='utf-8'))
    if summary['status'] != 'completed' or summary['counts']['failed']:
        raise ValueError('build_is_not_complete')
    config = Config.model_validate(summary['config'])
    docs, refs, corpus_hash = load_knowledge(root / 'knowledge_manifest.json', config.chunk_chars)
    client = LocalModel(config, root)
    client.identity = summary['models']  # Verify against the recorded build model digests, offline.
    rows, dimensions = [], set()
    aggregate = json.loads((root / 'knowledge_graph.json').read_text(encoding='utf-8'))
    aggregate_graphs = {g['gid']: g for g in aggregate['graphs']}
    if len(aggregate_graphs) != len(docs):
        raise ValueError('aggregate_graph_count_mismatch')
    for doc in docs:
        graph, _ = knowledge_graph(client, doc, config, root)
        if digest(graph) != digest(aggregate_graphs[graph['gid']]):
            raise ValueError('aggregate_graph_differs_from_document_artifact')
        expected = {chunk['ref'] for chunk in doc['chunks']}
        if set(graph['source_refs']) != expected:
            raise ValueError('missing_document_source_blocks')
        nodes = {entity['id'] for entity in graph['entities']}
        if len(nodes) != len(graph['entities']):
            raise ValueError('duplicate_entity_ids')
        for entity in graph['entities']:
            vector = entity['embedding']
            if not vector or not all(math.isfinite(v) for v in vector) or not any(vector):
                raise ValueError('invalid_embedding')
            dimensions.add(len(vector))
            if not set(entity['refs']) <= expected:
                raise ValueError('unknown_entity_reference')
        for rel in graph['relationships']:
            if rel['source'] not in nodes or rel['target'] not in nodes:
                raise ValueError('orphan_relationship')
            if not set(rel['refs']) <= expected:
                raise ValueError('unknown_relationship_reference')
        rows.append({'document': doc.get('title', doc['id']), 'document_id': doc['id'],
                     'pages': len({c['page'] for c in doc['chunks']}), 'source_blocks': len(expected),
                     'entities': len(nodes), 'relationships': len(graph['relationships'])})
    if len(dimensions) != 1:
        raise ValueError('inconsistent_embedding_dimensions')
    recorded = {'generation_calls': 0, 'embedding_calls': 0, 'transport_errors': 0, 'truncated_responses': 0,
                'prompt_tokens': 0, 'output_tokens': 0, 'embedding_tokens': 0}
    with (root / 'llm_calls.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            entry = json.loads(line)
            response = entry.get('response', {})
            if entry['endpoint'] == '/api/chat':
                if entry['request']['model'] != 'qwen3.8:27b':
                    raise ValueError('unexpected_generation_model')
                recorded['generation_calls'] += 1
                recorded['prompt_tokens'] += response.get('prompt_eval_count', 0)
                recorded['output_tokens'] += response.get('eval_count', 0)
            elif entry['endpoint'] == '/api/embed':
                if entry['request']['model'] != 'qwen3-embedding:0.6b':
                    raise ValueError('unexpected_embedding_model')
                recorded['embedding_calls'] += 1
                recorded['embedding_tokens'] += response.get('prompt_eval_count', 0)
            recorded['transport_errors'] += int(bool(entry.get('error')))
            recorded['truncated_responses'] += int(response.get('done_reason') == 'length')
    result = {'status': 'passed', 'documents': rows, 'document_count': len(rows),
              'pages': sum(r['pages'] for r in rows), 'source_blocks': len(refs),
              'entity_nodes': sum(r['entities'] for r in rows),
              'relationships': sum(r['relationships'] for r in rows),
              'count_scope': 'sum_of_document_subgraphs_no_cross_document_deduplication',
              'embedding_dimension': next(iter(dimensions)), 'corpus_hash': corpus_hash,
              'unknown_references': 0, 'orphan_relationships': 0,
              'recorded_usage_across_attempts': recorded,
              'usage_limitation': 'In-flight calls externally interrupted before response may not be recorded.',
              'validation_scope': 'source block coverage, checksums, model identities recorded at build time, graph structure; not medical fact verification',
              'graph_backend': config.graph_backend, 'models': summary['models']}
    write_json(root / 'audit.json', result)
    snapshot = root / 'implementation_snapshot'
    existing = snapshot / 'sha256.json'
    if existing.exists():
        for name, checksum in json.loads(existing.read_text(encoding='utf-8')).items():
            if hashlib.sha256((snapshot / name).read_bytes()).hexdigest() != checksum:
                raise ValueError('implementation_snapshot_changed: ' + name)
        return finish_report(root, rows, result, refs, config)
    snapshot.mkdir(exist_ok=True)
    source_paths = [ROOT / 'code/neurogra/benchmarks/medgraphrag' / name for name in
                    ('pipeline.py', 'runtime.py', 'folder_build.py')]
    source_paths.extend([Path(__file__), ROOT / 'code/tools/build_medgraphrag.py',
                         ROOT / 'code/neurogra/benchmarks/patient_view.py'])
    fingerprints = {}
    for source in source_paths:
        shutil.copyfile(source, snapshot / source.name)
        fingerprints[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
    write_json(snapshot / 'build_config.json', summary['config'])
    fingerprints['build_config.json'] = hashlib.sha256((snapshot / 'build_config.json').read_bytes()).hexdigest()
    write_json(snapshot / 'sha256.json', fingerprints)
    return finish_report(root, rows, result, refs, config)


def finish_report(root, rows, result, refs, config):
    lines = ['# 文献知识图构建结果', '', f'完成 {len(rows)} 份文献、{result["pages"]} 页、{len(refs)} 个原文块。',
             f'各文献子图合计 {result["entity_nodes"]} 个实体节点、{result["relationships"]} 条关系；跨文献未做全局去重。',
             '', '| 文献 | 页数 | 实体节点 | 关系 |', '|---|---:|---:|---:|']
    lines += [f'| {r["document"]} | {r["pages"]} | {r["entities"]} | {r["relationships"]} |' for r in rows]
    lines += ['', '生成模型：本地 qwen3.8:27b；嵌入模型：本地 qwen3-embedding:0.6b。',
              f'图存储：{config.graph_backend}。结构与引用编号检查通过；这不等于已完成医学事实审核。', '',
              '主要文件：knowledge_graph.json（图）、knowledge_sources.json（来源）、knowledge_manifest.json（读取清单）、audit.json（核验）、summary.json（逐文件状态）。',
              '模型请求与原始响应保存在 llm_calls.jsonl；构建代码与配置快照保存在 implementation_snapshot。']
    (root / 'build_report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    result = audit(parser.parse_args().directory)
    print(json.dumps({k: result[k] for k in ('status', 'document_count', 'pages', 'source_blocks',
                                            'entity_nodes', 'relationships')}, ensure_ascii=False))
