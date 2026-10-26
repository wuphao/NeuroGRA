"""Resume per-paper full-text construction, then publish one eight-paper corpus.

No evaluation question or answer key is read by this program.
"""
import argparse
import json
import os
from pathlib import Path
import time
from contextlib import contextmanager

from neurogra_kg import KnowledgeGraphService
from neurogra_kg.config import load_config
from neurogra_kg.contracts.domain import BuildRequest
from neurogra_kg.runtime import implementation_binding
from neurogra_kg.utils import atomic_json, checksum, identity
from neurogra.benchmarks.kgqa.core import run_lock


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def runtime_binding(service):
    return identity('build_runtime', ['construction_pipeline_v3',
        implementation_binding(['construction', 'adapters']),
        service.config.construction.model_dump(mode='json'), service.llm.binding(),
        service.embedding.config.model_dump(mode='json'), service.embedding.model_digest()])


def matching_builds(service, document, corpus_version, runtime):
    binding = identity('input', [{'checksum': checksum(document.path),
        'version': document.version, 'title': document.title, 'language': document.language}])
    found = []
    for path in (service.output_root / 'builds').glob('*/request.json'):
        row = read(path)
        if (row.get('binding') != binding
                or row['request']['corpus_version'] != corpus_version):
            continue
        result_path = path.parent / 'result.json'
        result = read(result_path) if result_path.exists() else {}
        # Completed builds are immutable source-checked inputs to publish_builds.
        # Incomplete checkpoints may only resume under their exact runtime binding.
        if row.get('runtime_binding') != runtime and result.get('status') != 'completed':
            continue
        found.append((result.get('status') == 'completed', result.get('completed_units', 0),
                      path.stat().st_mtime, path.parent.name, result))
    return sorted(found, reverse=True)


def validate_publish_inputs(service, rows, expected):
    """Reject empty/changed paper contributions before any active release change."""
    from neurogra_kg.contracts.domain import KnowledgeStatement, ParsedDocument
    from neurogra_kg.construction.sources import condition_leaves, validate_ref
    from collections import Counter
    documents, statements = {}, {}
    counts = {}
    for row in rows:
        if service.inspect_build(row['build_id']).status != 'completed':
            raise ValueError('cannot_publish_incomplete_paper:' + row['file'])
        directory = service.output_root / 'builds' / row['build_id']
        parsed = [ParsedDocument.model_validate(d) for d in read(directory / 'documents.json')]
        if len(parsed) != 1 or parsed[0].document.checksum != row['checksum']:
            raise ValueError('paper_source_changed:' + row['file'])
        document = parsed[0]
        if checksum(Path(document.document.snapshot_path)) != row['checksum']:
            raise ValueError('paper_snapshot_changed:' + row['file'])
        blocks = {block.block_id: block for block in document.blocks}
        items = [KnowledgeStatement.model_validate(s) for s in read(directory / 'statements.json')]
        accepted = [s for s in items if s.status in {'approved', 'source_checked'}]
        if not accepted:
            raise ValueError('no_accepted_statements_for_paper:' + row['file'])
        for s in accepted:
            for ref in [*s.source_refs, *[r for leaf in condition_leaves(s.condition) for r in leaf.source_refs]]:
                validate_ref(ref, blocks)
            if s.statement_id in statements and statements[s.statement_id] != s:
                raise ValueError('conflicting_statement_identity')
            statements[s.statement_id] = s
        documents[document.document.document_id] = document.document.checksum
        counts[row['file']] = {'accepted': len(accepted), 'all': len(items),
                              'statuses': dict(Counter(s.status for s in items))}
    if len(documents) != 8 or set(documents.values()) != expected:
        raise ValueError('eight_paper_coverage_required_before_publish')
    return counts


@contextmanager
def skip_failed_units(service, build_id):
    """Honor explicit skip policy without changing or completing failed checkpoints."""
    import neurogra_kg.construction.pipeline as pipeline
    original = pipeline.extraction_units
    def units(parsed, max_chars):
        directory = service.output_root / 'builds' / build_id / parsed.document.document_id
        for unit_id, blocks, issue in original(parsed, max_chars):
            if (directory / (unit_id + '.failed.json')).exists() and not (directory / (unit_id + '.json')).exists():
                yield unit_id, None, 'skipped_failed_unit_by_explicit_user_policy'
            else:
                yield unit_id, blocks, issue
    pipeline.extraction_units = units
    try:
        yield
    finally:
        pipeline.extraction_units = original


def publish_available(service, publisher, rows, output, phase):
    """Publish only successful checkpoints and verified statements; retain all omissions."""
    from neurogra_kg.contracts.domain import KnowledgeStatement, ParsedDocument, BuildResult
    from neurogra_kg.construction.sources import condition_leaves, validate_ref
    from neurogra_kg.storage.releases import create_release, load_release
    from neurogra_kg.runtime import Budget
    statements, documents, coverage, members = {}, {}, [], []
    for row in rows:
        directory = service.output_root / 'builds' / row['build_id']
        parsed_paths = list(directory.glob('doc_*.parsed.json'))
        entry = {'file': row['file'], 'checksum': row['checksum'], 'build_id': row['build_id'],
                 'status': row['status'], 'completed_units': 0, 'skipped_units': [],
                 'accepted_statements': 0, 'omitted_statements': [], 'checkpoint_checksums': {}}
        coverage.append(entry)
        if not parsed_paths:
            continue
        if len(parsed_paths) != 1:
            raise ValueError('expected_one_parsed_paper')
        parsed = ParsedDocument.model_validate(read(parsed_paths[0]))
        if parsed.document.checksum != row['checksum'] or checksum(Path(parsed.document.snapshot_path)) != row['checksum']:
            raise ValueError('paper_snapshot_changed')
        blocks = {b.block_id: b for b in parsed.blocks}
        unit_dir = directory / parsed.document.document_id
        accepted = {}
        for path in sorted(unit_dir.glob('*.json')):
            saved = read(path)
            if 'checkpoint_checksum' not in saved:
                continue
            recorded = saved.pop('checkpoint_checksum')
            if identity('checkpoint', saved) != recorded:
                raise ValueError('checkpoint_checksum_mismatch:' + str(path))
            entry['completed_units'] += 1
            entry['checkpoint_checksums'][path.name] = checksum(path)
            for raw in saved['statements']:
                statement = KnowledgeStatement.model_validate(raw)
                if statement.status not in {'approved', 'source_checked'}:
                    entry['omitted_statements'].append({'id': statement.statement_id, 'reason': statement.status})
                    continue
                try:
                    for ref in [*statement.source_refs,
                            *[r for leaf in condition_leaves(statement.condition) for r in leaf.source_refs],
                            *[r for refs in statement.field_evidence.values() for r in refs]]:
                        validate_ref(ref, blocks)
                except ValueError as exc:
                    entry['omitted_statements'].append({'id': statement.statement_id, 'reason': str(exc)})
                    continue
                accepted[statement.statement_id] = statement
        for failed in sorted(unit_dir.glob('*.failed.json')):
            success = unit_dir / failed.name.replace('.failed.json', '.json')
            if not success.exists():
                entry['skipped_units'].append({'unit_id': failed.name.removesuffix('.failed.json'),
                                               'issues': read(failed).get('issues', [])})
        result_path = directory / 'result.json'
        result = read(result_path) if result_path.exists() else {}
        entry['total_units'] = result.get('current_document_units')
        entry['build_status'] = result.get('status')
        entry['accepted_statements'] = len(accepted)
        if accepted:
            documents[parsed.document.document_id] = parsed
            for sid, statement in accepted.items():
                if sid in statements and statements[sid] != statement:
                    raise ValueError('conflicting_statement_identity')
                statements[sid] = statement
            members.append(row['build_id'])
    if not statements:
        raise ValueError('no_verified_statements_available')
    collection_id = identity('build_fast_collection', [coverage, sorted(statements)])
    directory = service.output_root / 'builds' / collection_id
    directory.mkdir(parents=True, exist_ok=True)
    completed = sum(d['completed_units'] for d in coverage)
    failed = sum(len(d['skipped_units']) for d in coverage)
    # A skipped failure remains a failure; never rewrite a partial build as completed.
    collection = BuildResult(build_id=collection_id, status='partial', completed_units=completed,
                             failed_units=failed, statement_ids=sorted(statements))
    atomic_json(directory / 'result.json', collection.model_dump(mode='json'))
    atomic_json(directory / 'members.json', {'build_ids': members, 'failure_policy': 'skip_failed'})
    atomic_json(directory / 'statements.json', [statements[k].model_dump(mode='json') for k in sorted(statements)])
    atomic_json(directory / 'documents.json', [d.model_dump(mode='json') for d in documents.values()])
    service.registry.save('build', collection_id, collection)
    release = create_release(publisher.data_root, publisher.registry, collection_id,
        [statements[k] for k in sorted(statements)], list(documents.values()),
        publisher.embedding, publisher.graph, Budget(14400, max_embedding_calls=20000))
    load_release(release)
    quality = {'release_id': release['release_id'], 'phase': phase,
               'coverage_label': 'partial_coverage_failed_units_skipped', 'failure_policy': 'skip_failed',
               'source_documents_expected': len(rows), 'documents_with_statements': len(documents),
               'accepted_statements': len(statements), 'documents': coverage}
    quality_path = output / 'coverage' / (release['release_id'] + '.json')
    if quality_path.exists() and read(quality_path) != quality:
        quality_path = output / 'coverage' / (release['release_id'] + '-' + phase + '.json')
    atomic_json(quality_path, quality)
    atomic_json(output / ('release-' + phase + '.json'), release)
    print('PUBLISHED_PARTIAL', phase, release['release_id'], len(statements),
          'documents', len(documents), 'skipped_units', failed, flush=True)
    return release, quality_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='configs/kg.local.yaml')
    parser.add_argument('--manifest', default='configs/kg.bilingual_core.manifest.json')
    parser.add_argument('--publish-config', default='configs/kg.local.yaml')
    parser.add_argument('--output', default='output/kg/core_corpus_20261017')
    parser.add_argument('--attempts', type=int, default=4)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--fast', action='store_true', help='single pass, skip failed units, publish verified partial coverage')
    args = parser.parse_args(argv)
    if args.attempts < 1:
        parser.error('positive attempts required')
    cfg = load_config(args.config)
    publish_cfg = load_config(args.publish_config)
    if (cfg.resolve(cfg.paths.data_root) != publish_cfg.resolve(publish_cfg.paths.data_root)
            or cfg.resolve(cfg.paths.output_root) != publish_cfg.resolve(publish_cfg.paths.output_root)
            or cfg.embedding.model != publish_cfg.embedding.model):
        raise ValueError('publish_service_must_share_sources_and_embedding_model')
    request = BuildRequest.model_validate(read(args.manifest))
    for document in request.documents:
        document.path = cfg.resolve(document.path)
    source_rows = read(cfg.resolve('docs/references/neurogra_kg_core/sources.json'))
    expected = {row['sha256'] for row in source_rows}
    if len(request.documents) != 8 or {checksum(d.path) for d in request.documents} != expected:
        raise ValueError('exact_eight_core_sources_required')
    output = cfg.resolve(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with run_lock(output), KnowledgeGraphService(cfg) as service:
        service._check_runtime()
        runtime = runtime_binding(service)
        binding = identity('core_corpus_job', [runtime, request.model_dump(mode='json')])
        publish_binding = identity('publish_config', publish_cfg.model_dump(mode='json'))
        progress_path = output / 'progress.json'
        if progress_path.exists():
            progress = read(progress_path)
            if progress['binding'] != binding:
                raise ValueError('job_input_or_runtime_changed_use_new_output')
            if progress.get('publish_binding', publish_binding) != publish_binding:
                raise ValueError('publish_configuration_changed')
            if progress.get('status') in {'completed', 'completed_with_skips'}:
                release = service.get_release(progress['release_id'])
                from neurogra_kg.storage.releases import load_release
                load_release(release)
                print('Already completed:', progress['release_id'], flush=True)
                return 0
        else:
            progress = {'binding': binding, 'status': 'planned', 'created_at': time.time(),
                        'documents': [], 'previous_active_release': service.registry.active_release_id()}
            for document in request.documents:
                candidates = matching_builds(service, document, request.corpus_version, runtime)
                chosen = candidates[0] if candidates else None
                progress['documents'].append({'file': document.path.name,
                    'checksum': checksum(document.path), 'document': document.model_dump(mode='json'),
                    'build_id': chosen[3] if chosen else identity('build', [binding, document.model_dump(mode='json')]),
                    'status': chosen[4].get('status', 'pending') if chosen else 'pending',
                    'completed_units': chosen[1] if chosen else 0,
                    'history': [],
                    'source_runtime_binding': read(service.output_root / 'builds' / chosen[3] / 'request.json')['runtime_binding'] if chosen else runtime})
            # Restore finished work first, then resume the furthest partial paper.
            progress['documents'].sort(key=lambda row: (row['status'] != 'completed',
                -row['completed_units'], not row['file'].startswith('02_'), row['file']))
            atomic_json(output / 'plan.json', progress)
        def save():
            progress['process_id'] = os.getpid()
            progress['publish_binding'] = publish_binding
            progress['updated_at'] = time.time()
            atomic_json(progress_path, progress)
        save()
        if args.check:
            print(json.dumps(progress, ensure_ascii=False, indent=2))
            return 0
        progress['status'] = 'building'
        progress['failure_policy'] = 'skip_failed_single_pass' if args.fast else 'strict_retry'
        save()
        try:
            if args.fast and not progress.get('initial_partial_release'):
                with KnowledgeGraphService(publish_cfg) as publisher:
                    stage_release, stage_quality = publish_available(service, publisher,
                        progress['documents'], output, 'available')
                progress['initial_partial_release'] = stage_release['release_id']
                progress['initial_partial_quality'] = str(stage_quality)
                save()
            for row in progress['documents']:
                if row['status'] == 'completed':
                    current = service.inspect_build(row['build_id'])
                    if current.status != 'completed':
                        raise ValueError('completed_build_changed')
                    print('REUSE', row['file'], row['build_id'], len(current.statement_ids), flush=True)
                    continue
                if args.fast and row['status'] == 'partial':
                    print('KEEP_PARTIAL_NO_RETRY', row['file'], row['build_id'], flush=True)
                    continue
                for attempt in range(1 if args.fast else args.attempts):
                    row['status'] = 'running'
                    progress['current_file'], progress['current_build_id'] = row['file'], row['build_id']
                    save()
                    started = time.time()
                    print('BUILD', row['file'], row['build_id'], 'attempt', attempt + 1, flush=True)
                    try:
                        request_one = BuildRequest(
                            documents=[row['document']], build_id=row['build_id'],
                            corpus_version=request.corpus_version, max_seconds=request.max_seconds,
                            max_llm_calls=request.max_llm_calls,
                            max_embedding_calls=request.max_embedding_calls)
                        if args.fast:
                            with skip_failed_units(service, row['build_id']):
                                result = service.build(request_one)
                        else:
                            result = service.build(request_one)
                        row.update(status=result.status, completed_units=result.completed_units,
                            failed_units=result.failed_units, statements=len(result.statement_ids),
                            rejected=len(result.rejected_ids))
                        row['history'].append({'status': result.status, 'seconds': time.time() - started,
                                               'failed_units': result.failed_units})
                    except Exception as exc:
                        row['status'] = 'error'
                        row['history'].append({'status': 'error', 'seconds': time.time() - started,
                                               'error': type(exc).__name__ + ':' + str(exc)})
                        # Configuration/checkpoint errors are not repaired by another model call.
                        if 'changed' in str(exc) or 'checksum' in str(exc):
                            raise
                    save()
                    print('RESULT', row['file'], row['status'], flush=True)
                    if row['status'] == 'completed':
                        break
            unfinished = [row['file'] for row in progress['documents'] if row['status'] != 'completed']
            if unfinished and not args.fast:
                progress['status'] = 'needs_repair'
                progress['unfinished'] = unfinished
                save()
                return 1
            progress['status'] = 'publishing'
            if args.fast:
                save()
                with KnowledgeGraphService(publish_cfg) as publisher:
                    release, quality_path = publish_available(service, publisher, progress['documents'], output, 'final')
                progress.update(status='completed_with_skips', release_id=release['release_id'],
                                quality_report=str(quality_path), unfinished_full_builds=unfinished)
                atomic_json(output / 'release.json', release)
                experiment = read(cfg.resolve('configs/kgqa.core30.json'))
                experiment.update(kg_release=release['release_id'],
                    kg_config=str(Path(args.publish_config).resolve().relative_to(cfg.root)).replace('\\', '/'),
                    kg_quality_report=str(quality_path.relative_to(cfg.root)).replace('\\', '/'),
                    output='output/benchmarks/kgqa/core30-fast-v1')
                target = cfg.resolve('configs/kgqa.core30.fast.json')
                if target.exists() and read(target) != experiment:
                    experiment['root'] = str(cfg.root)
                    target = output / 'experiment_config.json'
                atomic_json(target, experiment)
                progress['experiment_config'] = str(target)
                save()
                return 0
            progress['paper_quality'] = validate_publish_inputs(service, progress['documents'], expected)
            save()
            # Publish with the normal query service's embedding contract, so that
            # model-residency options used while building do not break query vectors.
            with KnowledgeGraphService(publish_cfg) as publisher:
                release = publisher.publish_builds([row['build_id'] for row in progress['documents']],
                                                   max_seconds=14400, max_embedding_calls=20000)
            from neurogra_kg.storage.releases import load_release
            statements, blocks, resources, corpus = load_release(release)
            if {d['checksum'] for d in corpus['documents']} != expected:
                raise ValueError('published_corpus_coverage_mismatch')
            counts = {d['document_id']: 0 for d in corpus['documents']}
            for statement in statements:
                for doc_id in {blocks[ref.block_id].document_id for ref in statement.source_refs}:
                    counts[doc_id] += 1
            if not all(counts.values()):
                raise ValueError('published_document_without_statements')
            progress.update(status='completed', release_id=release['release_id'],
                statement_count=len(statements), document_statement_counts=counts,
                resource_count=len(resources))
            atomic_json(output / 'release.json', release)
            experiment_path = cfg.resolve('configs/kgqa.core30.json')
            if experiment_path.exists():
                experiment = read(experiment_path)
                experiment['kg_release'] = release['release_id']
                experiment['kg_config'] = str(Path(args.publish_config).resolve().relative_to(cfg.root)).replace('\\', '/')
                experiment['output'] = 'output/benchmarks/kgqa/core30-full-corpus-v1'
                experiment_target = cfg.resolve('configs/kgqa.core30.full-corpus.json')
                if experiment_target.exists() and read(experiment_target) != experiment:
                    experiment['root'] = str(cfg.root)
                    atomic_json(output / 'experiment_config.json', experiment)
                    progress['experiment_config'] = str(output / 'experiment_config.json')
                else:
                    atomic_json(experiment_target, experiment)
                    progress['experiment_config'] = str(experiment_target)
            save()
            print('PUBLISHED', release['release_id'], len(statements), flush=True)
            return 0
        except BaseException:
            progress['status'] = 'interrupted'
            save()
            raise


if __name__ == '__main__':
    raise SystemExit(main())
