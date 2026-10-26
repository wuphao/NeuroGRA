"""Read-only context-management acceptance metrics for a completed clinical run."""
import argparse
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[2]


def collect(run_id):
    with sqlite3.connect((ROOT / 'data/clinical/clinical.sqlite').as_uri() + '?mode=ro', uri=True) as db:
        status, config = db.execute('SELECT status,config FROM runs WHERE run_id=?', (run_id,)).fetchone()
        config = json.loads(config)
        objects = [(kind, json.loads(payload)) for kind, payload in db.execute(
            'SELECT kind,payload FROM objects WHERE run_id=? ORDER BY version', (run_id,))]
        events = [(kind, json.loads(payload)) for kind, payload in db.execute(
            'SELECT type,payload FROM events WHERE run_id=?', (run_id,))]
        usage = [{'kind': k, 'status': s, 'calls': n} for k, s, n in db.execute(
            'SELECT kind,status,count(*) FROM budget_ledger WHERE run_id=? GROUP BY kind,status', (run_id,))]
    outcome = next((p for k, p in reversed(objects) if k == 'run_result'), {})
    report = outcome.get('report', {})
    metrics = [p for k, p in events if k == 'context_usage']
    rejected = [p for k, p in events if k == 'context_rejected']
    roles = {p['task_id']: p['agent_type'] for k, p in objects if k == 'task'}
    coverage = []
    for result in report.get('agent_results', []):
        cv = result.get('coverage', {})
        coverage.append({'role': roles.get(result['task_id']), 'task_id': result['task_id'],
            'status': result['status'], 'total': cv.get('total'),
            **{k: len(cv.get(k + '_ids', [])) for k in ('analyzed', 'failed', 'deferred')},
            'verification': cv.get('verification', 'unknown')})
    return {'run_id': run_id, 'status': status, 'stop_reason': outcome.get('stop_reason'),
        'assessment_status': report.get('assessment_status'),
        'narrative_method': report.get('narrative_method'), 'report_paths': outcome.get('report_paths'),
        'preflight_calls': len(metrics), 'context_rejected_count': len(rejected),
        'max_business_chars': max((m['business_chars'] for m in metrics), default=None),
        'max_estimated_input_tokens': max((m['estimated_input_tokens'] for m in metrics), default=None),
        'max_actual_input_tokens': max((m.get('actual_input_tokens') or 0 for m in metrics), default=None),
        'cache_hits': sum(k == 'context_cache_hit' for k, p in events),
        'coverage': coverage, 'usage': usage, 'rejections': rejected,
        'configuration': {k: config.get(k) for k in ('budget', 'knowledge_model_calls',
            'synthesis_reserved_calls', 'max_parallel_tasks', 'verify_narrative')},
        'generation_model': config.get('model', {}).get('model'),
        'knowledge_versions': {k: p.get('release_id') for k, p in objects
            if k in {'knowledge_binding', 'text_knowledge_binding'}},
        'source_hash': next((p.get('raw_input_hash') for k, p in objects if k == 'intake'), None)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--baseline')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/clinical/context_acceptance.json')
    args = parser.parse_args()
    result = {'current': collect(args.run_id)}
    if args.baseline:
        result['baseline'] = collect(args.baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
