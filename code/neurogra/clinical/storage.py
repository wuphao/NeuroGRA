"""Per-operation SQLite connections; transactional budgets survive process restarts."""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass
from .utils import dumps


class BudgetExceeded(RuntimeError):
    pass


class VersionConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class PlatformAttemptFence:
    """Ephemeral authority for a report worker; never serialized in a run."""
    path: Path
    attempt_id: str
    worker: str
    deadline: float


class RunStore:
    def __init__(self, path: Path, *, parent_fence=None, revalidate_parent_id=None, platform_attempt=None):
        if parent_fence is not None and revalidate_parent_id is not None:
            raise ValueError('ambiguous_parent_guard')
        self.path = path
        self.parent_fence = parent_fence
        self.revalidate_parent_id = revalidate_parent_id
        self.platform_attempt = None
        self._revalidation_receipts = set()
        self._revalidation_lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS runs(
              run_id TEXT PRIMARY KEY, config TEXT NOT NULL, deadline REAL NOT NULL,
              limits TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS budget_ledger(
              call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL,
              status TEXT NOT NULL, started REAL NOT NULL, finished REAL, usage TEXT);
            CREATE TABLE IF NOT EXISTS budget_links(
              child_run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
              parent_run_id TEXT NOT NULL REFERENCES runs(run_id));
            CREATE INDEX IF NOT EXISTS budget_links_parent ON budget_links(parent_run_id);
            CREATE INDEX IF NOT EXISTS budget_ledger_run_kind ON budget_ledger(run_id,kind);
            CREATE TABLE IF NOT EXISTS objects(
              run_id TEXT NOT NULL, kind TEXT NOT NULL, object_id TEXT NOT NULL,
              version INTEGER NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(run_id,kind,object_id,version));
            CREATE TABLE IF NOT EXISTS events(
              seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
              type TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(budget_ledger)")}
            if "scope" not in columns:
                db.execute("ALTER TABLE budget_ledger ADD COLUMN scope TEXT")
        if platform_attempt is not None:
            if (not isinstance(platform_attempt, PlatformAttemptFence) or not platform_attempt.attempt_id or
                    not platform_attempt.worker or not platform_attempt.path.is_file() or
                    platform_attempt.path.resolve() == path.resolve()):
                raise ValueError('invalid_platform_attempt_fence')
            self.platform_attempt = platform_attempt

    @contextmanager
    def connect(self):
        # isolation_level=None: explicit transactions below, no cross-thread connections.
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if self.platform_attempt is not None:
                db.execute('ATTACH DATABASE ? AS platform_guard', (str(self.platform_attempt.path),))
            yield db
        finally:
            db.close()

    def check_platform_attempt(self, db, *, run_id=None):
        """Check the platform lease while BEGIN IMMEDIATE holds both writer locks."""
        fence = self.platform_attempt
        if fence is None:
            return
        if not db.in_transaction:
            raise RuntimeError('platform_fence_requires_transaction')
        row = db.execute('''SELECT status,worker,lease_until,cancel_requested,run_id FROM platform_guard.attempts
                            WHERE attempt_id=?''', (fence.attempt_id,)).fetchone()
        now = time.time()
        if (row is None or row['status'] != 'running' or row['worker'] != fence.worker or
                not row['lease_until'] or row['lease_until'] <= now):
            raise BudgetExceeded('platform_ownership_lost')
        if row['cancel_requested']:
            raise BudgetExceeded('platform_cancelled')
        if now >= fence.deadline:
            raise BudgetExceeded('platform_deadline')
        if run_id is not None and row['run_id'] != run_id:
            raise BudgetExceeded('platform_run_mismatch')

    def commit(self, db):
        self.check_platform_attempt(db)
        db.execute('COMMIT')

    def create_run(self, config: dict, budget: dict, *, run_id=None) -> str:
        from .collaboration_store import OwnershipLost
        run_id = run_id or uuid.uuid4().hex
        parent_id = config.get('budget_parent_run_id')
        if self.revalidate_parent_id is not None:
            raise OwnershipLost('revalidation_read_only')
        if self.parent_fence is not None and parent_id is None:
            raise OwnershipLost('parent_guard_run_mismatch')
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self.check_platform_attempt(db)
                if parent_id is not None:
                    if not isinstance(parent_id, str) or parent_id == run_id:
                        raise ValueError('invalid_budget_parent_binding')
                    parent = db.execute("SELECT config FROM runs WHERE run_id=?", (parent_id,)).fetchone()
                    parent_config = json.loads(parent['config']) if parent else {}
                    if (config.get('purpose') != 'patient_dialogue' or
                            parent_config.get('purpose') != 'mixed_dialogue' or
                            parent_config.get('budget_child_run_id') != run_id or
                            db.execute('SELECT 1 FROM budget_links WHERE child_run_id=?', (parent_id,)).fetchone()):
                        raise ValueError('invalid_budget_parent_binding')
                    self._check_parent(db, parent_id)
                db.execute("INSERT INTO runs VALUES(?,?,?,?,?)",
                           (run_id, dumps(config), time.time() + budget["seconds"],
                            dumps({k: budget.get(k, 0) for k in ("llm", "retrieval", "image", "patient_data", "embedding")}), "received"))
                if parent_id is not None:
                    db.execute('INSERT INTO budget_links(child_run_id,parent_run_id) VALUES(?,?)', (run_id, parent_id))
                self.commit(db)
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return run_id

    def run(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("unknown run")
        return dict(row)

    def budget_parent(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT parent_run_id FROM budget_links WHERE child_run_id=?', (run_id,)).fetchone()
        return row['parent_run_id'] if row else None

    def _check_parent(self, db, parent_id, *, allow_revalidation=False):
        """Require the active parent owner, except for terminal source checks."""
        from .collaboration_store import CollaborationFence, CollaborationStore, OwnershipLost

        if self.parent_fence is not None:
            if not isinstance(self.parent_fence, CollaborationFence) or self.parent_fence.run_id != parent_id:
                raise OwnershipLost('parent_fence_mismatch')
            CollaborationStore._check_fence(db, self.parent_fence)
            return self.parent_fence
        if allow_revalidation and self.revalidate_parent_id == parent_id:
            row = db.execute('SELECT state FROM collaboration_runs WHERE run_id=?', (parent_id,)).fetchone()
            if row is not None and row['state'] in {'completed', 'partial'}:
                return None
        raise OwnershipLost('parent_fence_required')

    def check_parent_guard(self, db, run_id, *, allow_revalidation=False):
        """Check a linked child's parent in the current write transaction."""
        from .collaboration_store import OwnershipLost

        self.check_platform_attempt(db, run_id=run_id)
        row = db.execute('SELECT config FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('unknown run')
        config = json.loads(row['config'])
        parent_id = config.get('budget_parent_run_id')
        link = db.execute('SELECT parent_run_id FROM budget_links WHERE child_run_id=?', (run_id,)).fetchone()
        if parent_id != (link['parent_run_id'] if link else None):
            raise OwnershipLost('budget_parent_binding_changed')
        if self.revalidate_parent_id is not None and not allow_revalidation:
            raise OwnershipLost('revalidation_read_only')
        if parent_id is None:
            if self.parent_fence is not None or self.revalidate_parent_id is not None:
                raise OwnershipLost('parent_guard_run_mismatch')
            return None
        parent = db.execute('SELECT config FROM runs WHERE run_id=?', (parent_id,)).fetchone()
        parent_config = json.loads(parent['config']) if parent else {}
        if (config.get('purpose') != 'patient_dialogue' or
                parent_config.get('purpose') != 'mixed_dialogue' or
                parent_config.get('budget_child_run_id') != run_id):
            raise OwnershipLost('budget_parent_binding_changed')
        return self._check_parent(db, parent_id, allow_revalidation=allow_revalidation)

    def reserve(self, run_id: str, kind: str, scope=None, scope_limit=None, reserved=0) -> tuple[str, float]:
        from neurogra.platform.control import check_cancelled
        from .collaboration_store import OwnershipLost
        check_cancelled()
        if reserved < 0:
            raise ValueError("negative_reserved_budget")
        if self.revalidate_parent_id is not None and (
                kind != 'patient_data' or not isinstance(scope, str) or
                not scope.startswith('dialogue_form:') or scope == 'dialogue_form:'):
            raise OwnershipLost('revalidation_read_only')
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if self.revalidate_parent_id is not None and self.budget_parent_in_transaction(db, run_id) != self.revalidate_parent_id:
                    raise OwnershipLost('revalidation_parent_mismatch')
                self.check_parent_guard(db, run_id, allow_revalidation=True)
                call_id, remaining = self.reserve_in_transaction(db, run_id, kind, scope, scope_limit, reserved)
                self.commit(db)
                if self.revalidate_parent_id is not None:
                    with self._revalidation_lock:
                        self._revalidation_receipts.add(call_id)
                return call_id, remaining
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def budget_parent_in_transaction(db, run_id):
        row = db.execute('SELECT parent_run_id FROM budget_links WHERE child_run_id=?', (run_id,)).fetchone()
        return row['parent_run_id'] if row else None

    @staticmethod
    def reserve_in_transaction(db, run_id, kind, scope=None, scope_limit=None, reserved=0):
        """Reserve once while checking both the local and linked parent limits."""
        if reserved < 0:
            raise ValueError('negative_reserved_budget')
        run = db.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if run is None:
            raise ValueError('unknown run')
        run_config = json.loads(run['config'])
        expected_child = run_config.get('budget_child_run_id') if run_config.get('purpose') == 'mixed_dialogue' else None
        if expected_child:
            child = db.execute('SELECT config FROM runs WHERE run_id=?', (expected_child,)).fetchone()
            if child:
                link = db.execute('SELECT parent_run_id FROM budget_links WHERE child_run_id=?',
                                  (expected_child,)).fetchone()
                if (not link or link['parent_run_id'] != run_id or
                        json.loads(child['config']).get('budget_parent_run_id') != run_id):
                    raise ValueError('budget_parent_binding_changed')
        now = time.time()
        remaining = run['deadline'] - now
        used = db.execute('''SELECT count(*) FROM budget_ledger WHERE kind=? AND
            (run_id=? OR run_id IN (SELECT child_run_id FROM budget_links WHERE parent_run_id=?))''',
            (kind, run_id, run_id)).fetchone()[0]
        if remaining <= 0 or used >= max(0, json.loads(run['limits']).get(kind, 0) - reserved):
            raise BudgetExceeded(kind)
        if scope is not None and scope_limit is not None:
            scoped = db.execute('SELECT count(*) FROM budget_ledger WHERE run_id=? AND kind=? AND scope=?',
                                (run_id, kind, scope)).fetchone()[0]
            if scoped >= scope_limit:
                raise BudgetExceeded('task_' + kind)
        parent_link = db.execute('SELECT parent_run_id FROM budget_links WHERE child_run_id=?', (run_id,)).fetchone()
        configured_parent = run_config.get('budget_parent_run_id')
        if configured_parent != (parent_link['parent_run_id'] if parent_link else None):
            raise ValueError('budget_parent_binding_changed')
        if parent_link:
            parent_id = parent_link['parent_run_id']
            parent = db.execute('SELECT * FROM runs WHERE run_id=?', (parent_id,)).fetchone()
            if parent is None:
                raise ValueError('missing_budget_parent')
            parent_config = json.loads(parent['config'])
            if (parent_config.get('purpose') != 'mixed_dialogue' or
                    parent_config.get('budget_child_run_id') != run_id):
                raise ValueError('budget_parent_binding_changed')
            remaining = min(remaining, parent['deadline'] - now)
            total = db.execute('''SELECT count(*) FROM budget_ledger WHERE kind=? AND
                (run_id=? OR run_id IN (SELECT child_run_id FROM budget_links WHERE parent_run_id=?))''',
                (kind, parent_id, parent_id)).fetchone()[0]
            if remaining <= 0 or total >= max(0, json.loads(parent['limits']).get(kind, 0) - reserved):
                raise BudgetExceeded('parent_' + kind)
        call_id = uuid.uuid4().hex
        db.execute('''INSERT INTO budget_ledger(call_id,run_id,kind,status,started,finished,usage,scope)
                      VALUES(?,?,?,?,?,?,?,?)''', (call_id, run_id, kind, 'running', now, None, None, scope))
        return call_id, remaining

    def settle(self, call_id, status, usage=None):
        from .collaboration_store import OwnershipLost
        if self.revalidate_parent_id is not None:
            with self._revalidation_lock:
                if call_id not in self._revalidation_receipts:
                    raise OwnershipLost('revalidation_receipt_mismatch')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT run_id FROM budget_ledger WHERE call_id=?', (call_id,)).fetchone()
                if row is not None:
                    self.check_parent_guard(db, row['run_id'], allow_revalidation=True)
                    db.execute("UPDATE budget_ledger SET status=?, finished=?, usage=? WHERE call_id=? AND status='running'",
                               (status, time.time(), dumps(usage or {}), call_id))
                self.commit(db)
                if self.revalidate_parent_id is not None:
                    with self._revalidation_lock:
                        self._revalidation_receipts.discard(call_id)
            except BaseException:
                db.execute('ROLLBACK')
                raise

    def mark_interrupted(self, run_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                self.check_parent_guard(db, run_id)
                db.execute("UPDATE budget_ledger SET status='indeterminate' WHERE run_id=? AND status='running'", (run_id,))
                self.commit(db)
            except BaseException:
                db.execute('ROLLBACK')
                raise

    def status(self, run_id, status):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                self.check_parent_guard(db, run_id)
                db.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))
                self.commit(db)
            except BaseException:
                db.execute('ROLLBACK')
                raise

    def event(self, run_id, kind, payload):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                self.check_parent_guard(db, run_id)
                db.execute("INSERT INTO events(run_id,type,payload,created) VALUES(?,?,?,?)",
                           (run_id, kind, dumps(payload), time.time()))
                self.commit(db)
            except BaseException:
                db.execute('ROLLBACK')
                raise
        from neurogra.platform.control import report_event
        report_event(kind, payload)

    def save(self, run_id, kind, object_id, payload, version=1, expected_version=0):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self.check_parent_guard(db, run_id)
                latest = db.execute("SELECT max(version) FROM objects WHERE run_id=? AND kind=? AND object_id=?",
                                    (run_id, kind, object_id)).fetchone()[0] or 0
                if latest != expected_version or version != latest + 1:
                    raise VersionConflict(f"expected {expected_version}, actual {latest}")
                db.execute("INSERT INTO objects VALUES(?,?,?,?,?)",
                           (run_id, kind, object_id, version, dumps(payload)))
                self.commit(db)
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def objects(self, run_id, kind=None):
        with self.connect() as db:
            query = "SELECT * FROM objects WHERE run_id=?"
            rows = db.execute(query + (" AND kind=?" if kind else ""), (run_id, kind) if kind else (run_id,)).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload"])} for row in rows]

    def summary(self, run_id):
        with self.connect() as db:
            counts = db.execute("SELECT kind,status,count(*) n FROM budget_ledger WHERE run_id=? GROUP BY kind,status", (run_id,)).fetchall()
            events = db.execute("SELECT type,payload FROM events WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
            linked = db.execute('SELECT 1 FROM budget_links WHERE parent_run_id=? LIMIT 1', (run_id,)).fetchone()
            total = db.execute('''SELECT kind,status,count(*) n FROM budget_ledger WHERE
                run_id=? OR run_id IN (SELECT child_run_id FROM budget_links WHERE parent_run_id=?)
                GROUP BY kind,status''', (run_id, run_id)).fetchall() if linked else None
        run = self.run(run_id)
        result = {"run_id": run_id, "status": run["status"], "calls": [dict(r) for r in counts],
                  "events": [{"type": r["type"], "payload": json.loads(r["payload"])} for r in events]}
        if total is not None:
            result['aggregate_calls'] = [dict(row) for row in total]
        return result

    @contextmanager
    def workflow_lock(self, run_id):
        """OS releases the lock on crash; concurrent resume must not consume calls twice."""
        import os
        lock_path = self.path.parent / (run_id + ".lock")
        with lock_path.open("a+b") as handle:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
