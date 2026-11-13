import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neurogra.clinical.storage import BudgetExceeded, RunStore
from neurogra.platform.application import Application
from neurogra.platform.configuration import resolve_config
from neurogra.platform.control import execution_control
from neurogra.platform.locking import ResourceBusy, exclusive
from neurogra.platform.worker import Worker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = resolve_config(root=self.root, profile='patient-only')
        self.app = Application(self.root, self.config)
        case = self.app.import_case({'患者ID': 'synthetic', '病史': 'memory'})
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True, 'checks': []}):
            self.job = self.app.submit(case['version_id'])

    def test_worker_persists_partial_as_successful_execution(self):
        worker = Worker(self.root, executor=lambda attempt, check: {'execution_status': 'partial'})
        self.assertTrue(worker.run_once())
        self.assertFalse(worker.run_once())
        job = self.app.job(self.job['job_id'])
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(job['attempts'][0]['result']['execution_status'], 'partial')

    def test_cancel_prevents_new_engine_budget_reservation(self):
        engine = RunStore(self.root / 'engine.sqlite')
        run_id = engine.create_run({}, {'seconds': 60, 'llm': 3})

        def cancel():
            raise BudgetExceeded('platform_cancelled')

        with execution_control(cancel, lambda *args: None):
            with self.assertRaisesRegex(BudgetExceeded, 'platform_cancelled'):
                engine.reserve(run_id, 'llm')
        self.assertEqual(engine.summary(run_id)['calls'], [])

    def test_active_process_lock_prevents_explicit_retry(self):
        self.app.store.cancel(self.job['job_id'])
        with exclusive(self.root, 'job_' + self.job['job_id']):
            with self.assertRaises(ResourceBusy):
                self.app.retry_job(self.job['job_id'])

    def test_running_cancellation_is_terminal_even_if_executor_returns(self):
        def execute(attempt, check):
            self.app.store.cancel(self.job['job_id'])
            return {'execution_status': 'partial'}
        Worker(self.root, executor=execute).run_once()
        self.assertEqual(self.app.job(self.job['job_id'])['status'], 'cancelled')

    def test_worker_errors_do_not_leak_exception_text(self):
        def execute(attempt, check):
            raise RuntimeError('secret-patient-detail')
        Worker(self.root, executor=execute).run_once()
        attempt = self.app.job(self.job['job_id'])['attempts'][0]
        self.assertEqual(attempt['status'], 'failed')
        self.assertEqual(attempt['error'], 'RuntimeError')

    def test_report_graph_and_late_artifact_recovery_reuse_completed_run(self):
        from neurogra.clinical.collaboration_store import CollaborationStore

        response = {'message': {'content': 'synthetic invalid model output'}}
        worker = Worker(self.root)
        with patch('neurogra.clinical.llm.http_json', return_value=response) as transport, \
             patch.object(worker.app, 'register_artifacts', side_effect=RuntimeError('synthetic artifact interruption')):
            worker.run_once()
        first = self.app.store.job(self.job['job_id'])['attempts'][-1]
        self.assertEqual(first['status'], 'failed')
        self.assertTrue(first['run_id'])
        engine = RunStore(self.config.resolve(self.config.database))
        graph = CollaborationStore(engine).snapshot(first['run_id'])
        self.assertIn(graph['state'], {'completed', 'partial'})
        self.assertTrue(any(row['kind'] == 'patient.read' for row in graph['tasks']))
        self.assertTrue(any(row['kind'] == 'specialist.analyze' for row in graph['tasks']))
        self.assertTrue(any(row['kind'] == 'answer.synthesize' for row in graph['tasks']))
        self.assertGreater(transport.call_count, 0)

        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True, 'checks': []}):
            self.app.retry_job(self.job['job_id'], resume=True)
        with patch('neurogra.clinical.llm.http_json', side_effect=AssertionError('must not call model')) as retried:
            Worker(self.root).run_once()
        final = self.app.job(self.job['job_id'])
        self.assertEqual(final['status'], 'succeeded')
        self.assertEqual(final['attempts'][-1]['run_id'], first['run_id'])
        self.assertTrue(final['attempts'][-1]['artifacts'])
        retried.assert_not_called()

    def test_resume_after_report_saved_before_collaboration_finalization(self):
        from neurogra.clinical.collaboration_store import CollaborationStore

        response = {'message': {'content': 'synthetic invalid model output'}}
        with patch('neurogra.clinical.llm.http_json', return_value=response), \
             patch.object(CollaborationStore, 'finish_run', side_effect=RuntimeError('synthetic final interruption')):
            Worker(self.root).run_once()
        first = self.app.store.job(self.job['job_id'])['attempts'][-1]
        self.assertEqual(first['status'], 'failed')
        engine = RunStore(self.config.resolve(self.config.database))
        self.assertTrue(engine.objects(first['run_id'], 'run_result'))
        self.assertEqual(CollaborationStore(engine).snapshot(first['run_id'])['state'], 'interrupted')
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True, 'checks': []}):
            self.app.retry_job(self.job['job_id'], resume=True)
        with patch('neurogra.clinical.llm.http_json', side_effect=AssertionError('must not call model')) as retried:
            Worker(self.root).run_once()
        final = self.app.job(self.job['job_id'])
        self.assertEqual(final['status'], 'succeeded')
        self.assertEqual(final['attempts'][-1]['run_id'], first['run_id'])
        self.assertIn(CollaborationStore(engine).snapshot(first['run_id'])['state'], {'partial', 'completed'})
        retried.assert_not_called()

    def test_report_clinical_task_cannot_commit_after_platform_lease_expires(self):
        from neurogra.clinical.collaboration_store import CollaborationStore

        original_finish = CollaborationStore.finish_task
        injected = []

        def expire_before_commit(store, fence, task_id, *args, **kwargs):
            if not injected:
                with self.app.store.connect() as db:
                    db.execute('UPDATE attempts SET lease_until=0 WHERE job_id=?', (self.job['job_id'],))
                injected.append(task_id)
            return original_finish(store, fence, task_id, *args, **kwargs)

        with patch('neurogra.clinical.llm.http_json', return_value={'message': {'content': 'invalid'}}), \
             patch.object(CollaborationStore, 'finish_task', autospec=True, side_effect=expire_before_commit):
            Worker(self.root).run_once()
        attempt = self.app.job(self.job['job_id'])['attempts'][-1]
        self.assertTrue(injected, f"attempt status={attempt['status']} error={attempt['error']}")
        self.assertEqual(attempt['status'], 'interrupted')
        engine = RunStore(self.config.resolve(self.config.database))
        snapshot = CollaborationStore(engine).snapshot(attempt['run_id'])
        self.assertFalse(any(task['result_id'] for task in snapshot['tasks']))
        self.assertFalse(engine.objects(attempt['run_id'], 'collaboration_result'))
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True, 'checks': []}):
            self.app.retry_job(self.job['job_id'], resume=True)
        with patch('neurogra.clinical.llm.http_json', return_value={'message': {'content': 'invalid'}}):
            Worker(self.root).run_once()
        final = self.app.job(self.job['job_id'])
        self.assertEqual(final['status'], 'succeeded')
        self.assertEqual(final['attempts'][-1]['run_id'], attempt['run_id'])
        self.assertIn(CollaborationStore(engine).snapshot(attempt['run_id'])['state'], {'completed', 'partial'})
