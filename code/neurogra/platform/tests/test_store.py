import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from neurogra.platform.application import Application
from neurogra.platform.configuration import resolve_config
from neurogra.platform.store import SCHEMA_VERSION, PlatformStore


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

    def test_expired_worker_cannot_renew_commit_events_or_register_artifacts(self):
        job = self.submit()
        claim = self.app.store.claim('worker', lease_seconds=-1)
        attempt = claim['attempt_id']
        path = self.root / 'synthetic-report.md'
        path.write_text('synthetic', encoding='utf-8')
        for operation in (
                lambda: self.app.store.heartbeat(attempt, 'worker'),
                lambda: self.app.store.bind_run(attempt, 'worker', 'run'),
                lambda: self.app.store.event(attempt, 'progress', {}, worker='worker'),
                lambda: self.app.register_artifacts(attempt, {'markdown': path}, worker='worker'),
                lambda: self.app.store.finish(attempt, 'worker', 'succeeded')):
            with self.assertRaisesRegex(RuntimeError, 'worker_ownership_lost'):
                operation()
        self.assertFalse(self.app.store.events(attempt))
        self.assertFalse(self.app.artifacts(attempt))
        self.app.store.interrupt_expired()
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

    def test_old_management_schema_upgrades_with_recoverable_backup(self):
        job = self.submit()
        with self.app.store.connect() as db:
            db.execute('DROP TABLE batches')
            db.execute('CREATE TABLE batches(batch_id TEXT PRIMARY KEY,name TEXT NOT NULL,created REAL NOT NULL)')
            db.execute("INSERT INTO batches VALUES('old_batch','old',0)")
            db.execute('PRAGMA user_version=2')
        upgraded = PlatformStore(self.root)
        with upgraded.connect() as db:
            self.assertEqual(db.execute('SELECT plan FROM batches').fetchone()[0], '[]')
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
        backups = list(upgraded.path.parent.glob('pre-migration-v2-*.sqlite'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as old:
            self.assertEqual(old.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertNotIn('plan', {r[1] for r in old.execute('PRAGMA table_info(batches)')})
            self.assertEqual(old.execute('SELECT job_id FROM jobs').fetchone()[0], job['job_id'])
        PlatformStore(self.root)
        self.assertEqual(len(list(upgraded.path.parent.glob('pre-migration-v2-*.sqlite'))), 1)

    def test_scoped_patients_with_same_display_number_are_separate(self):
        raw = {'患者ID': 'same-display-number', '病史': 'synthetic'}
        source = {'kind': 'rwe_workspace', 'source_id': 'source-a', 'project_id': 1, 'patient_id': '7'}
        first = self.app.import_case(raw, source=source)
        same = self.app.import_case(raw, source=source)
        other_project = self.app.import_case(raw, source={**source, 'project_id': 2})
        other_source = self.app.import_case(raw, source={**source, 'source_id': 'source-b'})
        self.assertEqual(first['version_id'], same['version_id'])
        self.assertEqual(len({first['case_id'], other_project['case_id'], other_source['case_id']}), 3)
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM patient_scope').fetchone()[0], 3)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_v3_case_uniqueness_migration_preserves_foreign_keys(self):
        legacy_root = self.root / 'legacy'
        path = legacy_root / 'data/platform/platform.sqlite'
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as db:
            db.executescript('''
                CREATE TABLE cases(case_id TEXT PRIMARY KEY,patient_number TEXT NOT NULL UNIQUE,created REAL NOT NULL);
                CREATE TABLE case_versions(version_id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES cases(case_id),
                    checksum TEXT NOT NULL,path TEXT NOT NULL,source TEXT NOT NULL,issues TEXT NOT NULL,
                    created REAL NOT NULL,UNIQUE(case_id,checksum));
                INSERT INTO cases VALUES('old_case','same',1);
                INSERT INTO case_versions VALUES('old_version','old_case','hash','old.json','{}','[]',1);
                PRAGMA user_version=3;
            ''')
        upgraded = PlatformStore(legacy_root)
        with upgraded.connect() as db:
            db.execute("INSERT INTO cases VALUES('new_case','same',2)")
            self.assertEqual(db.execute("SELECT case_id FROM case_versions WHERE version_id='old_version'").fetchone()[0], 'old_case')
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
        self.assertEqual(len(list(path.parent.glob('pre-migration-v3-*.sqlite'))), 1)
