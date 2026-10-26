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
