import os
import tempfile
import time
import unittest
from pathlib import Path

from neurogra.platform.application import Application
from neurogra.platform.configuration import resolve_config
from neurogra.platform.lifecycle import Lifecycle


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Application(self.root, resolve_config(root=self.root, profile='patient-only'))
        self.lifecycle = Lifecycle(self.app)
        job = self.app.store.create_job('document_preview', None, {}, {}, 'test')
        attempt = self.app.store.claim('fixture')
        self.attempt_id = attempt['attempt_id']
        self.app.store.finish(self.attempt_id, 'fixture', 'succeeded')
        with self.app.store.connect() as db:
            db.execute('UPDATE attempts SET finished=? WHERE attempt_id=?', (time.time() - 40 * 86400, self.attempt_id))
        self.directory = self.root / 'data/platform/runs' / self.attempt_id
        self.directory.mkdir(parents=True)
        self.old = self.directory / 'llm_calls.jsonl.1'
        self.old.write_text('old debug log', encoding='utf-8')
        os.utime(self.old, (time.time() - 40 * 86400,) * 2)
        self.current = self.directory / 'llm_calls.jsonl'
        self.current.write_text('current audit', encoding='utf-8')
        self.assertEqual(self.app.store.job(job['job_id'])['status'], 'succeeded')

    def test_preview_is_read_only_and_only_registered_terminal_attempt_logs_are_eligible(self):
        arbitrary = self.root / 'unmanaged' / 'llm_calls.jsonl.1'
        arbitrary.parent.mkdir()
        arbitrary.write_text('keep', encoding='utf-8')
        plan = self.lifecycle.preview()
        self.assertEqual(len(plan['candidates']), 1)
        self.assertTrue(self.old.exists())
        result = self.lifecycle.apply(plan)
        self.assertEqual(result['bytes'], len('old debug log'))
        self.assertFalse(self.old.exists())
        self.assertTrue(self.current.exists())
        self.assertTrue(arbitrary.exists())

    def test_changed_or_forged_plan_cannot_delete_files(self):
        plan = self.lifecycle.preview()
        self.old.write_text('modified', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.lifecycle.apply(plan)
        self.assertTrue(self.old.exists())
        plan = self.lifecycle.preview()
        plan['candidates'] = [{'path': 'data/platform/platform.sqlite'}]
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.lifecycle.apply(plan)
        self.assertTrue(self.app.store.path.exists())

    def test_active_jobs_and_registered_artifacts_are_protected(self):
        plan = self.lifecycle.preview()
        job = self.app.store.create_job('document_preview', None, {}, {}, 'active')
        with self.assertRaisesRegex(ValueError, '任务结束'):
            self.lifecycle.apply(plan)
        self.app.store.cancel(job['job_id'])
        self.app.register_artifacts(self.attempt_id, {'debug_evidence': self.old})
        self.assertEqual(self.lifecycle.preview()['candidates'], [])
        self.assertTrue(self.old.exists())

    def test_inventory_excludes_unmanaged_files_and_counts_overlapping_roots_once(self):
        unmanaged = self.root / 'study' / 'notes.txt'
        unmanaged.parent.mkdir()
        unmanaged.write_text('x' * 40000, encoding='utf-8')
        before = self.lifecycle.inventory()
        report = self.root / 'data/clinical/reports/report.md'
        report.parent.mkdir(parents=True)
        report.write_bytes(b'example-report')
        after = self.lifecycle.inventory()
        self.assertEqual(after['bytes'] - before['bytes'], len(b'example-report'))
        self.assertFalse(after['unmanaged_directories_included'])
