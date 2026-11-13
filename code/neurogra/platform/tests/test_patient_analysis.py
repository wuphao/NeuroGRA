import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neurogra.clinical.storage import BudgetExceeded
from neurogra.platform.application import Application
from neurogra.platform.configuration import resolve_config
from neurogra.platform.patient_analysis import PatientAnalysis
from neurogra.platform.patient_chat import PatientChat
from neurogra.platform.patient_workspace import PatientWorkspace, WorkspaceError
from neurogra.platform.worker import Worker


class Reader:
    foreign = False
    empty = False
    score = 23

    def query(self, path, body):
        if path == '/patient/queryPatient':
            return {'list': [{'id': 1, 'patientNumber': 'synthetic-1', 'name': 'Synthetic'},
                             {'id': 2, 'patientNumber': 'synthetic-2', 'name': 'Synthetic2'}],
                    'pagination': {'total': 2}}
        if path == '/form/getFormDetailsBatch':
            return {str(i): {'fields': [{'enName': 'score', 'cnName': '总分'}]} for i in body['formIds']}
        records = [] if self.empty else [{'id': 1, 'patient_id': 999 if self.foreign else 1,
                                         '访视日期': '2026-11-05', 'score': self.score}]
        return {str(item['formId']): {'list': records if item['formId'] == 55 else [],
                    'pagination': {'total': len(records) if item['formId'] == 55 else 0}}
                for item in body['forms']}


class PatientAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Application(self.root, resolve_config(root=self.root, profile='kb-only'))
        self.reader = Reader()
        self.workspace = PatientWorkspace(self.app.config, reader_factory=lambda *args: self.reader)
        with patch('neurogra.platform.patient_workspace.obtain_token', return_value='private-token'):
            cookie = self.workspace.login('synthetic-account', 'private-password')
        self.session = self.workspace.session(cookie)
        self.started = []
        self.service = PatientAnalysis(self.app, self.workspace, starter=lambda root: self.started.append(root))
        readiness = {'ready': True, 'checks': [{'name': 'kb', 'details': {'release_id': 'synthetic-release'}}]}
        self.preflight = patch('neurogra.platform.application.inspect_environment', return_value=readiness)
        self.preflight.start()
        self.addCleanup(self.preflight.stop)

    def test_submit_imports_full_records_and_pins_patient_context(self):
        job = self.service.submit(self.session, '1')
        raw = self.app.patient(job['version_id'])
        self.assertEqual(raw['patient']['patient_number'], 'synthetic-1')
        self.assertEqual(raw['forms']['moca']['records'][0]['总分'], 23)
        self.assertEqual(job['payload']['patient_workspace']['patient_id'], '1')
        self.assertEqual(job['config']['text_knowledge_release_id'], 'synthetic-release')
        serialized = json.dumps(job)
        self.assertNotIn('private-token', serialized)
        self.assertNotIn('private-password', serialized)
        self.assertEqual(len(self.started), 1)

    def test_report_enables_both_local_sources_only_when_graph_ready(self):
        self.app.config = resolve_config(root=self.root, profile='standard')
        readiness = {'ready': True, 'checks': [
            {'name': 'kb', 'available': True, 'details': {'release_id': 'kb-release'}},
            {'name': 'kg_release', 'available': True, 'details': {'release_id': 'kg-release'}},
            {'name': 'graph', 'available': True, 'details': {}},
        ]}
        with patch('neurogra.platform.application.inspect_environment', return_value=readiness):
            job = self.service.submit(self.session, '1')
        self.assertEqual(job['payload']['patient_workspace']['profile'], 'local-kb-kg')
        self.assertTrue(job['config']['main_agent']['use_kb'])
        self.assertTrue(job['config']['main_agent']['use_kg'])
        self.assertEqual(job['config']['knowledge_release_id'], 'kg-release')

    def test_report_does_not_enable_graph_when_disabled_in_settings(self):
        readiness = {'ready': True, 'checks': [
            {'name': 'kb', 'available': True, 'details': {'release_id': 'kb-release'}},
            {'name': 'kg_release', 'available': True, 'details': {'release_id': 'kg-release'}},
            {'name': 'graph', 'available': True, 'details': {}},
        ]}
        with patch('neurogra.platform.application.inspect_environment', return_value=readiness):
            job = self.service.submit(self.session, '1')
        self.assertEqual(job['payload']['patient_workspace']['profile'], 'kb-only')
        self.assertTrue(job['config']['main_agent']['use_kb'])
        self.assertFalse(job['config']['main_agent']['use_kg'])
        self.assertTrue(all(not role['use_kg'] for role in job['config']['agents'].values()))
        self.assertNotIn('knowledge_note', job['payload']['patient_workspace'])

    def test_report_graph_fallback_preserves_per_specialist_kb_settings(self):
        config = resolve_config(root=self.root, profile='standard')
        agents = dict(config.agents)
        agents['history'] = agents['history'].model_copy(update={'use_kb': False})
        self.app.config = config.model_copy(update={'agents': agents})
        readiness = {'ready': True, 'checks': [
            {'name': 'kb', 'available': True, 'details': {'release_id': 'kb-release'}},
            {'name': 'kg_release', 'available': False}, {'name': 'graph', 'available': False},
        ]}
        with patch('neurogra.platform.application.inspect_environment', return_value=readiness):
            job = self.service.submit(self.session, '1')
        self.assertEqual(job['payload']['patient_workspace']['profile'], 'kb-only')
        self.assertFalse(job['config']['agents']['history']['use_kb'])
        self.assertFalse(job['config']['agents']['history']['use_kg'])
        self.assertTrue(job['config']['agents']['cognition']['use_kb'])
        self.assertFalse(job['config']['agents']['cognition']['use_kg'])
        self.assertIn('知识图谱未就绪', job['payload']['patient_workspace']['knowledge_note'])

    def test_active_job_is_reused_instead_of_duplicate_model_execution(self):
        first = self.service.submit(self.session, '1')
        second = self.service.submit(self.session, '1')
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual(len(self.app.store.jobs()), 1)

    def test_changed_input_does_not_reuse_active_job(self):
        first = self.service.submit(self.session, '1')
        self.reader.score = 22
        second = self.service.submit(self.session, '1')
        self.assertNotEqual(first['job_id'], second['job_id'])
        self.assertNotEqual(first['version_id'], second['version_id'])

    def test_dispatch_key_stays_idempotent_after_job_finishes(self):
        first = self.complete(self.service.submit(self.session, '1', request_key='synthetic-dispatch'))
        repeated = self.service.submit(self.session, '1', request_key='synthetic-dispatch')
        self.assertEqual(first['job_id'], repeated['job_id'])
        self.assertEqual(len(self.app.store.jobs()), 1)

    def test_report_link_recovers_when_job_finishes_before_chat_commit(self):
        chat = PatientChat(self.app, self.workspace, self.service)
        sid = chat.create(self.session, '1')['id']
        body = {'message': '综合分析这个患者', 'request_id': 'crash-after-submit',
                'capabilities': ['interactions_v1']}
        with patch.object(chat.store, 'transaction', side_effect=RuntimeError('chat_commit_interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'chat_commit_interrupted'):
                chat.send(self.session, '1', sid, body, lambda: None)
        job = self.app.store.jobs()[0]
        completed = self.complete(job)
        # The source does not claim an atomic patient snapshot, so a fresh
        # report-status query would require confirmation; this is a replay of
        # an already-dispatched request and must link its original job instead.
        self.assertEqual(self.service.report_status(self.session, '1')['status'], 'unknown')
        reply = chat.send(self.session, '1', sid, body, lambda: None)['reply']
        self.assertEqual(reply['analysis_job_id'], completed['job_id'])
        self.assertNotIn('interaction', reply)
        self.assertEqual(len(self.app.store.jobs()), 1)

    def test_report_dispatch_is_linked_when_starter_fails_after_job_commit(self):
        self.service.starter = lambda _root: (_ for _ in ()).throw(WorkspaceError('启动器暂不可用。', 503))
        chat = PatientChat(self.app, self.workspace, self.service)
        sid = chat.create(self.session, '1')['id']
        body = {'message': '综合分析这个患者', 'request_id': 'starter-interrupted',
                'capabilities': ['interactions_v1']}
        reply = chat.send(self.session, '1', sid, body, lambda: None)['reply']
        repeated = chat.send(self.session, '1', sid, body, lambda: None)['reply']
        jobs = self.app.store.jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(reply['analysis_job_id'], jobs[0]['job_id'])
        self.assertEqual(repeated['analysis_job_id'], jobs[0]['job_id'])
        self.assertIn('启动状态', reply['text'])
        self.assertEqual(jobs[0]['status'], 'queued')

    def test_report_starter_failure_does_not_expose_job_after_access_revoked(self):
        self.service.starter = lambda _root: (_ for _ in ()).throw(WorkspaceError('启动器暂不可用。', 503))
        chat = PatientChat(self.app, self.workspace, self.service)
        sid = chat.create(self.session, '1')['id']
        body = {'message': '综合分析这个患者', 'request_id': 'starter-revoked',
                'capabilities': ['interactions_v1']}
        with patch.object(self.service, 'job', side_effect=WorkspaceError('患者权限已撤销。', 403)):
            with self.assertRaises(WorkspaceError) as error:
                chat.send(self.session, '1', sid, body, lambda: None)
        self.assertEqual(error.exception.status, 403)
        self.assertEqual(len(self.app.store.jobs()), 1)
        self.assertEqual(chat.store.database().get_messages(sid), [])

    def test_report_timeout_before_dispatch_does_not_invent_a_job(self):
        chat = PatientChat(self.app, self.workspace, self.service)
        sid = chat.create(self.session, '1')['id']
        body = {'message': '综合分析这个患者', 'request_id': 'timeout-before-dispatch',
                'capabilities': ['interactions_v1']}
        with patch.object(self.service, 'submit', side_effect=TimeoutError('synthetic_timeout')):
            with self.assertRaisesRegex(TimeoutError, 'synthetic_timeout'):
                chat.send(self.session, '1', sid, body, lambda: None)
        self.assertEqual(self.app.store.jobs(), [])
        self.assertEqual(chat.store.database().get_messages(sid), [])

    def test_report_timeout_after_dispatch_reuses_the_committed_job(self):
        self.service.starter = lambda _root: (_ for _ in ()).throw(TimeoutError('synthetic_timeout'))
        chat = PatientChat(self.app, self.workspace, self.service)
        sid = chat.create(self.session, '1')['id']
        body = {'message': '综合分析这个患者', 'request_id': 'timeout-after-dispatch',
                'capabilities': ['interactions_v1']}
        reply = chat.send(self.session, '1', sid, body, lambda: None)['reply']
        self.assertEqual(len(self.app.store.jobs()), 1)
        self.assertEqual(reply['analysis_job_id'], self.app.store.jobs()[0]['job_id'])
        self.assertIn('启动状态暂未确认', reply['text'])
        self.assertNotIn('synthetic_timeout', reply['text'])

    def test_report_freshness_distinguishes_unchanged_changed_and_unknown(self):
        self.complete(self.service.submit(self.session, '1'))
        self.assertEqual(self.service.report_status(self.session, '1')['status'], 'unknown')
        self.workspace.schema.consistent_patient_snapshot = True
        self.assertEqual(self.service.report_status(self.session, '1')['status'], 'unchanged')
        self.reader.score = 22
        self.assertEqual(self.service.report_status(self.session, '1')['status'], 'changed')

    def test_partial_report_requires_confirmation_instead_of_complete_reuse(self):
        self.complete(self.service.submit(self.session, '1'), status='partial')
        self.workspace.schema.consistent_patient_snapshot = True
        self.assertEqual(self.service.report_status(self.session, '1')['status'], 'incomplete')
        chat = PatientChat(self.app, self.workspace, self.service)
        sid = chat.create(self.session, '1')['id']
        reply = chat.send(self.session, '1', sid, {'message': '综合分析这个患者',
                          'request_id': 'partial-turn', 'capabilities': ['interactions_v1']}, lambda: None)['reply']
        self.assertIn('部分完成', reply['text'])
        self.assertEqual(reply['interaction']['type'], 'confirm_report')
        self.assertNotIn('analysis_job_id', reply)

    def test_dialogue_report_confirmation_reuses_choice_and_checks_version(self):
        old = self.complete(self.service.submit(self.session, '1'))
        chat = PatientChat(self.app, self.workspace, self.service)
        sid = chat.create(self.session, '1')['id']
        def check():
            pass
        result = chat.send(self.session, '1', sid, {'message': '综合分析这个患者',
                           'request_id': 'initial-turn', 'capabilities': ['interactions_v1']}, check)
        action = result['reply']['interaction']['action_id']
        body = {'request_id': 'confirm-turn', 'action_response': {'action_id': action, 'choice': 'view_previous'}}
        chosen = chat.send(self.session, '1', sid, body, check)['reply']
        self.assertEqual(chosen['analysis_job_id'], old['job_id'])
        self.assertEqual(chat.send(self.session, '1', sid, body, check)['reply'], chosen)
        self.assertEqual(len(self.app.store.jobs()), 1)

        result = chat.send(self.session, '1', sid, {'message': '再全面分析患者',
                           'request_id': 'second-turn', 'capabilities': ['interactions_v1']}, check)
        self.reader.score = 21
        with self.assertRaisesRegex(WorkspaceError, '再次发生变化'):
            chat.send(self.session, '1', sid, {'request_id': 'confirm-two',
                      'action_response': {'action_id': result['reply']['interaction']['action_id'],
                                          'choice': 'reanalyze'}}, check)

    def test_patient_membership_checked_before_submit(self):
        with self.assertRaises(WorkspaceError):
            self.service.submit(self.session, '999')
        self.assertFalse(self.app.store.jobs())

    def test_foreign_source_record_blocks_analysis(self):
        self.workspace.snapshot(self.session)
        self.reader.foreign = True
        with self.assertRaisesRegex(WorkspaceError, '不匹配'):
            self.service.submit(self.session, '1')
        self.assertFalse(self.app.store.jobs())

    def test_empty_patient_data_does_not_start_worker(self):
        self.reader.empty = True
        with self.assertRaisesRegex(WorkspaceError, '尚无表单'):
            self.service.submit(self.session, '1')
        self.assertFalse(self.started)

    def test_cross_patient_history_and_job_are_isolated(self):
        job = self.service.submit(self.session, '1')
        self.assertFalse(self.service.history(self.session, '2')['jobs'])
        with self.assertRaises(WorkspaceError):
            self.service.job(self.session, '2', job['job_id'])

    def complete(self, job, status='completed'):
        worker = Worker(self.root)

        def execute(attempt, check):
            run_id = 'synthetic-run'
            worker.store.bind_run(attempt['attempt_id'], worker.identity, run_id)
            directory = self.root / 'synthetic' / attempt['attempt_id']
            directory.mkdir(parents=True)
            report = directory / 'report.json'
            report.write_text(json.dumps({'patient_id': 'synthetic-1', 'run_id': run_id, 'status': status}), encoding='utf-8')
            markdown = directory / 'report.md'
            markdown.write_text('# Synthetic report', encoding='utf-8')
            self.app.register_artifacts(attempt['attempt_id'], {'json': report, 'markdown': markdown})
            return {'execution_status': status, 'run_id': run_id}
        worker.executor = execute
        worker.run_once()
        return self.service.job(self.session, '1', job['job_id'])

    def test_report_artifacts_are_bound_to_patient_job_and_run(self):
        job = self.complete(self.service.submit(self.session, '1'))
        artifact = next(item for item in job['attempts'][0]['artifacts'] if item['kind'] == 'markdown')
        _, path = self.service.artifact(self.session, '1', job['job_id'], artifact['artifact_id'])
        self.assertEqual(path.read_text(encoding='utf-8'), '# Synthetic report')
        with self.assertRaises(WorkspaceError):
            self.service.artifact(self.session, '2', job['job_id'], artifact['artifact_id'])
        with self.assertRaises(WorkspaceError):
            self.service.artifact(self.session, '1', job['job_id'], 'unknown-artifact')

    def test_finished_job_can_create_new_analysis(self):
        first = self.complete(self.service.submit(self.session, '1'))
        second = self.service.submit(self.session, '1')
        self.assertNotEqual(first['job_id'], second['job_id'])
        self.assertEqual(first['version_id'], second['version_id'])

    def test_unavailable_environment_does_not_enqueue(self):
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': False, 'checks': []}):
            with self.assertRaisesRegex(WorkspaceError, '环境未就绪'):
                self.service.submit(self.session, '1')
        self.assertFalse(self.app.store.jobs())

    def test_worker_transfers_input_source_to_engine(self):
        job = self.service.submit(self.session, '1')
        observed = {}

        def stop_after_source(raw, config, store, run_id):
            observed.update(store.objects(run_id, 'rwe_source')[0]['payload'])
            raise BudgetExceeded('synthetic-stop')
        with patch('neurogra.clinical.service.prepare_run', side_effect=stop_after_source):
            Worker(self.root).run_once()
        self.assertEqual(observed['patient_id'], '1')
        self.assertEqual(observed['project_id'], 8)
        self.assertTrue(Path(observed['path']).is_file())
        self.assertTrue(observed['sha256'])
        self.assertEqual(self.app.job(job['job_id'])['status'], 'interrupted')

    def test_report_collaboration_progress_rechecks_patient_permissions(self):
        job = self.service.submit(self.session, '1')
        with patch('neurogra.clinical.llm.http_json', return_value={'message': {'content': 'synthetic invalid'}}):
            Worker(self.root).run_once()
        visible = self.service.job(self.session, '1', job['job_id'])
        self.assertEqual(visible['status'], 'succeeded')
        self.assertTrue(visible['collaboration']['tasks'])
        self.assertTrue(any(row['kind'] == 'specialist.analyze' for row in visible['collaboration']['tasks']))
        self.assertNotIn('synthetic-1', json.dumps(visible['collaboration']))
        with patch.object(self.workspace, 'authorize_forms', side_effect=WorkspaceError('无权访问', 403)):
            with self.assertRaises(WorkspaceError):
                self.service.job(self.session, '1', job['job_id'])
        run_id = visible['attempts'][-1]['run_id']
        from neurogra.clinical.storage import RunStore
        engine = RunStore(self.app.config.resolve(self.app.config.database))
        with engine.connect() as db:
            scope = json.loads(db.execute('SELECT scope FROM collaboration_runs WHERE run_id=?', (run_id,)).fetchone()[0])
            scope['primary_patient'] = '2'
            db.execute('UPDATE collaboration_runs SET scope=? WHERE run_id=?', (json.dumps(scope), run_id))
        with self.assertRaises(WorkspaceError):
            self.service.job(self.session, '1', job['job_id'])

    def test_interrupted_report_progress_does_not_show_stale_task_as_running(self):
        from neurogra.clinical.collaboration_store import CollaborationStore

        job = self.service.submit(self.session, '1')
        finish = CollaborationStore.finish_task
        expired = []

        def expire_before_finish(store, fence, task_id, *args, **kwargs):
            if not expired:
                with self.app.store.connect() as db:
                    db.execute('UPDATE attempts SET lease_until=0 WHERE job_id=?', (job['job_id'],))
                expired.append(task_id)
            return finish(store, fence, task_id, *args, **kwargs)

        with patch('neurogra.clinical.llm.http_json', return_value={'message': {'content': 'synthetic invalid'}}), \
             patch.object(CollaborationStore, 'finish_task', autospec=True, side_effect=expire_before_finish):
            Worker(self.root).run_once()
        visible = self.service.job(self.session, '1', job['job_id'])
        self.assertEqual(visible['status'], 'interrupted')
        self.assertEqual(visible['collaboration']['status'], 'interrupted')
        self.assertTrue(any(row['state'] == 'interrupted' for row in visible['collaboration']['tasks']))
        self.assertFalse(any(row['state'] == 'running' for row in visible['collaboration']['tasks']))


if __name__ == '__main__':
    unittest.main()
