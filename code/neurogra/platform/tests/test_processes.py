import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from neurogra.platform.application import Application
from neurogra.platform.configuration import resolve_config
from neurogra.platform.locking import ResourceBusy
from neurogra.platform.worker import Worker


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Application(self.root, resolve_config(root=self.root, profile='patient-only'))
        case = self.app.import_case({'患者ID': 'synthetic-process', '病史': '合成资料'})
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True, 'checks': []}):
            self.job = self.app.submit(case['version_id'])

    def child(self, script, *args):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        return subprocess.Popen([sys.executable, '-c', script, str(self.root), *args], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    def test_crashed_worker_is_interrupted_and_never_implicitly_restarted(self):
        child = self.child("import sys,os;from neurogra.platform.worker import Worker;Worker(sys.argv[1],executor=lambda *a:os._exit(17)).run_once()")
        child.communicate(timeout=10)
        self.assertEqual(child.returncode, 17)
        with self.app.store.connect() as db:
            db.execute('UPDATE attempts SET lease_until=? WHERE job_id=?', (time.time() - 1, self.job['job_id']))
        self.assertEqual(self.app.store.interrupt_expired(), 1)
        self.assertFalse(Worker(self.root, executor=lambda *a: {}).run_once())
        self.assertEqual(self.app.job(self.job['job_id'])['status'], 'interrupted')
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True}):
            self.app.retry_job(self.job['job_id'])
        Worker(self.root, executor=lambda *a: {'execution_status': 'partial'}).run_once()
        job = self.app.job(self.job['job_id'])
        self.assertEqual([a['status'] for a in job['attempts']], ['interrupted', 'succeeded'])

    def test_live_process_lock_fences_manual_retry_until_process_exits(self):
        self.app.store.cancel(self.job['job_id'])
        script = "import sys,time;from pathlib import Path;from neurogra.platform.locking import exclusive\nwith exclusive(sys.argv[1],'job_'+sys.argv[2]):\n Path(sys.argv[1],'ready').write_text('ready');time.sleep(30)"
        child = self.child(script, self.job['job_id'])
        try:
            deadline = time.monotonic() + 8
            while not (self.root / 'ready').exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue((self.root / 'ready').exists())
            self.assertIsNone(child.poll())
            with self.assertRaises(ResourceBusy):
                self.app.retry_job(self.job['job_id'])
        finally:
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=5)
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True}):
            self.app.retry_job(self.job['job_id'])
        self.assertEqual(self.app.job(self.job['job_id'])['status'], 'queued')

    def test_two_processes_cannot_claim_the_same_attempt(self):
        script = "import sys,json;from neurogra.platform.store import PlatformStore;print(json.dumps(PlatformStore(sys.argv[1]).claim(sys.argv[2])))"
        first, second = self.child(script, 'one'), self.child(script, 'two')
        outputs = [first.communicate(timeout=10), second.communicate(timeout=10)]
        self.assertEqual(first.returncode, 0, outputs[0][1])
        self.assertEqual(second.returncode, 0, outputs[1][1])
        self.assertEqual(sum(json.loads(out[0]) is not None for out in outputs), 1)

    def test_idle_worker_survives_a_database_maintenance_window_and_then_drains(self):
        self.app.store.cancel(self.job['job_id'])
        child = self.child("import sys;from neurogra.platform.worker import Worker;Worker(sys.argv[1],executor=lambda *a:{'synthetic':True}).serve()")
        try:
            deadline = time.monotonic() + 8
            while not self.app.store.workers() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(self.app.store.workers())
            with self.app.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                # Exceed the real 15-second SQLite busy timeout. This reproduces
                # a large consistent snapshot, without invoking any model.
                time.sleep(17)
                self.assertIsNone(child.poll())
                db.execute('ROLLBACK')
            self.app.store.retry(self.job['job_id'])
            deadline = time.monotonic() + 8
            while self.app.store.job(self.job['job_id'])['status'] != 'succeeded' and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertEqual(self.app.store.job(self.job['job_id'])['status'], 'succeeded')
            self.app.store.stop_workers()
            output = child.communicate(timeout=8)
            self.assertEqual(child.returncode, 0, output[1])
            self.assertFalse(any(w['online'] for w in self.app.store.workers()))
        finally:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=5)
