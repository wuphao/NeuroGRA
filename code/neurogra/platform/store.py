"""Transactional application index and durable queue, separate from engine history."""
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from neurogra.clinical.utils import dumps

SCHEMA_VERSION = 1
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}


class PlatformStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / 'data/platform/platform.sqlite'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=15, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            yield db

    def migrate(self):
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError('platform_database_newer_than_application')
            if version == SCHEMA_VERSION:
                return
            # Existing versions must be backed up before any migration.
            if self.path.stat().st_size:
                backup = self.path.with_name(f'pre-migration-v{version}-{uuid.uuid4().hex}.sqlite')
                with closing(sqlite3.connect(backup)) as target:
                    db.backup(target)
            db.execute('BEGIN IMMEDIATE')
            try:
                for statement in [
                    'CREATE TABLE IF NOT EXISTS cases(case_id TEXT PRIMARY KEY, patient_number TEXT NOT NULL UNIQUE, created REAL NOT NULL)',
                    '''CREATE TABLE IF NOT EXISTS case_versions(version_id TEXT PRIMARY KEY,
                        case_id TEXT NOT NULL REFERENCES cases(case_id), checksum TEXT NOT NULL,
                        path TEXT NOT NULL, source TEXT NOT NULL, issues TEXT NOT NULL, created REAL NOT NULL,
                        UNIQUE(case_id,checksum))''',
                    '''CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                        version_id TEXT REFERENCES case_versions(version_id), config TEXT NOT NULL,
                        payload TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                        created REAL NOT NULL, updated REAL NOT NULL)''',
                    '''CREATE TABLE IF NOT EXISTS attempts(attempt_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL REFERENCES jobs(job_id), ordinal INTEGER NOT NULL,
                        mode TEXT NOT NULL, resume_run_id TEXT, status TEXT NOT NULL,
                        worker TEXT, lease_until REAL, cancel_requested INTEGER NOT NULL DEFAULT 0,
                        run_id TEXT, result TEXT, error TEXT, started REAL, finished REAL,
                        UNIQUE(job_id,ordinal))''',
                    'CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,created)',
                    '''CREATE TABLE IF NOT EXISTS artifacts(artifact_id TEXT PRIMARY KEY,
                        attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id), kind TEXT NOT NULL,
                        path TEXT NOT NULL, checksum TEXT NOT NULL, bytes INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'available', UNIQUE(attempt_id,kind))''',
                    '''CREATE TABLE IF NOT EXISTS job_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id), kind TEXT NOT NULL,
                        payload TEXT NOT NULL, created REAL NOT NULL)''',
                    '''CREATE TABLE IF NOT EXISTS resource_leases(name TEXT PRIMARY KEY,
                        owner TEXT NOT NULL, lease_until REAL NOT NULL)''',
                ]:
                    db.execute(statement)
                db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise

    def cases(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT c.*,count(v.version_id) versions,
                max(v.created) updated FROM cases c JOIN case_versions v USING(case_id)
                GROUP BY c.case_id ORDER BY updated DESC''')]

    def versions(self, case_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM case_versions WHERE case_id=? ORDER BY created DESC', (case_id,))]

    def version(self, version_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM case_versions WHERE version_id=?', (version_id,)).fetchone()
        if row is None:
            raise KeyError('case_version_not_found')
        return dict(row)

    def create_job(self, kind, version_id, config, payload, fingerprint):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute("SELECT job_id FROM jobs WHERE fingerprint=? AND status IN ('queued','running')", (fingerprint,)).fetchone()
            if existing:
                db.execute('COMMIT')
                return self.job(existing[0])
            job_id, attempt_id = uuid.uuid4().hex, uuid.uuid4().hex
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                (job_id, kind, version_id, dumps(config), dumps(payload), fingerprint, 'queued', now, now))
            db.execute('INSERT INTO attempts(attempt_id,job_id,ordinal,mode,status) VALUES(?,?,1,?,?)',
                       (attempt_id, job_id, 'new', 'queued'))
            db.execute('COMMIT')
        return self.job(job_id)

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            if row is None:
                raise KeyError('job_not_found')
            value = dict(row)
            value['attempts'] = [dict(r) for r in db.execute('SELECT * FROM attempts WHERE job_id=? ORDER BY ordinal', (job_id,))]
        return value

    def jobs(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute('SELECT job_id FROM jobs ORDER BY created DESC LIMIT 200')]
        return [self.job(i) for i in ids]

    def claim(self, worker, lease_seconds=60):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("""SELECT a.* FROM attempts a JOIN jobs j USING(job_id)
                WHERE a.status='queued' AND j.status='queued' ORDER BY j.created LIMIT 1""").fetchone()
            if row is None:
                db.execute('COMMIT')
                return None
            now = time.time()
            db.execute("UPDATE attempts SET status='running',worker=?,lease_until=?,started=? WHERE attempt_id=?",
                       (worker, now + lease_seconds, now, row['attempt_id']))
            db.execute("UPDATE jobs SET status='running',updated=? WHERE job_id=?", (now, row['job_id']))
            db.execute('COMMIT')
        return self.attempt(row['attempt_id'])

    def attempt(self, attempt_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
        if row is None:
            raise KeyError('attempt_not_found')
        return dict(row)

    def heartbeat(self, attempt_id, worker, lease_seconds=60):
        with self.connect() as db:
            updated = db.execute("UPDATE attempts SET lease_until=? WHERE attempt_id=? AND worker=? AND status='running'",
                (time.time() + lease_seconds, attempt_id, worker)).rowcount
        if not updated:
            raise RuntimeError('worker_ownership_lost')

    def bind_run(self, attempt_id, worker, run_id):
        with self.connect() as db:
            if not db.execute("UPDATE attempts SET run_id=? WHERE attempt_id=? AND worker=? AND status='running'",
                              (run_id, attempt_id, worker)).rowcount:
                raise RuntimeError('worker_ownership_lost')

    def event(self, attempt_id, kind, payload):
        with self.connect() as db:
            db.execute('INSERT INTO job_events(attempt_id,kind,payload,created) VALUES(?,?,?,?)',
                       (attempt_id, kind, dumps(payload), time.time()))

    def events(self, attempt_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM job_events WHERE attempt_id=? ORDER BY seq DESC LIMIT 100', (attempt_id,))]

    def finish(self, attempt_id, worker, status, *, result=None, error=None):
        if status not in TERMINAL:
            raise ValueError('invalid_terminal_status')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM attempts WHERE attempt_id=? AND worker=? AND status='running'", (attempt_id, worker)).fetchone()
            if row is None:
                raise RuntimeError('worker_ownership_lost')
            if row['cancel_requested']:
                status = 'cancelled'
            db.execute('UPDATE attempts SET status=?,result=?,error=?,finished=? WHERE attempt_id=?',
                       (status, dumps(result) if result is not None else None, error, time.time(), attempt_id))
            db.execute('UPDATE jobs SET status=?,updated=? WHERE job_id=?', (status, time.time(), row['job_id']))
            db.execute('COMMIT')

    def cancel(self, job_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE job_id=? ORDER BY ordinal DESC LIMIT 1', (job_id,)).fetchone()
            if row is None:
                raise KeyError('job_not_found')
            if row['status'] == 'queued':
                db.execute("UPDATE attempts SET status='cancelled',cancel_requested=1,finished=? WHERE attempt_id=?", (time.time(), row['attempt_id']))
                db.execute("UPDATE jobs SET status='cancelled',updated=? WHERE job_id=?", (time.time(), job_id))
            elif row['status'] == 'running':
                db.execute('UPDATE attempts SET cancel_requested=1 WHERE attempt_id=?', (row['attempt_id'],))
            else:
                raise ValueError('terminal_job_cannot_be_cancelled')
            db.execute('COMMIT')
        return self.job(job_id)

    def retry(self, job_id, *, resume_run_id=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE job_id=? ORDER BY ordinal DESC LIMIT 1', (job_id,)).fetchone()
            if row is None:
                raise KeyError('job_not_found')
            if row['status'] not in TERMINAL:
                raise ValueError('job_still_active')
            db.execute('INSERT INTO attempts(attempt_id,job_id,ordinal,mode,resume_run_id,status) VALUES(?,?,?,?,?,?)',
                       (uuid.uuid4().hex, job_id, row['ordinal'] + 1, 'resume' if resume_run_id else 'new', resume_run_id, 'queued'))
            db.execute("UPDATE jobs SET status='queued',updated=? WHERE job_id=?", (time.time(), job_id))
            db.execute('COMMIT')
        return self.job(job_id)

    def interrupt_expired(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT attempt_id,job_id FROM attempts WHERE status='running' AND lease_until<?", (time.time(),)).fetchall()
            for row in rows:
                db.execute("UPDATE attempts SET status='interrupted',cancel_requested=1,error='worker_lease_expired',finished=? WHERE attempt_id=?", (time.time(), row['attempt_id']))
                db.execute("UPDATE jobs SET status='interrupted',updated=? WHERE job_id=?", (time.time(), row['job_id']))
            db.execute('COMMIT')
        return len(rows)
