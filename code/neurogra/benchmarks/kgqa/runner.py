"""Stage checkpoints with immutable bindings; no answer-key access."""
import argparse
import json
from pathlib import Path
import sys
import time

from .core import (GROUPS, SYSTEM, audit, canonical, digest, implementation_identity,
                   load_config, load_queries, read_json, read_case, run_lock, write_json, write_case)


def model_bindings(c, a, models):
    for directory in [a['kb_directory'], a['kg_directory']]:
        vector = read_json(Path(directory) / 'vector_manifest.json')
        if vector['model'] != c['embedding_model'] or vector['model_digest'] != models[c['embedding_model']]:
            raise ValueError('published_embedding_model_changed:' + directory)
    summary_path = Path(c['med_manifest']).parent / 'summary.json'
    summary = read_json(summary_path)
    for name in [c['model'], c['embedding_model']]:
        if summary['models'][name]['digest'] != models[name]:
            raise ValueError('medgraph_build_model_changed:' + name)


def run_cases(c, a, questions, groups, repetitions, output, gateway, adapters, *,
              resume=False, retry_errors=False):
    output = Path(output)
    with run_lock(output):
        models = gateway.preflight()
        model_bindings(c, a, models)
        binding = implementation_identity(c, a, questions, groups, repetitions, models)
        manifest_path = output / 'manifest.json'
        if manifest_path.exists():
            if not resume:
                raise ValueError('run_exists_use_resume_or_new_output')
            manifest = read_json(manifest_path)
            if manifest['binding'] != binding:
                raise ValueError('resume_config_questions_artifacts_code_or_models_changed')
        else:
            if resume:
                raise ValueError('cannot_resume_without_manifest')
            manifest = {'binding': binding, 'config': c, 'audit': a,
                        'models': models, 'questions': questions, 'groups': groups,
                        'repetitions': repetitions, 'system': SYSTEM,
                        'expected_cases': len(questions) * len(groups) * repetitions,
                        'created_at': time.time(), 'status': 'running'}
            write_json(manifest_path, manifest)
        def progress():
            counts = {'completed': 0, 'error': 0, 'retrieved': 0, 'pending': 0}
            for rep in range(1, repetitions + 1):
                for q in questions:
                    for group in groups:
                        p = output / 'cases' / f"{q['id']}_{group}_r{rep}.json"
                        status = read_case(p)['status'] if p.exists() else 'pending'
                        counts[status] += 1
            write_json(output / 'progress.json', {'counts': counts,
                       'expected': manifest['expected_cases'], 'updated_at': time.time()})
        progress()
        try:
            for rep in range(1, repetitions + 1):
                for qindex, q in enumerate(questions):
                    # Rotate method order to reduce consistent warm/cold ordering bias.
                    shift = (qindex + rep - 1) % len(groups)
                    order = groups[shift:] + groups[:shift]
                    for group in order:
                        case_id = f"{q['id']}_{group}_r{rep}"
                        path = output / 'cases' / (case_id + '.json')
                        directory = output / 'case_logs' / case_id
                        directory.mkdir(parents=True, exist_ok=True)
                        record = read_case(path) if path.exists() else {
                            'case_id': case_id, 'question': q, 'group': group,
                            'repetition': rep, 'run_binding': binding, 'status': 'pending'}
                        if record['run_binding'] != binding:
                            raise ValueError('case_binding_changed')
                        if record['status'] == 'completed' or (record['status'] == 'error' and not retry_errors):
                            continue
                        if record['status'] == 'error':
                            record.setdefault('previous_errors', []).append(record.pop('error'))
                        started = time.monotonic()
                        gateway.case_id = case_id
                        calls_before = (gateway.calls, gateway.embedding_calls, dict(gateway.usage))
                        try:
                            if 'retrieval' not in record:
                                record['retrieval'] = adapters.retrieve(group, q['question'], directory)
                                record['status'] = 'retrieved'
                                write_case(path, record)
                            payload = {'question': q['question'],
                                       'evidence': record['retrieval']['evidence']}
                            record['answer_input'] = {'system': SYSTEM, 'payload': payload}
                            # Preserve actual input even when the final model fails.
                            write_case(path, record)
                            answer_start = time.monotonic()
                            record['answer'] = gateway.generate(SYSTEM, payload,
                                'answer:' + case_id, answer=True)
                            record['answer_seconds'] = time.monotonic() - answer_start
                            record['status'] = 'completed'
                        except Exception as exc:
                            record['status'] = 'error'
                            record['error'] = {'type': type(exc).__name__, 'message': str(exc)}
                        finally:
                            record.setdefault('attempts', []).append({
                                'seconds': time.monotonic() - started,
                                'generation_calls': gateway.calls - calls_before[0],
                                'embedding_calls': gateway.embedding_calls - calls_before[1],
                                'usage_delta': {k: gateway.usage[k] - calls_before[2][k] for k in gateway.usage}})
                            write_case(path, record)
                            progress()
                        print(case_id, record['status'], flush=True)
            manifest['status'] = 'finished_with_errors' if read_json(output / 'progress.json')['counts']['error'] else 'completed'
        except BaseException:
            manifest['status'] = 'interrupted'
            raise
        finally:
            manifest['updated_at'] = time.time()
            write_json(manifest_path, manifest)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Four-arm frozen-corpus QA benchmark')
    parser.add_argument('--config', required=True)
    parser.add_argument('--output')
    parser.add_argument('--check', action='store_true', help='offline artifact check only')
    parser.add_argument('--preflight-models', action='store_true', help='also check local model digests; no QA')
    parser.add_argument('--groups', nargs='+', choices=GROUPS, default=list(GROUPS))
    parser.add_argument('--questions', nargs='+', help='explicit pilot IDs, e.g. Q08 Q09')
    parser.add_argument('--repetitions', type=int, default=1)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--retry-errors', action='store_true')
    args = parser.parse_args(argv)
    c = load_config(args.config)
    if args.output:
        c['output'] = str(Path(args.output).resolve())
    if args.repetitions < 1 or len(set(args.groups)) != len(args.groups):
        parser.error('positive repetitions and unique groups required')
    questions = load_queries(c['questions'])
    if args.questions:
        if set(args.questions) - {q['id'] for q in questions}:
            parser.error('unknown question IDs')
        questions = [q for q in questions if q['id'] in args.questions]
    a = audit(c)
    output = Path(c['output'])
    if args.check:
        write_json(output / 'artifact_check.json', {'audit': a, 'questions': len(questions)})
        kg_summary = {k: v for k, v in a['kg'].items() if k != 'construction_quality'}
        if quality := a['kg'].get('construction_quality'):
            kg_summary['construction_quality'] = {
                k: quality[k] for k in ['coverage_label', 'documents_with_statements', 'source_documents_expected']}
            kg_summary['construction_quality']['skipped_units'] = sum(len(d['skipped_units']) for d in quality['documents'])
        print(canonical({'questions': len(questions), 'kb': a['kb'], 'kg': kg_summary,
                         'medgraphrag': a['medgraphrag'], 'limitations': a['limitations']}))
        if not args.preflight_models:
            return 0
    from .model import Gateway
    gateway = Gateway(c, output / 'model_logs')
    if args.preflight_models:
        models = gateway.preflight()
        model_bindings(c, a, models)
        write_json(output / 'model_preflight.json', {'models': models, 'status': 'ok'})
        print('model preflight ok; no questions executed')
        return 0
    from .adapters import RetrievalAdapters
    run_cases(c, a, questions, args.groups, args.repetitions, output, gateway,
              RetrievalAdapters(c, a, gateway), resume=args.resume, retry_errors=args.retry_errors)
    return 1 if read_json(output / 'progress.json')['counts']['error'] else 0


if __name__ == '__main__':
    sys.exit(main())
