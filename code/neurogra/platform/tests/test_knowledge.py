import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neurogra_kg.storage.registry import Registry

from neurogra.platform.application import Application
from neurogra.platform.configuration import resolve_config
from neurogra.platform.knowledge import KnowledgeManager, build_readiness, kb_service
from neurogra.platform.worker import Worker


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Application(self.root, resolve_config(root=self.root, profile='kb-only'))
        self.manager = KnowledgeManager(self.app)

    def document(self, text='合成知识原文，仅用于软件测试。'):
        return self.manager.upload('synthetic.json', json.dumps({'blocks': [
            {'kind': 'paragraph', 'page': 1, 'text': text}]}, ensure_ascii=False).encode())

    def test_preview_and_build_are_durable_and_require_explicit_activation(self):
        doc = self.document()
        duplicate = self.document()
        self.assertEqual(doc['document_id'], duplicate['document_id'])
        self.assertEqual(len(self.manager.documents()), 1)
        preview = self.manager.submit([doc['document_id']], preview=True)
        Worker(self.root).run_once()
        result = self.app.job(preview['job_id'])['attempts'][-1]['result']
        self.assertEqual(result['documents'][0]['block_count'], 1)
        build = self.manager.submit([doc['document_id']], vector=False)
        Worker(self.root).run_once()
        job = self.app.job(build['job_id'])
        self.assertEqual(job['status'], 'succeeded')
        release = job['attempts'][-1]['result']['kb_release']
        self.assertIsNone(kb_service(self.app.config).active_release())
        self.manager.activate('kb', release)
        self.assertEqual(kb_service(self.app.config).active_release(), release)
        second = self.document('第二份不同的合成知识原文。')
        next_build = self.manager.submit([second['document_id']], vector=False)
        Worker(self.root).run_once()
        newer = self.app.job(next_build['job_id'])['attempts'][-1]['result']['kb_release']
        self.assertEqual(kb_service(self.app.config).active_release(), release)
        self.manager.activate('kb', newer)
        self.manager.activate('kb', release)  # Rollback is an explicit pointer change.
        self.assertEqual(kb_service(self.app.config).active_release(), release)

    def test_mutated_source_is_not_accepted_for_build(self):
        doc = self.document()
        with self.app.store.connect() as db:
            path = self.root / db.execute('SELECT path FROM documents').fetchone()[0]
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'missing_or_damaged'):
            self.manager.submit([doc['document_id']], vector=False)

    def test_identical_content_with_different_suffix_has_one_registered_file(self):
        first = self.document()
        content = next((self.root / 'data/platform/documents').iterdir()).read_bytes()
        alias = self.manager.upload('alias.pdf', content)
        self.assertEqual(first, alias)
        self.assertEqual(len(list((self.root / 'data/platform/documents').iterdir())), 1)

    def test_first_graph_build_checks_connection_without_requiring_registry(self):
        config = resolve_config(root=self.root)
        models = [{'name': config.kg.llm.model}, {'name': config.kg.embedding.model}]
        with patch('neurogra.platform.knowledge.http_json', return_value={'models': models}), \
             patch('neurogra_kg.adapters.graph.Neo4jGraphStore.probe', return_value=True) as connection:
            self.assertTrue(build_readiness(config, kb=False, kg=True)['ready'])
            connection.assert_called_once()
        self.assertFalse((self.root / 'data/kg/registry.sqlite').exists())

    def test_graph_capability_probe_consumes_the_shared_build_budget(self):
        from neurogra_kg.diagnostics import probe
        from neurogra_kg.runtime import Budget

        config = self.app.config.kg
        def execute(task, payload, schema, budget):
            budget.reserve('llm')
            return SimpleNamespace(ok=True, source_refs=[SimpleNamespace(
                block_id='probe-block', quote='非患者连通性检查', occurrence=0)])
        def embed(texts, budget):
            budget.reserve('embedding')
            return [[1., 0.], [0., 1.]]
        with patch('neurogra_kg.diagnostics.request_json', return_value={}), \
             patch('neurogra_kg.diagnostics.model_info', return_value={'digest': 'test-digest'}), \
             patch('neurogra_kg.diagnostics.OllamaLLMAdapter') as llm, \
             patch('neurogra_kg.diagnostics.OllamaEmbeddingAdapter') as embedding:
            llm.return_value.execute.side_effect = execute
            embedding.return_value.embed.side_effect = embed
            embedding.return_value.dimension = 2
            budget = Budget(30, max_llm_calls=1, max_embedding_calls=1)
            self.assertTrue(probe(config, budget=budget)['ready'])
            self.assertEqual((budget.llm_calls, budget.embedding_calls), (1, 1))
            self.assertFalse(probe(config, budget=budget)['ready'])
            self.assertEqual((budget.llm_calls, budget.embedding_calls), (1, 1))

    def test_invalid_document_never_becomes_successful_knowledge(self):
        doc = self.manager.upload('broken.json', b'{not-json}')
        job = self.manager.submit([doc['document_id']], vector=False)
        Worker(self.root).run_once()
        result = self.app.job(job['job_id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['attempts'][-1]['result']['documents'][0]['kb'], 'failed')
        self.assertIsNone(kb_service(self.app.config).active_release())

    def test_kg_registry_staging_preserves_previous_active_version(self):
        registry = Registry(self.root / 'registry.sqlite')
        try:
            registry.publish('release_a', {'version': 'a'})
            registry.publish('release_b', {'version': 'b'}, activate=False)
            self.assertEqual(registry.active_release_id(), 'release_a')
            self.assertEqual(registry.release('release_b'), {'version': 'b'})
            registry.publish('release_b', {'version': 'b'})
            self.assertEqual(registry.active_release_id(), 'release_b')
        finally:
            registry.close()
