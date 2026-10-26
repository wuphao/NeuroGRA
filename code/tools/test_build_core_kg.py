import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest

from tools.build_core_kg import matching_builds, validate_publish_inputs, skip_failed_units, publish_available
from neurogra_kg.contracts.domain import DocumentInput
from neurogra_kg.utils import atomic_json, checksum, identity


class CorpusBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.tmp.name)
        self.source = self.root / 'paper.pdf'
        self.source.write_bytes(b'source')
        self.document = DocumentInput(path=self.source, title='paper', language='en')
        self.service = SimpleNamespace(output_root=self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, runtime, status, units, source_checksum=None):
        directory = self.root / 'builds' / name
        directory.mkdir(parents=True)
        binding = identity('input', [{'checksum': source_checksum or checksum(self.source),
            'version': '1', 'title': 'paper', 'language': 'en'}])
        atomic_json(directory / 'request.json', {'binding': binding, 'runtime_binding': runtime,
                                                'request': {'corpus_version': 'v1'}})
        atomic_json(directory / 'result.json', {'status': status, 'completed_units': units})

    def test_complete_old_runtime_is_reusable_but_partial_is_not(self):
        self.put('completed', 'old', 'completed', 8)
        self.put('partial', 'old', 'partial', 100)
        found = matching_builds(self.service, self.document, 'v1', 'new')
        self.assertEqual([row[3] for row in found], ['completed'])

    def test_current_runtime_prefers_furthest_partial(self):
        self.put('early', 'new', 'running', 1)
        self.put('late', 'new', 'partial', 4)
        found = matching_builds(self.service, self.document, 'v1', 'new')
        self.assertEqual(found[0][3], 'late')

    def test_changed_source_or_corpus_is_never_reused(self):
        self.put('changed', 'new', 'completed', 4, source_checksum='other')
        self.assertEqual(matching_builds(self.service, self.document, 'v1', 'new'), [])
        self.put('same', 'new', 'completed', 4)
        self.assertEqual(matching_builds(self.service, self.document, 'v2', 'new'), [])

    def test_completed_result_precedes_partial_even_with_more_units(self):
        self.put('complete', 'new', 'completed', 4)
        self.put('partial', 'new', 'partial', 40)
        self.assertEqual(matching_builds(self.service, self.document, 'v1', 'new')[0][3], 'complete')

    def test_incomplete_build_cannot_reach_publication(self):
        self.service.inspect_build = lambda name: SimpleNamespace(status='partial')
        with self.assertRaisesRegex(ValueError, 'cannot_publish_incomplete_paper'):
            validate_publish_inputs(self.service, [{'build_id': 'b', 'file': 'paper'}], set())

    def test_empty_collection_cannot_reach_publication(self):
        with self.assertRaisesRegex(ValueError, 'eight_paper_coverage_required'):
            validate_publish_inputs(self.service, [], set())

    def test_fast_policy_skips_failed_unit_but_restores_successful_checkpoint(self):
        directory = self.root / 'builds' / 'build' / 'doc'
        directory.mkdir(parents=True)
        for name in ['bad.failed.json', 'fixed.failed.json', 'fixed.json']:
            (directory / name).write_text('{}')
        parsed = SimpleNamespace(document=SimpleNamespace(document_id='doc'))
        original = lambda p, n: iter([('bad', ['text'], None), ('fixed', ['text'], None), ('fresh', ['text'], None)])
        with patch('neurogra_kg.construction.pipeline.extraction_units', original):
            import neurogra_kg.construction.pipeline as module
            with skip_failed_units(self.service, 'build'):
                rows = list(module.extraction_units(parsed, 6000))
                self.assertEqual(rows[0], ('bad', None, 'skipped_failed_unit_by_explicit_user_policy'))
                self.assertEqual(rows[1][1], ['text'])
                self.assertEqual(rows[2][1], ['text'])
            self.assertIs(module.extraction_units, original)
        self.assertFalse((directory / 'bad.json').exists())

    def test_fast_policy_is_restored_after_exception(self):
        import neurogra_kg.construction.pipeline as module
        original = module.extraction_units
        with self.assertRaises(RuntimeError):
            with skip_failed_units(self.service, 'build'):
                raise RuntimeError('test')
        self.assertIs(module.extraction_units, original)

    def test_fast_publication_rejects_empty_verified_graph(self):
        with self.assertRaisesRegex(ValueError, 'no_verified_statements'):
            publish_available(self.service, SimpleNamespace(), [
                {'file': 'paper', 'checksum': checksum(self.source), 'build_id': 'absent', 'status': 'pending'}],
                self.root, 'test')

    def test_partial_release_preserves_failures_and_excludes_invalid_evidence(self):
        from neurogra_kg.contracts.domain import Block, Document, Entity, KnowledgeStatement, ParsedDocument, SourceRef
        from neurogra_kg.config import EmbeddingConfig
        from neurogra_kg.storage.registry import Registry
        from neurogra_kg.adapters.graph import MemoryGraphStore
        from neurogra_kg.storage.releases import load_release
        class Embedding:
            config = EmbeddingConfig(model='fixture', dimension=2)
            def model_digest(self): return 'fixture'
            def embed(self, texts, budget=None): return [[1., 0.] for _ in texts]
        self.source.write_bytes(b'X supports D.')
        block = Block(block_id='b', document_id='doc_test', page=1, order=0, kind='paragraph', text='X supports D.')
        parsed = ParsedDocument(document=Document(document_id='doc_test', checksum=checksum(self.source),
            source_path=str(self.source), snapshot_path=str(self.source), title='fixture', version='1'),
            blocks=[block], parser='fixture', parser_version='1')
        statement = KnowledgeStatement(statement_id='good',
            subject=Entity(entity_id='x', name='X', entity_type='other'), predicate='supports',
            object=Entity(entity_id='d', name='D', entity_type='other'), assertion_text=block.text,
            source_refs=[SourceRef(block_id='b', start=0, end=len(block.text), quote=block.text)], status='source_checked')
        bad = statement.model_copy(deep=True)
        bad.statement_id = 'bad'
        bad.field_evidence = {'subject': [SourceRef(block_id='b', start=0, end=3, quote='bad')]}
        rejected = statement.model_copy(deep=True)
        rejected.statement_id, rejected.status = 'rejected', 'rejected'
        directory = self.root / 'builds' / 'partial'
        (directory / 'doc_test').mkdir(parents=True)
        atomic_json(directory / 'doc_test.parsed.json', parsed.model_dump(mode='json'))
        atomic_json(directory / 'result.json', {'status': 'partial', 'current_document_units': 2})
        saved = {'statements': [s.model_dump(mode='json') for s in [statement, bad, rejected]]}
        saved['checkpoint_checksum'] = identity('checkpoint', saved)
        atomic_json(directory / 'doc_test' / 'ok.json', saved)
        atomic_json(directory / 'doc_test' / 'failed.failed.json', {'issues': ['fixture failed']})
        registry = Registry(self.root / 'registry.sqlite')
        self.service.registry = registry
        publisher = SimpleNamespace(data_root=self.root / 'data', registry=registry,
                                    embedding=Embedding(), graph=MemoryGraphStore())
        try:
            release, report = publish_available(self.service, publisher, [{'file': 'fixture',
                'checksum': checksum(self.source), 'build_id': 'partial', 'status': 'partial'}], self.root, 'test')
            statements, _, _, _ = load_release(release)
            self.assertEqual([s.statement_id for s in statements], ['good'])
            quality = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(len(quality['documents'][0]['skipped_units']), 1)
            self.assertEqual(len(quality['documents'][0]['omitted_statements']), 2)
            self.assertEqual(registry.get('build', release['build_id'])['status'], 'partial')
            self.assertEqual(json.loads((directory / 'result.json').read_text())['status'], 'partial')
        finally:
            registry.close()


if __name__ == '__main__':
    unittest.main()
