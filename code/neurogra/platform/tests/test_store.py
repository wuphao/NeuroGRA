import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from neurogra.platform.application import Application
from neurogra.platform.configuration import resolve_config
from neurogra.platform.store import PlatformStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = resolve_config(root=self.root, profile='patient-only')
        self.app = Application(self.root, self.config)

    def submit(self):
        case = self.app.import_case({'患者ID': 'synthetic', '病史': '记忆下降'})
        with patch('neurogra.platform.application.inspect_environment', return_value={'ready': True, 'checks': []}):
            return self.app.submit(case['version_id'])

    def test_input_versions_deduplicate_and_mutation_is_rejected(self):
        first = self.app.import_case({'患者ID': 'p', '病史': 'first'})
        same = self.app.import_case({'患者ID': 'p', '病史': 'first'})
        second = self.app.import_case({'患者ID': 'p', '病史': 'second'})
        self.assertEqual(first['version_id'], same['version_id'])
        self.assertNotEqual(first['version_id'], second['version_id'])
        self.assertEqual(len(self.app.store.versions(first['case_id'])), 2)
        path = self.root / self.app.store.version(first['version_id'])['path']
        path.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'checksum_mismatch'):
            self.app.patient(first['version_id'])

    def test_transactional_claim_has_only_one_winner(self):
        job = self.submit()
        with ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(lambda w: self.app.store.claim(w), ['worker1', 'worker2']))
        self.assertEqual(sum(r is not None for r in rows), 1)
        self.assertEqual(self.app.store.job(job['job_id'])['status'], 'running')

    def test_duplicate_submission_and_retry_keep_attempt_history(self):
        first, same = self.submit(), self.submit()
        self.assertEqual(first['job_id'], same['job_id'])
        claim = self.app.store.claim('worker')
        self.app.store.finish(claim['attempt_id'], 'worker', 'failed', error='test')
        retried = self.app.store.retry(first['job_id'])
        self.assertEqual(len(retried['attempts']), 2)
        self.assertEqual(retried['attempts'][0]['status'], 'failed')
        self.assertEqual(retried['attempts'][1]['mode'], 'new')

    def test_cancel_does_not_requeue_and_fences_old_worker(self):
        job = self.submit()
        claim = self.app.store.claim('worker', lease_seconds=-1)
        self.assertEqual(self.app.store.interrupt_expired(), 1)
        self.assertIsNone(self.app.store.claim('worker2'))
        with self.assertRaisesRegex(RuntimeError, 'ownership_lost'):
            self.app.store.finish(claim['attempt_id'], 'worker', 'succeeded')
        self.assertEqual(self.app.store.job(job['job_id'])['status'], 'interrupted')

    def test_artifact_ids_enforce_boundary_and_integrity(self):
        self.submit()
        claim = self.app.store.claim('worker')
        path = self.root / 'report.md'
        path.write_text('report', encoding='utf-8')
        self.app.register_artifacts(claim['attempt_id'], {'markdown': path})
        artifact = self.app.artifacts(claim['attempt_id'])[0]
        self.assertNotIn('path', artifact)
        self.assertEqual(self.app.artifact(artifact['artifact_id'])[1], path)
        path.write_text('tampered', encoding='utf-8')
        self.assertEqual(self.app.artifacts(claim['attempt_id'])[0]['status'], 'damaged')
        with self.assertRaisesRegex(ValueError, 'damaged'):
            self.app.artifact(artifact['artifact_id'])
        with self.assertRaisesRegex(ValueError, 'outside_workspace'):
            self.app.register_artifacts(claim['attempt_id'], {'other': self.root.parent / 'outside.md'})

    def test_existing_database_migration_is_idempotent(self):
        self.submit()
        reopened = PlatformStore(self.root)
        self.assertEqual(len(reopened.jobs()), 1)
