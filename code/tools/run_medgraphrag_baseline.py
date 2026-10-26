"""MedGraphRAG-adapted entry point; --prepare-only is entirely offline."""
import argparse
import json
from pathlib import Path
import sys
import uuid
import time
import hashlib
import subprocess

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'code'))

import yaml
from medgraphrag_provenance import adapter_fingerprints

from neurogra.benchmarks.patient_view import prepare_patient, digest
from neurogra.benchmarks.medgraphrag.pipeline import TASKS, load_knowledge, run_pipeline
from neurogra.benchmarks.medgraphrag.runtime import Config, LocalModel, Neo4jStore, JsonGraphStore, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patient', type=Path, required=True)
    parser.add_argument('--task', choices=TASKS, default='clinical_review')
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/medgraphrag.baseline.yaml')
    parser.add_argument('--knowledge-manifest', type=Path, help='Override with a PDF folder build manifest')
    parser.add_argument('--policy', type=Path, help='Reviewed visibility/cutoff policy, JSON')
    parser.add_argument('--source-trim-manifest', type=Path, help='Manifest of an already trimmed input')
    parser.add_argument('--output', type=Path, required=True, help='Must be a new directory')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args(argv)
    config_path = args.config.resolve()
    config = Config.model_validate(yaml.safe_load(config_path.read_text(encoding='utf-8-sig')) or {})
    config.validate_local()
    if args.knowledge_manifest:
        config.knowledge_manifest = str(args.knowledge_manifest.resolve())
    if config.knowledge_cache and not Path(config.knowledge_cache).is_absolute():
        config.knowledge_cache = str((config_path.parent / config.knowledge_cache).resolve())
    if config.model_cache and not Path(config.model_cache).is_absolute():
        config.model_cache = str((config_path.parent / config.model_cache).resolve())
    policy = json.loads(args.policy.read_text(encoding='utf-8-sig')) if args.policy else {}
    raw = json.loads(args.patient.read_text(encoding='utf-8-sig'))
    # Validate before creating the output directory.
    prepared = prepare_patient(raw, task=args.task, policy=policy)
    trim_path = args.source_trim_manifest or args.patient.with_suffix('.manifest.json')
    source_trim = json.loads(trim_path.read_text(encoding='utf-8-sig')) if trim_path.exists() else None
    if source_trim:
        if args.task != 'clinical_review':
            raise ValueError('evaluation_requires_untrimmed_source_then_cutoff_and_trim')
        prepared['manifest']['upstream_trim_manifest'] = source_trim
        prepared['manifest']['limitations'].append('Original pointers refer to supplied trimmed JSON; see upstream manifest for original source rows.')
    if args.task != 'clinical_review' and policy.get('source_is_complete') is not True:
        raise ValueError('evaluation_requires_source_is_complete_attestation')
    args.output.mkdir(parents=True, exist_ok=False)
    run_id = uuid.uuid4().hex
    prepared['manifest'].update({'run_id': run_id, 'config': config.model_dump(),
                                'config_hash': digest(config.model_dump()),
                                'implementation': 'neurogra-medgraphrag-adapted-v1',
                                'upstream': 'local Medical-Graph-RAG; independent task adapter',
                                'input_file': str(args.patient.resolve()),
                                'model_service_verified': False})
    prepared['manifest']['implementation_files'] = adapter_fingerprints()
    upstream_root = ROOT.parent / 'MGraphRAG/Medical-Graph-RAG'
    if upstream_root.exists():
        prepared['manifest']['upstream_files'] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in upstream_root.glob('*.py')}
        revision = subprocess.run(['git', '-C', str(upstream_root), 'rev-parse', 'HEAD'],
                                  capture_output=True, text=True, timeout=10)
        prepared['manifest']['upstream_commit'] = revision.stdout.strip() if revision.returncode == 0 else None
    write_json(args.output / 'visible_patient.json', prepared['visible'])
    (args.output / 'patient.txt').write_text(prepared['text'], encoding='utf-8')
    write_json(args.output / 'source_map.json', prepared['sources'])
    write_json(args.output / 'attachments.json', prepared['images'])
    write_json(args.output / 'manifest.json', prepared['manifest'])
    if args.prepare_only:
        write_json(args.output / 'status.json', {'status': 'prepared', 'run_id': run_id,
                                               'model_called': False, 'database_connected': False})
        print(f'Prepared {len(prepared["sources"])} facts: {args.output.resolve()}')
        return 0
    client, store = None, None
    start = time.monotonic()
    try:
        if not config.knowledge_manifest:
            raise ValueError('knowledge_manifest_required_for_inference')
        knowledge_path = Path(config.knowledge_manifest)
        if not knowledge_path.is_absolute():
            knowledge_path = config_path.parent / knowledge_path
        documents, refs, knowledge_hash = load_knowledge(knowledge_path, config.chunk_chars)
        prepared['manifest']['knowledge_hash'] = knowledge_hash
        write_json(args.output / 'knowledge_sources.json', refs)
        write_json(args.output / 'manifest.json', prepared['manifest'])
        # Fail before model calls if dedicated database/credentials are unavailable.
        store = Neo4jStore(config) if config.graph_backend == 'neo4j' else JsonGraphStore(args.output)
        client = LocalModel(config, args.output)
        prepared['manifest']['local_models'] = client.preflight()
        prepared['manifest']['model_service_verified'] = True
        write_json(args.output / 'manifest.json', prepared['manifest'])
        result = run_pipeline(prepared, config, client, store, args.output, run_id,
                              documents, refs, knowledge_hash)
        print(f'{result["primary_label"]}: {args.output.resolve()}')
        write_json(args.output / 'status.json', {'status': 'completed', 'seconds': time.monotonic()-start,
                                               'run_id': run_id})
        return 0
    except Exception as exc:
        usage = ({**client.usage, 'generation_calls': client.calls,
                  'embedding_calls': client.embedding_calls} if client else {})
        write_json(args.output / 'prediction.json', {
            'method': 'MedGraphRAG-adapted', 'run_id': run_id, 'task': args.task,
            'case_id': prepared['manifest']['visible_hash'], 'primary_label': None,
            'execution_status': 'failed', 'parse_status': 'unavailable',
            'error': {'type': type(exc).__name__, 'message': str(exc)}, 'usage': usage})
        write_json(args.output / 'status.json', {'status': 'failed', 'run_id': run_id,
                                               'seconds': time.monotonic()-start, 'error': str(exc)})
        print(f'Failed: {exc}; evidence: {args.output.resolve()}', file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
