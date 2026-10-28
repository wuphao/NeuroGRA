import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from neurogra.clinical.storage import RunStore
from neurogra.platform.application import Application
from neurogra.platform.backup import Backups, restore, verify
from neurogra.platform.configuration import resolve_config
from neurogra.platform.integrity import inspect_history
from neurogra.platform.knowledge import KnowledgeManager, kb_service
from neurogra.platform.locking import ResourceBusy, exclusive
from neurogra.platform.worker import Worker


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'source'
        self.root.mkdir()
        config = resolve_config(root=self.root, profile='patient-only', overrides={
            'kg': {'graph': {'password_file': 'data/platform/local-credentials.json'}}})
        self.app = Application(self.root, config)
        self.patient = {'患者ID': 'synthetic-backup', '病史': '用于软件测试；原路径文字 ' + str(self.root)}
        self.case = self.app.import_case(self.patient)
        secret = self.root / 'data/platform/local-credentials.json'
        secret.write_text('{"password":"synthetic-secret-do-not-copy"}', encoding='utf-8')
        (self.root / '.env.rwe.local').write_text('RWE_LOGIN_PASSWORD=another-synthetic-secret', encoding='utf-8')
        self.snapshot = self.base / 'snapshot'

    def test_round_trip_relocates_metadata_preserves_sources_and_keeps_missing_history_honest(self):
        manager = KnowledgeManager(self.app)
        doc = manager.upload('synthetic.json', json.dumps({'blocks': [
            {'kind': 'paragraph', 'page': 1, 'text': '仅用于工程验收的原文。'}]}, ensure_ascii=False).encode())
        build = manager.submit([doc['document_id']], vector=False)
        Worker(self.root).run_once()
        release = self.app.job(build['job_id'])['attempts'][-1]['result']['kb_release']
        manager.activate('kb', release)
        report = self.root / 'data/clinical/reports/test.md'
        report.parent.mkdir(parents=True)
        report.write_text('# Synthetic partial report', encoding='utf-8')
        job = self.app.store.create_job('analysis', self.case['version_id'],
            self.app.config.model_dump(mode='json'), {}, 'synthetic-result')
        attempt = self.app.store.claim('fixture')
        result = {'status': 'partial', 'report_paths': {'markdown': str(report),
            'json': str(report.with_suffix('.json'))}}
        self.app.register_artifacts(attempt['attempt_id'], {'markdown': report})
        self.app.store.finish(attempt['attempt_id'], 'fixture', 'succeeded', result=result)
        engine = RunStore(self.app.config.resolve(self.app.config.database))
        run = engine.create_run(self.app.config.model_dump(mode='json'), self.app.config.budget.model_dump())
        engine.save(run, 'run_result', 'main', result)
        engine.reserve(run, 'llm')
        self.app.store.worker_seen('old-worker', 123, 'idle')
        Backups(self.app).create(self.snapshot)
        manifest = verify(self.snapshot)
        self.assertFalse(manifest['credentials_included'])
        self.assertIn('data/platform/local-credentials.json', manifest['excluded_credentials'])
        for path in self.snapshot.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'synthetic-secret-do-not-copy', path.read_bytes())
        # Recovery must work without reading any original configuration or data.
        self.root.rename(self.base / 'unavailable-original')
        target = self.base / 'restored'
        restored = restore(self.snapshot, target)
        self.assertEqual(restored['integrity'], 'passed')
        app = Application(target)
        self.assertEqual(app.patient(self.case['version_id']), self.patient)
        self.assertEqual(KnowledgeManager(app).documents()[0]['status'], 'available')
        self.assertEqual(kb_service(app.config).active_release(), release)
        self.assertFalse((target / 'data/platform/local-credentials.json').exists())
        self.assertFalse(any(w['online'] for w in app.store.workers()))
        history = inspect_history(app.config)
        self.assertEqual(history['missing'], 1)
        self.assertEqual(history['runs'][0]['execution_status'], 'partial')
        self.assertEqual({a['kind']: a['status'] for a in history['runs'][0]['artifacts']}['markdown'], 'available')
        moved_job = app.job(job['job_id'])
        self.assertEqual(moved_job['config']['project_root'], str(target))
        self.assertEqual(moved_job['attempts'][-1]['artifacts'][0]['status'], 'available')
        self.assertEqual(moved_job['attempts'][-1]['result']['report_paths']['markdown'],
                         str(target / 'data/clinical/reports/test.md'))
        with closing(sqlite3.connect(app.config.resolve(app.config.database))) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM budget_ledger').fetchone()[0], 1)
        app.store.retry(job['job_id'])
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True, 'checks': []}):
            self.assertEqual(app.submit(self.case['version_id'])['job_id'], job['job_id'])

    def test_wal_commits_are_included_and_source_remains_unchanged(self):
        path = self.root / 'data/platform/extra.sqlite'
        with closing(sqlite3.connect(path, isolation_level=None)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE evidence(value TEXT)')
            db.execute("INSERT INTO evidence VALUES('committed WAL row')")
            self.assertTrue(Path(str(path) + '-wal').exists())
            Backups(self.app).create(self.snapshot)
            copy = self.snapshot / 'files/data/platform/extra.sqlite'
            with closing(sqlite3.connect(copy)) as copied:
                self.assertEqual(copied.execute('SELECT value FROM evidence').fetchone()[0], 'committed WAL row')
            self.assertEqual(db.execute('SELECT count(*) FROM evidence').fetchone()[0], 1)

    def test_graph_release_remains_immutable_and_readable_after_workspace_relocation(self):
        from neurogra_kg.adapters.graph import MemoryGraphStore
        from neurogra_kg.runtime import Budget
        from neurogra_kg.storage.registry import Registry
        from neurogra_kg.storage.releases import create_release
        from neurogra_kg.tests.test_release_integrity import FixtureEmbedding, ReleaseIntegrityTests

        from neurogra.clinical.knowledge_adapter import service

        self.app.config.kg.graph.provider = 'memory'
        data = self.root / 'data/kg'
        registry = Registry(data / 'registry.sqlite')
        try:
            statement, parsed = ReleaseIntegrityTests().data()
            published = create_release(data, registry, 'fixture-build', [statement], [parsed],
                FixtureEmbedding(), MemoryGraphStore(), Budget(10))
        finally:
            registry.close()
        Backups(self.app).create(self.snapshot)
        self.root.rename(self.base / 'unavailable-original')
        destination = self.base / 'restored'
        restore(self.snapshot, destination)
        app = Application(destination)
        with service(app.config) as graph:
            current = graph.get_release(published['release_id'])
            self.assertEqual(current['directory'], str(destination / 'data/kg/releases' / published['release_id']))
            self.assertEqual(graph.get_source('b', published['release_id'])['block']['text'], 'X supports D.')
            self.assertEqual(graph.registry.release(published['release_id']), published)
        KnowledgeManager(app).activate('kg', published['release_id'])

    def test_active_or_queued_jobs_and_live_resource_locks_block_backup(self):
        job = self.app.store.create_job('document_preview', None, self.app.config.model_dump(mode='json'), {}, 'pending')
        with self.assertRaisesRegex(ValueError, '任务结束'):
            Backups(self.app).create(self.snapshot)
        self.assertFalse(self.snapshot.exists())
        self.app.store.cancel(job['job_id'])
        with exclusive(self.root, 'model_resource'), self.assertRaises(ResourceBusy):
            Backups(self.app).create(self.snapshot)

    def test_restore_rejects_corruption_before_creating_destination_and_never_overwrites(self):
        Backups(self.app).create(self.snapshot)
        existing = self.base / 'existing'
        existing.mkdir()
        sentinel = existing / 'keep.txt'
        sentinel.write_text('keep', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'new_unlinked'):
            restore(self.snapshot, existing)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
        manifest = verify(self.snapshot)
        damaged = self.snapshot / 'files' / manifest['files'][0]['path']
        damaged.write_bytes(b'corrupted')
        destination = self.base / 'new'
        with self.assertRaisesRegex(ValueError, 'checksum'):
            restore(self.snapshot, destination)
        self.assertFalse(destination.exists())

    def test_untrusted_relative_paths_and_nested_backup_destinations_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'inside_source_data'):
            Backups(self.app).create(self.root / 'data/platform/backup')
        Backups(self.app).create(self.snapshot)
        manifest = verify(self.snapshot)
        manifest['files'].append({'path': '../outside', 'bytes': 0, 'checksum': ''})
        (self.snapshot / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'invalid_backup_path'):
            restore(self.snapshot, self.base / 'restored')

    def test_damaged_registered_input_blocks_a_successful_snapshot(self):
        path = self.root / self.app.store.version(self.case['version_id'])['path']
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'input_missing_or_damaged'):
            Backups(self.app).create(self.snapshot)
        self.assertFalse(self.snapshot.exists())

    def test_corrupt_knowledge_cannot_be_published_as_a_valid_backup(self):
        manager = KnowledgeManager(self.app)
        doc = manager.upload('source.json', b'{"blocks":[{"kind":"paragraph","page":1,"text":"test source"}]}')
        job = manager.submit([doc['document_id']], vector=False)
        Worker(self.root).run_once()
        release = self.app.job(job['job_id'])['attempts'][-1]['result']['kb_release']
        resource = self.root / 'output/kb/releases' / release / 'resources.json'
        resource.write_text('[]', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            Backups(self.app).create(self.snapshot)
        self.assertFalse(self.snapshot.exists())
