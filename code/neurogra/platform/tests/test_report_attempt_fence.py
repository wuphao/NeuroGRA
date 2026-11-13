import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from neurogra.clinical.collaboration_contracts import CollaborationGraph, CollaborationTask, SubjectScope
from neurogra.clinical.collaboration_store import CollaborationConflict, CollaborationStore, OwnershipLost
from neurogra.clinical.storage import BudgetExceeded, PlatformAttemptFence, RunStore
from neurogra.platform.application import Application
from neurogra.platform.configuration import resolve_config


class ReportAttemptFenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = Application(self.root, resolve_config(root=self.root, profile='patient-only'))
        case = self.app.import_case({'患者ID': 'synthetic', '病史': 'memory'})
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True, 'checks': []}):
            job = self.app.submit(case['version_id'])
        self.job_id = job['job_id']
        self.attempt = self.app.store.claim('synthetic-worker')
        self.guard = PlatformAttemptFence(self.app.store.path, self.attempt['attempt_id'],
                                          'synthetic-worker', time.time() + 60)
        self.store = RunStore(self.root / 'engine.sqlite', platform_attempt=self.guard)
        self.run_id = self.store.create_run({'purpose': 'synthetic_report'}, {'seconds': 60, 'llm': 5})
        self.app.store.bind_run(self.attempt['attempt_id'], 'synthetic-worker', self.run_id)
        self.engine = CollaborationStore(self.store)
        self.engine.create(self.run_id, SubjectScope(source_id='synthetic', project_id='1', primary_patient='1'),
                           entry_kind='report', entry_key='report:' + self.run_id, config_digest='config')
        self.fence = self.engine.claim(self.run_id, 'synthetic-worker:' + self.attempt['attempt_id'])
        node = CollaborationTask(task_id='read', kind='patient.read', objective='读取合成记录', patient_keys=['1'])
        self.engine.install_graph(self.fence, CollaborationGraph(tasks=[node]), form_keys=set(), agent_types=set())
        self.node_attempt = self.engine.claim_next(self.fence)['attempt_id']
        self.owned = self.engine.fenced_store(self.fence)

    def test_expired_platform_attempt_blocks_all_clinical_writes(self):
        call, _ = self.owned.reserve(self.run_id, 'llm')
        with self.app.store.connect() as db:
            db.execute('UPDATE attempts SET lease_until=0 WHERE attempt_id=?', (self.attempt['attempt_id'],))
        actions = [
            lambda: self.store.save(self.run_id, 'preparation', 'late', {}),
            lambda: self.owned.reserve(self.run_id, 'llm'),
            lambda: self.owned.settle(call, 'completed'),
            lambda: self.owned.save(self.run_id, 'cache', 'late', {}),
            lambda: self.owned.event(self.run_id, 'late', {}),
            lambda: self.engine.heartbeat(self.fence),
            lambda: self.engine.finish_task(self.fence, 'read', 1, self.node_attempt,
                                            status='succeeded', result={'synthetic': True}),
            lambda: self.engine.interrupt(self.fence),
        ]
        for action in actions:
            with self.subTest(action=action), self.assertRaisesRegex(BudgetExceeded, 'platform_ownership_lost'):
                action()
        plain = RunStore(self.store.path)
        self.assertFalse(plain.objects(self.run_id, 'preparation'))
        self.assertFalse(plain.objects(self.run_id, 'cache'))
        self.assertFalse(plain.objects(self.run_id, 'collaboration_result'))
        self.assertIn({'kind': 'llm', 'status': 'running', 'n': 1}, plain.summary(self.run_id)['calls'])

    def test_cancellation_blocks_a_clinical_commit(self):
        self.app.store.cancel(self.job_id)
        with self.assertRaisesRegex(BudgetExceeded, 'platform_cancelled'):
            self.engine.finish_task(self.fence, 'read', 1, self.node_attempt,
                                    status='succeeded', result={'synthetic': True})
        self.assertFalse(RunStore(self.store.path).objects(self.run_id, 'collaboration_result'))

    def test_attempt_cannot_write_a_different_clinical_run(self):
        with self.app.store.connect() as db:
            db.execute('UPDATE attempts SET run_id=? WHERE attempt_id=?', ('other-run', self.attempt['attempt_id']))
        with self.assertRaisesRegex(BudgetExceeded, 'platform_run_mismatch'):
            self.owned.save(self.run_id, 'cache', 'wrong-run', {})
        with self.assertRaisesRegex(BudgetExceeded, 'platform_run_mismatch'):
            self.engine.finish_task(self.fence, 'read', 1, self.node_attempt,
                                    status='succeeded', result={'synthetic': True})
        self.assertFalse(RunStore(self.store.path).objects(self.run_id, 'cache'))

    def test_a_live_report_attempt_cannot_be_taken_over(self):
        with self.assertRaisesRegex(CollaborationConflict, 'owner_busy'):
            self.engine.claim(self.run_id, 'different-worker:different-attempt')

    def test_platform_cancellation_cannot_overtake_guarded_write(self):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.check_platform_attempt(db)
            with self.app.store.connect() as other:
                other.execute('PRAGMA busy_timeout=50')
                with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                    other.execute('UPDATE attempts SET cancel_requested=1 WHERE attempt_id=?',
                                  (self.attempt['attempt_id'],))
            db.execute("INSERT INTO objects VALUES(?, 'test', 'before-cancel', 1, '{}')", (self.run_id,))
            self.store.commit(db)
        self.app.store.cancel(self.job_id)
        self.assertTrue(RunStore(self.store.path).objects(self.run_id, 'test'))
        with self.assertRaisesRegex(BudgetExceeded, 'platform_cancelled'):
            self.owned.save(self.run_id, 'test', 'after-cancel', {})

    def test_guard_blocks_platform_writer_in_another_process(self):
        code = '''import sqlite3, sys
db = sqlite3.connect(sys.argv[1], isolation_level=None, timeout=0.1)
try:
    db.execute("UPDATE attempts SET cancel_requested=1 WHERE attempt_id=?", (sys.argv[2],))
    print("unexpected-success")
except sqlite3.OperationalError as error:
    print(str(error))
finally:
    db.close()
'''
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.check_platform_attempt(db)
            process = subprocess.run([sys.executable, '-c', code, str(self.app.store.path),
                                      self.attempt['attempt_id']], capture_output=True, text=True, timeout=5)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn('locked', process.stdout.lower())
            self.store.commit(db)

    def test_new_attempt_reclaims_graph_and_retains_unknown_call(self):
        call, _ = self.owned.reserve(self.run_id, 'llm')
        with self.app.store.connect() as db:
            db.execute('UPDATE attempts SET lease_until=0 WHERE attempt_id=?', (self.attempt['attempt_id'],))
        self.assertEqual(self.app.store.interrupt_expired(), 1)
        self.app.store.retry(self.job_id, resume_run_id=self.run_id)
        next_attempt = self.app.store.claim('replacement-worker')
        self.app.store.bind_run(next_attempt['attempt_id'], 'replacement-worker', self.run_id)
        new_guard = PlatformAttemptFence(self.app.store.path, next_attempt['attempt_id'],
                                         'replacement-worker', time.time() + 60)
        new_store = RunStore(self.store.path, platform_attempt=new_guard)
        new_engine = CollaborationStore(new_store)
        new_fence = new_engine.claim(self.run_id, 'replacement-worker:' + next_attempt['attempt_id'])
        self.assertGreater(new_fence.epoch, self.fence.epoch)
        self.assertEqual(new_engine.snapshot(self.run_id)['tasks'][0]['state'], 'interrupted')
        self.assertIn({'kind': 'llm', 'status': 'indeterminate', 'n': 1}, new_store.summary(self.run_id)['calls'])
        with self.assertRaises((BudgetExceeded, OwnershipLost)):
            self.owned.settle(call, 'completed')
        new_engine.retry_interrupted(new_fence, 'read', 1)
        self.assertEqual(new_engine.claim_next(new_fence)['task']['task_id'], 'read')

    def test_deadline_is_checked_again_before_commit(self):
        original = self.store.check_platform_attempt
        checks = []

        def expire_after_first_check(db, **kwargs):
            original(db, **kwargs)
            checks.append(True)
            if len(checks) == 1:
                self.store.platform_attempt = replace(self.guard, deadline=time.time() - 1)

        with patch.object(self.store, 'check_platform_attempt', side_effect=expire_after_first_check):
            with self.assertRaisesRegex(BudgetExceeded, 'platform_deadline'):
                self.owned.save(self.run_id, 'cache', 'too-late', {})
        self.assertFalse(RunStore(self.store.path).objects(self.run_id, 'cache'))


if __name__ == '__main__':
    unittest.main()
