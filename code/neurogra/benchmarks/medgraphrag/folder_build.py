"""PDF directory -> immutable sources -> reusable per-document knowledge graphs."""
import hashlib
from pathlib import Path
import shutil
import time
import uuid

from neurogra.benchmarks.patient_view import digest
from .pipeline import parse_document, knowledge_graph, link_graphs
from .runtime import LocalModel, JsonGraphStore, Neo4jStore, write_json


def scan_pdfs(folder, recursive=True):
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError('pdf_folder_not_a_directory: ' + str(folder))
    paths = folder.rglob('*') if recursive else folder.iterdir()
    files = sorted((p for p in paths if p.is_file() and p.suffix.casefold() == '.pdf'),
                   key=lambda p: p.relative_to(folder).as_posix().casefold())
    if not files:
        raise ValueError('no_pdf_files: ' + str(folder))
    return files


def build_folder(folder, config, output, *, recursive=True, dictionary_folder=None,
                 check=False, resume=False, client=None, store=None, progress=None):
    """Publish only fully built documents; per-file errors never become graph successes."""
    config.validate_local()
    output = Path(output).resolve()
    roots = [('middle', Path(folder).resolve())]
    if dictionary_folder:
        roots.append(('bottom', Path(dictionary_folder).resolve()))
    files = [(layer, root, p) for layer, root in roots for p in scan_pdfs(root, recursive)]
    # Reject self-ingestion of snapshots on subsequent --resume scans.
    if any(output == root or output.is_relative_to(root) for _, root in roots):
        raise ValueError('output_must_be_outside_input_pdf_folders')
    output.mkdir(parents=True, exist_ok=resume)
    (output / 'sources').mkdir(exist_ok=True)
    (output / 'graphs').mkdir(exist_ok=True)
    if not check:
        write_json(output / 'knowledge_manifest.json', {'version': 'building', 'corpus_mode': 'pdf_folder',
                                                       'documents': []})
    implementation_source = Path(__file__).with_name('pipeline.py').read_text(encoding='utf-8')
    if not check:
        (output / 'graph_implementation.py').write_text(implementation_source, encoding='utf-8')
    config = config.model_copy(deep=True)
    if not config.knowledge_cache:
        config.knowledge_cache = str(output / 'cache')
    started = time.monotonic()
    run_id = uuid.uuid4().hex
    summary = {'run_id': run_id, 'status': 'preparing', 'check_only': check,
               'recursive': recursive, 'folders': {layer: str(root) for layer, root in roots},
               'config': config.model_dump(), 'files': [], 'models': None,
               'limitations': ['PDF-only input does not supply UMLS or an independent dictionary layer.',
                               'Text extraction is not visual/layout validation; scanned/empty pages require OCR/review.']}
    rows, parsed, seen = summary['files'], [], {}
    graphs, published, refs = [], [], {}
    own_store = store is None

    def checkpoint():
        summary['seconds'] = round(time.monotonic() - started, 3)
        summary['counts'] = {state: sum(r['status'] == state for r in rows) for state in
                             ('parsed', 'built', 'duplicate', 'failed')}
        if client:
            summary['usage'] = {**client.usage, 'generation_calls': client.calls,
                                'embedding_calls': client.embedding_calls}
        write_json(output / 'summary.json', summary)
        if progress:
            progress(summary)

    try:
        for layer, root, file in files:
            row = {'file': str(file), 'relative_path': file.relative_to(root).as_posix(),
                   'layer': layer, 'status': 'preparing'}
            rows.append(row)
            try:
                checksum = hashlib.sha256(file.read_bytes()).hexdigest()
                row['sha256'] = checksum
                if (layer, checksum) in seen:
                    row.update(status='duplicate', duplicate_of=seen[layer, checksum])
                    checkpoint()
                    continue
                identifier = layer + '-' + checksum
                snapshot = output / 'sources' / (identifier + '.pdf')
                if not snapshot.exists():
                    shutil.copyfile(file, snapshot)
                if hashlib.sha256(snapshot.read_bytes()).hexdigest() != checksum:
                    raise ValueError('source_snapshot_hash_mismatch')
                doc = {'id': identifier, 'layer': layer, 'path': snapshot.relative_to(output).as_posix(),
                       'source': str(file), 'title': file.stem,
                       'license_note': 'User-supplied local PDF; permission not independently verified.'}
                document = parse_document(doc, output, config.chunk_chars, 'folder-pending')
                row.update(status='parsed', document_id=identifier,
                           pages=len({c['page'] for c in document['chunks']}), chunks=len(document['chunks']))
                seen[layer, checksum] = str(file)
                parsed.append((row, doc, document))
            except Exception as exc:
                row.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)})
            checkpoint()
        version = 'pdf-folder-' + digest([(r.get('sha256'), r['layer']) for r, _, _ in parsed])[:24]
        summary['knowledge_version'] = version
        for _, _, document in parsed:
            for chunk in document['chunks']:
                chunk['knowledge_version'] = version
        if check:
            write_json(output / 'prepared_manifest.json', {'version': version, 'corpus_mode': 'pdf_folder',
                                                           'documents': [d for _, d, _ in parsed]})
            summary['status'] = 'checked' if parsed and not any(r['status'] == 'failed' for r in rows) else 'partial' if parsed else 'failed'
            checkpoint()
            return summary
        if parsed:
            client = client or LocalModel(config, output)
            summary['models'] = client.preflight() if hasattr(client, 'preflight') else None
            store = store or (Neo4jStore(config) if config.graph_backend == 'neo4j' else JsonGraphStore(output))
        summary['status'] = 'building'
        checkpoint()
        for row, doc, document in parsed:
            try:
                graph, artifact = knowledge_graph(client, document, config, output)
                store.save(run_id, graph)
                artifact_path = output / 'graphs' / (doc['id'] + '.json')
                write_json(artifact_path, artifact)
                published.append({**doc, 'prebuilt_graph': artifact_path.relative_to(output).as_posix()})
                graphs.append(graph)
                refs.update({c['ref']: c for c in document['chunks']})
                row.update(status='built', gid=graph['gid'], entities=len(graph['entities']),
                           relationships=len(graph['relationships']))
            except Exception as exc:
                row.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)})
            checkpoint()
        links = link_graphs(graphs, config.link_threshold)
        if store:
            store.link(run_id, links)
        write_json(output / 'knowledge_graph.json', {'run_id': run_id, 'graphs': graphs, 'links': links})
        write_json(output / 'knowledge_sources.json', refs)
        # Even an unsuccessful resume replaces the manifest with an empty, non-loadable set.
        # Old successful artifacts are never silently advertised as current successes.
        write_json(output / 'knowledge_manifest.json', {'version': version, 'corpus_mode': 'pdf_folder',
                                                       'graph_implementation_hash': digest(implementation_source),
                                                       'graph_implementation_source': 'graph_implementation.py',
                                                       'documents': published})
        failed = any(r['status'] == 'failed' for r in rows)
        summary['status'] = ('partial' if failed else 'completed') if published else 'failed'
        summary['layers'] = sorted({doc['layer'] for doc in published})
        summary['manifest'] = str(output / 'knowledge_manifest.json')
        checkpoint()
        return summary
    except Exception as exc:
        summary.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)})
        # Do not leave a previous manifest looking current after interrupted setup.
        if not check:
            write_json(output / 'knowledge_manifest.json', {'version': summary.get('knowledge_version', 'failed'),
                       'corpus_mode': 'pdf_folder', 'documents': []})
        checkpoint()
        return summary
    finally:
        if own_store and store:
            store.close()
