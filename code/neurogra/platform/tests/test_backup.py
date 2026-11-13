import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

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

    def test_restore_preserves_completed_dialogue_binding_and_rejects_stale_interrupted_run(self):
        from neurogra.clinical.collaboration_store import CollaborationStore
        from neurogra.clinical.schemas import AgentResult, Claim
        from neurogra.platform.patient_agents import DialoguePlan
        from neurogra.platform.patient_analysis import PatientAnalysis
        from neurogra.platform.patient_chat import PatientChat
        from neurogra.platform.patient_workspace import PatientWorkspace, WorkspaceError
        from neurogra.platform.tests.test_patient_analysis import Reader

        workspace = PatientWorkspace(self.app.config, reader_factory=lambda *_: Reader())
        with patch('neurogra.platform.patient_workspace.obtain_token', return_value='synthetic-token'):
            cookie = workspace.login('synthetic-account', 'synthetic-password')
        session = workspace.session(cookie)
        chat = PatientChat(self.app, workspace, PatientAnalysis(self.app, workspace, starter=lambda _: None))
        completed_sid = chat.create(session, '1')['id']
        interrupted_sid = chat.create(session, '1')['id']
        body = {'message': '解释这个患者的 MoCA 结果', 'request_id': 'backup-request', 'capabilities': []}
        plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)

        async def specialist(task_plan, snapshot, **_kwargs):
            task = task_plan.tasks[0]
            observation = next(item for item in snapshot.observations if item.name == '总分')
            return [AgentResult(result_id='result', task_id=task.task_id, case_version=snapshot.version,
                status='completed', claims=[Claim(claim_id='fact', text='总分 23', kind='description',
                    level='cognition', observation_ids=[observation.observation_id], strength='descriptive')])]

        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
             patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                   side_effect=specialist):
            with patch.object(chat.store, 'transaction', side_effect=RuntimeError('before_chat_commit')):
                with self.assertRaisesRegex(RuntimeError, 'before_chat_commit'):
                    chat.send(session, '1', completed_sid, body, Mock())
            with patch.object(CollaborationStore, 'finish_task', side_effect=RuntimeError('before_task_commit')):
                with self.assertRaisesRegex(RuntimeError, 'before_task_commit'):
                    chat.send(session, '1', interrupted_sid, body, Mock())
        completed_binding = chat.store.database().collaboration_binding(completed_sid, 'backup-request')
        interrupted_binding = chat.store.database().collaboration_binding(interrupted_sid, 'backup-request')
        engine = RunStore(self.app.config.resolve(self.app.config.database))
        self.assertTrue(engine.objects(completed_binding['run_id'], 'collaboration_final'))
        self.assertFalse(engine.objects(interrupted_binding['run_id'], 'collaboration_final'))
        followup_id = engine.create_run({'project_root': str(self.root), 'followup': {'report': 'synthetic'}},
                                        {'seconds': 60})

        Backups(self.app).create(self.snapshot)
        self.root.rename(self.base / 'unavailable-original')
        target = self.base / 'restored-dialogue'
        restore(self.snapshot, target)
        restored_app = Application(target)
        restored_engine = RunStore(restored_app.config.resolve(restored_app.config.database))
        followup = json.loads(restored_engine.run(followup_id)['config'])
        self.assertEqual(followup, {'project_root': str(target), 'followup': {'report': 'synthetic'}})
        restored_workspace = PatientWorkspace(restored_app.config, reader_factory=lambda *_: Reader())
        with patch('neurogra.platform.patient_workspace.obtain_token', return_value='synthetic-token'):
            restored_cookie = restored_workspace.login('synthetic-account', 'synthetic-password')
        restored_session = restored_workspace.session(restored_cookie)
        restored_chat = PatientChat(restored_app, restored_workspace,
                                    PatientAnalysis(restored_app, restored_workspace, starter=lambda _: None))
        self.assertIn(completed_sid, {item['id'] for item in restored_chat.sessions(restored_session, '1')['sessions']})
        with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured',
                   side_effect=AssertionError('restored result must not call the model')):
            reply = restored_chat.send(restored_session, '1', completed_sid, body, Mock())['reply']
            self.assertIn('总分：23', reply['text'])
            with self.assertRaisesRegex(WorkspaceError, '已变化'):
                restored_chat.send(restored_session, '1', interrupted_sid, body, Mock(), resume_collaboration=True)
        messages = restored_chat.store.database().get_messages(completed_sid)
        self.assertEqual(sum(item['role'] == 'assistant' for item in messages), 1)
        self.assertEqual(restored_chat.store.database().collaboration_binding(completed_sid, 'backup-request')['run_id'],
                         completed_binding['run_id'])

    def test_restored_retry_deduplicates_patient_scoped_report_with_same_context(self):
        context = {'source_id': 'synthetic-rwe', 'project_id': 1, 'patient_id': '1',
                   'patient_number': 'synthetic-backup', 'profile': 'patient-only'}
        ready = {'ready': True, 'checks': []}
        with patch('neurogra.platform.application.inspect_environment', return_value=ready):
            job = self.app.submit(self.case['version_id'], context=context)
        attempt = self.app.store.claim('fixture')
        self.app.store.finish(attempt['attempt_id'], 'fixture', 'succeeded')
        Backups(self.app).create(self.snapshot)
        self.root.rename(self.base / 'unavailable-original')
        target = self.base / 'restored-report'
        restore(self.snapshot, target)
        restored_app = Application(target)
        restored_app.store.retry(job['job_id'])
        with patch('neurogra.platform.application.inspect_environment', return_value=ready):
            reused = restored_app.submit(self.case['version_id'], context=context)
        self.assertEqual(reused['job_id'], job['job_id'])
        self.assertEqual(len(restored_app.store.jobs()), 1)

    def test_mixed_graph_backup_preserves_state_and_rejects_relocated_resume(self):
        from neurogra.clinical.collaboration_contracts import SubjectScope
        from neurogra.clinical.collaboration_store import CollaborationStore
        from neurogra.platform.mixed_collaboration import MixedCollaboration, mixed_run_id
        from neurogra.platform.patient_workspace import WorkspaceError

        scope = SubjectScope(source_id='synthetic', project_id='1', primary_patient='1')
        branches = {'report': {'kind': 'report.request', 'question': '合成报告', 'patient_keys': ['1']}}
        options = dict(allowed_forms=set(), binding='synthetic-binding', execution_digest='synthetic-version',
                       check=lambda: None, resume=False)
        completed_id, interrupted_id = 'a1' * 16, 'a2' * 16
        with MixedCollaboration(self.app.config, completed_id, scope, branches, **options) as run:
            child_store = RunStore(run.store.path, parent_fence=run.fence)
            child_store.create_run({'project_root': str(self.root), 'purpose': 'patient_dialogue',
                                  'budget_parent_run_id': run.run_id},
                                 {'seconds': 60, 'patient_data': 2}, run_id=completed_id)
            receipt, _ = child_store.reserve(completed_id, 'patient_data')
            child_store.settle(receipt, 'completed')
            run.run('report', lambda: {'job': 'synthetic'})
            run.finish({'answer': 'synthetic'})
        with self.assertRaisesRegex(RuntimeError, 'synthetic_interruption'):
            with MixedCollaboration(self.app.config, interrupted_id, scope, branches, **options):
                raise RuntimeError('synthetic_interruption')
        Backups(self.app).create(self.snapshot)
        target = self.base / 'restored-mixed'
        restore(self.snapshot, target)
        app = Application(target)
        engine = RunStore(app.config.resolve(app.config.database))
        for child_id, expected in ((completed_id, 'completed'), (interrupted_id, 'interrupted')):
            parent_id = mixed_run_id(child_id)
            self.assertEqual(json.loads(engine.run(parent_id)['config'])['project_root'], str(target))
            self.assertEqual(CollaborationStore(engine).snapshot(parent_id)['state'], expected)
            with self.assertRaisesRegex(WorkspaceError, '已变化'):
                with MixedCollaboration(app.config, child_id, scope, branches, **{**options, 'resume': True}):
                    pass
        self.assertEqual(engine.budget_parent(completed_id), mixed_run_id(completed_id))
        self.assertIn({'kind': 'patient_data', 'status': 'completed', 'n': 1},
                      engine.summary(mixed_run_id(completed_id))['aggregate_calls'])

    def test_legacy_patient_session_survives_repeated_relocation_without_cross_account_access(self):
        from neurogra.platform.patient_analysis import PatientAnalysis
        from neurogra.platform.patient_chat import PatientChat
        from neurogra.platform.patient_workspace import PatientWorkspace, WorkspaceError
        from neurogra.platform.tests.test_patient_analysis import Reader

        def open_chat(app, account):
            workspace = PatientWorkspace(app.config, reader_factory=lambda *_: Reader())
            with patch('neurogra.platform.patient_workspace.obtain_token', return_value='synthetic-token'):
                cookie = workspace.login(account, 'synthetic-password')
            return PatientChat(app, workspace, PatientAnalysis(app, workspace, starter=lambda _: None)), workspace.session(cookie)

        chat, session = open_chat(self.app, 'synthetic-account')
        scope, _ = chat.scope(session, '1')
        database = chat.store.database()
        database.create_session('legacy-session', scope.legacy, model='synthetic-model')
        database.append_message('legacy-session', 'user', '合成历史问题')
        database.append_message('legacy-session', 'assistant', '合成历史答复')
        Backups(self.app).create(self.snapshot)
        first = self.base / 'first-restore'
        restore(self.snapshot, first)
        first_app = Application(first)
        first_chat, first_session = open_chat(first_app, 'synthetic-account')
        self.assertIn('legacy-session', {row['id'] for row in first_chat.sessions(first_session, '1')['sessions']})
        self.assertEqual(first_chat.messages(first_session, '1', 'legacy-session')['messages'][-1]['text'], '合成历史答复')
        other_chat, other_session = open_chat(first_app, 'different-account')
        self.assertNotIn('legacy-session', {row['id'] for row in other_chat.sessions(other_session, '1')['sessions']})
        with self.assertRaises(WorkspaceError):
            other_chat.messages(other_session, '1', 'legacy-session')
        self.assertNotIn('legacy-session', {row['id'] for row in first_chat.sessions(first_session, '2')['sessions']})
        with self.assertRaises(WorkspaceError):
            first_chat.messages(first_session, '2', 'legacy-session')

        second_backup = self.base / 'second-snapshot'
        Backups(first_app).create(second_backup)
        second = self.base / 'second-restore'
        restore(second_backup, second)
        second_chat, second_session = open_chat(Application(second), 'synthetic-account')
        self.assertIn('legacy-session', {row['id'] for row in second_chat.sessions(second_session, '1')['sessions']})
        self.assertEqual(second_chat.messages(second_session, '1', 'legacy-session')['messages'][-1]['text'], '合成历史答复')

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

    def test_restore_copy_failure_keeps_destination_absent_and_snapshot_retryable(self):
        import shutil

        Backups(self.app).create(self.snapshot)
        original_copy = shutil.copyfile
        copied = 0

        def fail_after_one(*args, **kwargs):
            nonlocal copied
            copied += 1
            if copied == 2:
                raise OSError('synthetic_copy_interruption')
            return original_copy(*args, **kwargs)

        destination = self.base / 'restore-after-failure'
        with patch('neurogra.platform.backup.shutil.copyfile', side_effect=fail_after_one):
            with self.assertRaisesRegex(OSError, 'synthetic_copy_interruption'):
                restore(self.snapshot, destination)
        self.assertFalse(destination.exists())
        self.assertTrue(list(self.base.glob('restore-after-failure.partial-*')))
        self.assertEqual(verify(self.snapshot)['schema_version'], 1)
        self.assertEqual(restore(self.snapshot, destination)['integrity'], 'passed')
        self.assertEqual(Application(destination).patient(self.case['version_id']), self.patient)

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
