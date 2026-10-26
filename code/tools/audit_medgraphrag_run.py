"""Offline structural/provenance audit of one completed MedGraphRAG patient run."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'code'))

from neurogra.benchmarks.patient_view import digest
from neurogra.benchmarks.medgraphrag.pipeline import Prediction, comparison_labels, validate_prediction
from neurogra.benchmarks.medgraphrag.runtime import write_json
from neurogra.benchmarks.medgraphrag.retrieval import prompt_graph, score_trends
from medgraphrag_provenance import verify_adapter_implementation


def audit(directory):
    root = Path(directory)
    def read(name):
        return json.loads((root / name).read_text(encoding='utf-8'))
    def check(condition, reason):
        if not condition:
            raise ValueError(reason)
    manifest, result, labels = read('manifest.json'), read('prediction.json'), read('labels.json')
    check(read('status.json')['status'] == result['execution_status'] == 'completed', 'run_not_completed')
    check(manifest['model_service_verified'], 'models_not_preflighted')
    check(set(manifest['local_models']) == {'qwen3.8:27b', 'qwen3-embedding:0.6b'}, 'wrong_models')
    check(manifest['config']['graph_backend'] == 'json', 'expected_json_backend')
    check(digest(read('visible_patient.json')) == manifest['visible_hash'], 'visible_input_changed')
    verify_adapter_implementation(manifest['implementation_files'])
    facts = read('source_map.json')
    compact, presentation = read('model_patient.json'), read('presentation.json')
    check(digest(compact) == presentation['presentation_hash'], 'presentation_changed')
    restored = {}
    for group in compact['groups']:
        for ref, field, value in group['facts']:
            check(ref not in restored, 'duplicate_fact')
            restored[ref] = [group['path'] + '/' + field, group['context'], value, type(value)]
    check(len(facts) == len(restored) == presentation['fact_count'], 'fact_count_mismatch')
    for fact in facts:
        check(restored[fact['id']] == [fact['pointer'], fact['context'], fact['value'], type(fact['value'])],
              'fact_or_context_changed')
    top = read('patient-' + result['run_id'] + '.json')
    check(set(top['source_refs']) == set(restored), 'patient_graph_source_coverage_mismatch')
    retrieval, originals = read('retrieval.json'), read('knowledge_sources.json')
    check(read('score_trends.json') == score_trends(facts), 'arithmetic_trends_changed')
    check(retrieval['model_graph'] == prompt_graph(retrieval['context']), 'graph_alias_mapping_changed')
    for ref, excerpt in retrieval['knowledge_refs'].items():
        source = originals[ref]
        check(excerpt['text'] == source['text'][excerpt['excerpt_start']:excerpt['excerpt_end']],
              'excerpt_not_verbatim')
        check(excerpt['page'] == source['page'] and excerpt['document_id'] == source['document_id'],
              'excerpt_source_mismatch')
        check(excerpt['page_text_offset'] == source.get('offset', 0) + excerpt['excerpt_start'],
              'excerpt_offset_mismatch')
    nodes = {(e['gid'], e['id']) for e in retrieval['context']['entities']}
    for rel in retrieval['context']['relationships']:
        check((rel['gid'], rel['source']) in nodes and (rel['gid'], rel['target']) in nodes,
              'selected_relationship_has_orphan_endpoint')
    prediction = Prediction.model_validate({k: result[k] for k in Prediction.model_fields})
    validate_prediction(prediction, result['task'], set(restored), set(retrieval['knowledge_refs']))
    check(all(labels[k] == v for k, v in comparison_labels(prediction).items()), 'label_mapping_mismatch')
    check(all(labels[k] == result[k] for k in labels), 'labels_disagree_with_prediction')
    check(prediction.report in (root / 'report.md').read_text(encoding='utf-8'), 'report_body_changed')
    knowledge_hits = 0
    for line in (root / 'cache_events.jsonl').read_text(encoding='utf-8').splitlines():
        knowledge_hits += json.loads(line)['event'] == 'knowledge_cache_hit'
    calls = root / 'llm_calls.jsonl'
    for line in calls.read_text(encoding='utf-8').splitlines() if calls.exists() else []:
        entry = json.loads(line)
        check(entry['request']['model'] in manifest['local_models'], 'nonlocal_model_in_log')
        if entry['endpoint'] == '/api/chat':
            check(entry.get('response', {}).get('done_reason') != 'length', 'truncated_model_response')
    report = {'status': 'passed', 'patient_fact_count': len(facts), 'patient_facts_omitted': 0,
              'knowledge_graph_cache_hits': knowledge_hits,
              'selected_evidence_blocks': len(retrieval['knowledge_refs']),
              'selected_entities': len(nodes),
              'selected_relationships': len(retrieval['context']['relationships']),
              'citation_check': 'identifiers_and_verbatim_excerpt_offsets_only',
              'clinical_semantics_verified': False, 'execution_status': result['execution_status'],
              'models': manifest['local_models'], 'labels': labels}
    write_json(root / 'run_audit.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    value = audit(parser.parse_args().directory)
    print(json.dumps({k: v for k, v in value.items() if k not in {'models', 'labels'}}, ensure_ascii=False))
