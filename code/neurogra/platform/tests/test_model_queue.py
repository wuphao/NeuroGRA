import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import BaseModel

from neurogra.clinical.llm import ModelGateway
from neurogra.clinical.storage import RunStore
from neurogra.platform.application import Application
from neurogra.platform.configuration import resolve_config
from neurogra.platform.locking import exclusive
from neurogra.platform.model_queue import ModelQueueError, model_ticket
from neurogra.platform.worker import Worker


class SyntheticAnswer(BaseModel):
    ok: bool


class ModelQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Application(self.root, resolve_config(root=self.root, profile='patient-only'))

    def state(self, state):
        deadline = time.time() + 6
        while time.time() < deadline:
            with self.app.store.connect() as db:
                count = db.execute('SELECT count(*) FROM model_wait_ticket WHERE state=?', (state,)).fetchone()[0]
            if count:
                return
            time.sleep(.03)
        self.fail(f'no ticket became {state}')

    def test_report_and_chat_respect_one_fifo_and_model_lock(self):
        order, errors = [], []

        def work(name):
            try:
                with model_ticket(self.app.store, self.root, name, name, lambda: None, deadline=time.time() + 8):
                    order.append(name)
            except Exception as error:
                errors.append(error)

        with exclusive(self.root, 'model_resource'):
            first = threading.Thread(target=work, args=('report',))
            first.start()
            self.state('claimed')
            second = threading.Thread(target=work, args=('chat',))
            second.start()
            self.state('waiting')
            self.assertEqual(order, [])
        first.join(8)
        second.join(8)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertFalse(errors)
        self.assertEqual(order, ['report', 'chat'])

    def test_lost_model_lease_cannot_finish_report(self):
        version = self.app.import_case({'患者ID': 'synthetic', '病史': '测试'})
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True, 'checks': []}):
            job = self.app.submit(version['version_id'])
        attempt = self.app.store.claim('worker')
        with self.assertRaisesRegex(RuntimeError, 'model_queue_ownership_lost'):
            with model_ticket(self.app.store, self.root, 'report', 'test', lambda: None,
                              deadline=time.time() + 5) as fence:
                with self.app.store.connect() as db:
                    db.execute('UPDATE model_wait_ticket SET lease_until=0 WHERE ticket_id=?', (fence.ticket_id,))
                self.app.store.finish(attempt['attempt_id'], 'worker', 'succeeded', model_fence=fence)
        self.assertEqual(self.app.store.job(job['job_id'])['status'], 'running')

    def test_bounded_queue_rejects_new_ticket(self):
        with self.assertRaisesRegex(ModelQueueError, 'model_queue_full'):
            with model_ticket(self.app.store, self.root, 'chat', 'limited', lambda: None,
                              deadline=time.time() + 5, max_waiters=0):
                pass

    def report_and_engine(self):
        version = self.app.import_case({'患者ID': 'synthetic', '病史': '测试'})
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True, 'checks': []}):
            job = self.app.submit(version['version_id'])
        engine = RunStore(self.root / 'synthetic-engine.sqlite')
        run_id = engine.create_run(self.app.config.model_dump(mode='json'), {'seconds': 60, 'llm': 8})
        return job, engine, run_id

    def test_report_model_calls_release_resource_for_waiting_chat(self):
        job, engine, run_id = self.report_and_engine()
        first_entered, release_first = threading.Event(), threading.Event()
        self.addCleanup(release_first.set)
        order, failures = [], []

        def transport(_url, _payload, _timeout):
            order.append('report-' + str(sum(item.startswith('report-') for item in order) + 1))
            if order[-1] == 'report-1':
                first_entered.set()
                if not release_first.wait(6):
                    raise TimeoutError('synthetic test release')
            return {'message': {'content': '{"ok":true}'}}

        def execute(_attempt, _check):
            gateway = ModelGateway(self.app.config.model, engine, run_id, transport=transport, scope='fixture')
            async def concurrent_runtime():
                await asyncio.to_thread(gateway.generate_structured, 'first', {}, SyntheticAnswer)
                await asyncio.to_thread(gateway.generate_structured, 'second', {}, SyntheticAnswer)
            asyncio.run(concurrent_runtime())
            return {'execution_status': 'completed'}

        def chat():
            try:
                with model_ticket(self.app.store, self.root, 'chat', 'parallel-chat', lambda: None,
                                  deadline=time.time() + 8):
                    order.append('chat')
            except Exception as error:
                failures.append(error)

        report_thread = threading.Thread(target=Worker(self.root, executor=execute).run_once)
        report_thread.start()
        self.addCleanup(report_thread.join, 8)
        self.assertTrue(first_entered.wait(6))
        chat_thread = threading.Thread(target=chat)
        chat_thread.start()
        self.addCleanup(chat_thread.join, 8)
        self.state('waiting')
        release_first.set()
        report_thread.join(8)
        chat_thread.join(8)
        self.assertFalse(report_thread.is_alive() or chat_thread.is_alive())
        self.assertFalse(failures)
        self.assertEqual(order, ['report-1', 'chat', 'report-2'])
        self.assertEqual(self.app.store.job(job['job_id'])['status'], 'succeeded')
        self.assertEqual(engine.summary(run_id)['calls'], [{'kind': 'llm', 'status': 'completed', 'n': 2}])

    def test_waiting_for_model_does_not_reserve_clinical_budget(self):
        job, engine, run_id = self.report_and_engine()

        def execute(_attempt, _check):
            gateway = ModelGateway(self.app.config.model, engine, run_id, scope='fixture',
                transport=lambda *_: {'message': {'content': '{"ok":true}'}})
            gateway.generate_structured('first', {}, SyntheticAnswer)
            return {'execution_status': 'completed'}

        with exclusive(self.root, 'model_resource'):
            thread = threading.Thread(target=Worker(self.root, executor=execute).run_once)
            thread.start()
            self.state('claimed')
            self.assertEqual(engine.summary(run_id)['calls'], [])
        thread.join(8)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.app.store.job(job['job_id'])['status'], 'succeeded')

    def test_cancelled_waiting_report_releases_queue_for_chat(self):
        job, engine, run_id = self.report_and_engine()
        entered, errors = [], []

        def execute(_attempt, _check):
            ModelGateway(self.app.config.model, engine, run_id, scope='fixture',
                transport=lambda *_: {'message': {'content': '{"ok":true}'}}).generate_structured(
                    'first', {}, SyntheticAnswer)
            return {'execution_status': 'completed'}

        def chat():
            try:
                with model_ticket(self.app.store, self.root, 'chat', 'after-cancellation', lambda: None,
                                  deadline=time.time() + 8):
                    entered.append('chat')
            except Exception as error:
                errors.append(error)

        report_thread = threading.Thread(target=Worker(self.root, executor=execute).run_once)
        chat_thread = threading.Thread(target=chat)
        with exclusive(self.root, 'model_resource'):
            report_thread.start()
            self.addCleanup(report_thread.join, 8)
            self.state('claimed')
            chat_thread.start()
            self.addCleanup(chat_thread.join, 8)
            self.state('waiting')
            self.app.store.cancel(job['job_id'])
            report_thread.join(8)
            self.assertFalse(report_thread.is_alive())
            self.assertEqual(engine.summary(run_id)['calls'], [])
        chat_thread.join(8)
        self.assertFalse(chat_thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(entered, ['chat'])
        self.assertEqual(self.app.store.job(job['job_id'])['status'], 'cancelled')
        with self.app.store.connect() as db:
            states = {row['owner_type']: row['state'] for row in db.execute(
                'SELECT owner_type, state FROM model_wait_ticket')}
        self.assertEqual(states, {'report': 'interrupted', 'chat': 'done'})

    def test_lost_call_ticket_is_indeterminate_and_not_committed(self):
        job, engine, run_id = self.report_and_engine()

        def transport(*_args):
            with self.app.store.connect() as db:
                db.execute("UPDATE model_wait_ticket SET lease_until=0 WHERE state='claimed'")
            return {'message': {'content': '{"ok":true}'}}

        def execute(_attempt, _check):
            ModelGateway(self.app.config.model, engine, run_id, transport=transport).generate_structured(
                'first', {}, SyntheticAnswer)
            return {'execution_status': 'completed'}

        Worker(self.root, executor=execute).run_once()
        self.assertEqual(self.app.store.job(job['job_id'])['status'], 'interrupted')
        self.assertEqual(engine.summary(run_id)['calls'], [{'kind': 'llm', 'status': 'indeterminate', 'n': 1}])
        self.assertFalse(engine.objects(run_id, 'model_response'))


if __name__ == '__main__':
    unittest.main()
