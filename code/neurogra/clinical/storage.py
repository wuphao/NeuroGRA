"""Per-operation SQLite connections; transactional budgets survive process restarts."""
import json
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
from .utils import dumps


class BudgetExceeded(RuntimeError):
    pass


class VersionConflict(RuntimeError):
    pass


class RunStore:
    def __init__(self, path: Path):
        self.path = path
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

    @contextmanager
    def connect(self):
        # isolation_level=None: explicit transactions below, no cross-thread connections.
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def create_run(self, config: dict, budget: dict) -> str:
        run_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?)",
                       (run_id, dumps(config), time.time() + budget["seconds"],
                        dumps({k: budget[k] for k in ("llm", "retrieval", "image")}), "received"))
        return run_id

    def run(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("unknown run")
        return dict(row)

    def reserve(self, run_id: str, kind: str, scope=None, scope_limit=None) -> tuple[str, float]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if run is None:
                    raise ValueError("unknown run")
                remaining = run["deadline"] - time.time()
                used = db.execute("SELECT count(*) FROM budget_ledger WHERE run_id=? AND kind=?",
                                  (run_id, kind)).fetchone()[0]
                if remaining <= 0 or used >= json.loads(run["limits"]).get(kind, 0):
                    raise BudgetExceeded(kind)
                if scope is not None and scope_limit is not None:
                    scoped_used = db.execute(
                        "SELECT count(*) FROM budget_ledger WHERE run_id=? AND kind=? AND scope=?",
                        (run_id, kind, scope)).fetchone()[0]
                    if scoped_used >= scope_limit:
                        raise BudgetExceeded("task_" + kind)
                call_id = uuid.uuid4().hex
                db.execute("INSERT INTO budget_ledger(call_id,run_id,kind,status,started,finished,usage,scope) VALUES(?,?,?,?,?,?,?,?)",
                           (call_id, run_id, kind, "running", time.time(), None, None, scope))
                db.execute("COMMIT")
                return call_id, remaining
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def settle(self, call_id, status, usage=None):
        with self.connect() as db:
            db.execute("UPDATE budget_ledger SET status=?, finished=?, usage=? WHERE call_id=? AND status='running'",
                       (status, time.time(), dumps(usage or {}), call_id))

    def mark_interrupted(self, run_id):
        with self.connect() as db:
            db.execute("UPDATE budget_ledger SET status='indeterminate' WHERE run_id=? AND status='running'", (run_id,))

    def status(self, run_id, status):
        with self.connect() as db:
            db.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))

    def event(self, run_id, kind, payload):
        with self.connect() as db:
            db.execute("INSERT INTO events(run_id,type,payload,created) VALUES(?,?,?,?)",
                       (run_id, kind, dumps(payload), time.time()))

    def save(self, run_id, kind, object_id, payload, version=1, expected_version=0):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                latest = db.execute("SELECT max(version) FROM objects WHERE run_id=? AND kind=? AND object_id=?",
                                    (run_id, kind, object_id)).fetchone()[0] or 0
                if latest != expected_version or version != latest + 1:
                    raise VersionConflict(f"expected {expected_version}, actual {latest}")
                db.execute("INSERT INTO objects VALUES(?,?,?,?,?)",
                           (run_id, kind, object_id, version, dumps(payload)))
                db.execute("COMMIT")
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
        run = self.run(run_id)
        return {"run_id": run_id, "status": run["status"], "calls": [dict(r) for r in counts],
                "events": [{"type": r["type"], "payload": json.loads(r["payload"])} for r in events]}

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
