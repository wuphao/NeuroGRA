"""Durable task graphs and fenced writes in the existing clinical SQLite store.

This layer does not grant access to patients or tools.  The caller is responsible
for live authorization and for holding the run's process lock while executing.
Only task identifiers and statuses are written to public progress events.
"""

from __future__ import annotations

import errno
import json
import sqlite3
import threading
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass

from .collaboration_contracts import CollaborationGraph, SubjectScope, validate_task_scope
from .schemas import AgentResult
from .storage import BudgetExceeded, RunStore, VersionConflict
from .utils import dumps, fingerprint, identity


class CollaborationConflict(RuntimeError):
    pass


class OwnershipLost(CollaborationConflict):
    pass


@dataclass(frozen=True)
class CollaborationFence:
    run_id: str
    owner: str
    epoch: int
    parent: CollaborationFence | None = None


class CollaborationStore:
    """Store graph versions and results alongside an existing :class:`RunStore`."""

    MIGRATION = "collaboration_v2_1"
    TERMINAL = frozenset({"succeeded", "partial", "failed", "skipped", "cancelled"})

    def __init__(self, store: RunStore):
        self.store = store
        self._migrate()

    def _migrate(self):
        deadline = time.monotonic() + 15
        while True:
            try:
                return self._migrate_locked()
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def _migrate_locked(self):
        # Serialize the additive migration with other users of this same database.
        with self.store.workflow_lock("collaboration_schema"):
            with self.store.connect() as db:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("BEGIN IMMEDIATE")
                try:
                    self.store.check_platform_attempt(db)
                    present = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                         "AND name='collaboration_schema_migrations'").fetchone()
                    if present and db.execute("SELECT 1 FROM collaboration_schema_migrations WHERE name=?",
                                              (self.MIGRATION,)).fetchone():
                        self.store.commit(db)
                        return
                    # Hold the writer reservation across both snapshot and migration.
                    # Backup uses a second connection so it reads the committed state
                    # without trying to back up its own active write transaction.
                    backup = self.store.path.with_name(
                        self.store.path.name + ".pre-collaboration-" + uuid.uuid4().hex + ".sqlite")
                    with self.store.connect() as source, closing(sqlite3.connect(backup)) as target:
                        source.backup(target)
                    for statement in (
                        "CREATE TABLE IF NOT EXISTS collaboration_schema_migrations "
                        "(name TEXT PRIMARY KEY, applied REAL NOT NULL)",
                        """CREATE TABLE IF NOT EXISTS collaboration_runs (
                            run_id TEXT PRIMARY KEY REFERENCES runs(run_id), entry_kind TEXT NOT NULL,
                            entry_key TEXT NOT NULL, scope TEXT NOT NULL, binding_hash TEXT NOT NULL,
                            state TEXT NOT NULL, graph_version INTEGER NOT NULL DEFAULT 0,
                            owner TEXT, epoch INTEGER NOT NULL DEFAULT 0, lease_until REAL,
                            created REAL NOT NULL, updated REAL NOT NULL,
                            UNIQUE(entry_kind, entry_key))""",
                        """CREATE TABLE IF NOT EXISTS collaboration_graphs (
                            run_id TEXT NOT NULL REFERENCES collaboration_runs(run_id),
                            graph_version INTEGER NOT NULL, digest TEXT NOT NULL, created REAL NOT NULL,
                            PRIMARY KEY(run_id,graph_version))""",
                        """CREATE TABLE IF NOT EXISTS collaboration_nodes (
                            run_id TEXT NOT NULL REFERENCES collaboration_runs(run_id),
                            task_id TEXT NOT NULL, task_version INTEGER NOT NULL,
                            kind TEXT NOT NULL, agent_type TEXT, definition TEXT NOT NULL, digest TEXT NOT NULL,
                            state TEXT NOT NULL, attempt_id TEXT, result_id TEXT, result_digest TEXT,
                            created REAL NOT NULL, updated REAL NOT NULL,
                            PRIMARY KEY(run_id,task_id,task_version))""",
                        """CREATE TABLE IF NOT EXISTS collaboration_graph_nodes (
                            run_id TEXT NOT NULL, graph_version INTEGER NOT NULL, task_id TEXT NOT NULL,
                            task_version INTEGER NOT NULL, ordinal INTEGER NOT NULL,
                            PRIMARY KEY(run_id,graph_version,task_id,task_version),
                            FOREIGN KEY(run_id,graph_version) REFERENCES collaboration_graphs(run_id,graph_version),
                            FOREIGN KEY(run_id,task_id,task_version) REFERENCES collaboration_nodes(run_id,task_id,task_version))""",
                        """CREATE TABLE IF NOT EXISTS collaboration_edges (
                            run_id TEXT NOT NULL, graph_version INTEGER NOT NULL,
                            parent_id TEXT NOT NULL, parent_version INTEGER NOT NULL,
                            child_id TEXT NOT NULL, child_version INTEGER NOT NULL, allow_partial INTEGER NOT NULL,
                            PRIMARY KEY(run_id,graph_version,parent_id,parent_version,child_id,child_version),
                            FOREIGN KEY(run_id,graph_version) REFERENCES collaboration_graphs(run_id,graph_version),
                            FOREIGN KEY(run_id,parent_id,parent_version) REFERENCES collaboration_nodes(run_id,task_id,task_version),
                            FOREIGN KEY(run_id,child_id,child_version) REFERENCES collaboration_nodes(run_id,task_id,task_version))""",
                        """CREATE TABLE IF NOT EXISTS collaboration_events (
                            event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                            run_id TEXT NOT NULL REFERENCES collaboration_runs(run_id), event_key TEXT NOT NULL,
                            kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL,
                            UNIQUE(run_id,event_key))""",
                        "CREATE INDEX IF NOT EXISTS collaboration_nodes_ready "
                        "ON collaboration_nodes(run_id,state,created,task_id)",
                        "CREATE INDEX IF NOT EXISTS collaboration_edges_child "
                        "ON collaboration_edges(run_id,graph_version,child_id,child_version)",
                        "CREATE INDEX IF NOT EXISTS collaboration_events_cursor "
                        "ON collaboration_events(run_id,event_seq)",
                    ):
                        db.execute(statement)
                    if db.execute("PRAGMA foreign_key_check").fetchone():
                        raise CollaborationConflict("collaboration_migration_foreign_key_violation")
                    db.execute("INSERT INTO collaboration_schema_migrations(name,applied) VALUES(?,?)",
                               (self.MIGRATION, time.time()))
                    self.store.commit(db)
                except BaseException:
                    db.execute("ROLLBACK")
                    raise

    @staticmethod
    def _event(db, run_id, key, kind, payload):
        db.execute("""INSERT OR IGNORE INTO collaboration_events(run_id,event_key,kind,payload,created)
                      VALUES(?,?,?,?,?)""", (run_id, key, kind, dumps(payload), time.time()))

    @staticmethod
    def _check_fence(db, fence: CollaborationFence, *, allow_terminal=False):
        row = db.execute("""SELECT * FROM collaboration_runs WHERE run_id=? AND owner=? AND epoch=?
                            AND lease_until>?""",
                         (fence.run_id, fence.owner, fence.epoch, time.time())).fetchone()
        if row is None:
            raise OwnershipLost("collaboration_ownership_lost")
        if not allow_terminal and row["state"] in {"completed", "partial", "failed", "cancelled"}:
            raise CollaborationConflict("collaboration_run_terminal")
        metadata = db.execute('SELECT config FROM runs WHERE run_id=?', (fence.run_id,)).fetchone()
        configured_parent = json.loads(metadata['config']).get('budget_parent_run_id') if metadata else None
        link = db.execute('SELECT parent_run_id FROM budget_links WHERE child_run_id=?', (fence.run_id,)).fetchone()
        parent_id = link['parent_run_id'] if link else None
        if configured_parent != parent_id:
            raise OwnershipLost('budget_parent_binding_changed')
        if parent_id is not None:
            if fence.parent is None or fence.parent.run_id != parent_id:
                raise OwnershipLost('parent_fence_required')
            parent = db.execute('SELECT config FROM runs WHERE run_id=?', (parent_id,)).fetchone()
            parent_config = json.loads(parent['config']) if parent else {}
            if (parent_config.get('purpose') != 'mixed_dialogue' or
                    parent_config.get('budget_child_run_id') != fence.run_id):
                raise OwnershipLost('budget_parent_binding_changed')
            CollaborationStore._check_fence(db, fence.parent)
        elif fence.parent is not None:
            raise OwnershipLost('parent_fence_mismatch')
        return row

    def _check_owned(self, db, fence, *, allow_terminal=False):
        self.store.check_platform_attempt(db, run_id=fence.run_id)
        return self._check_fence(db, fence, allow_terminal=allow_terminal)

    def _prior_platform_owner_is_stale(self, db, row, now):
        """Permit a new report attempt to take over its predecessor's orphaned lease."""
        if self.store.platform_attempt is None or row['entry_kind'] != 'report' or ':' not in row['owner']:
            return False
        worker, attempt_id = row['owner'].rsplit(':', 1)
        prior = db.execute('''SELECT worker,status,lease_until,cancel_requested,run_id
                              FROM platform_guard.attempts WHERE attempt_id=?''', (attempt_id,)).fetchone()
        return bool(prior and prior['worker'] == worker and prior['run_id'] == row['run_id'] and
                    (prior['status'] != 'running' or prior['cancel_requested'] or
                     not prior['lease_until'] or prior['lease_until'] <= now))

    @classmethod
    def _interrupt_children(cls, db, parent_id, now, reason):
        """Invalidate inherited leases and retain the cost of uncertain calls."""
        children = db.execute('SELECT child_run_id FROM budget_links WHERE parent_run_id=?',
                              (parent_id,)).fetchall()
        for child in children:
            child_id = child['child_run_id']
            db.execute("UPDATE budget_ledger SET status='indeterminate',finished=? "
                       "WHERE run_id=? AND status='running'", (now, child_id))
            state = db.execute('SELECT state FROM collaboration_runs WHERE run_id=?', (child_id,)).fetchone()
            if state is None or state['state'] in {'completed', 'partial', 'failed', 'cancelled'}:
                continue
            tasks = db.execute("SELECT task_id,task_version FROM collaboration_nodes "
                               "WHERE run_id=? AND state='running'", (child_id,)).fetchall()
            db.execute("UPDATE collaboration_nodes SET state='interrupted',updated=? "
                       "WHERE run_id=? AND state='running'", (now, child_id))
            for task in tasks:
                cls._event(db, child_id, f"parent_interrupted:{reason}:{task['task_id']}:{task['task_version']}",
                           'task_interrupted', {'task_id': task['task_id'],
                                                'task_version': task['task_version'], 'status': 'interrupted'})
            db.execute("UPDATE collaboration_runs SET state='interrupted',owner=NULL,lease_until=NULL,updated=? "
                       "WHERE run_id=?", (now, child_id))
            cls._event(db, child_id, f'parent_interrupted:{reason}', 'run_interrupted', {'status': 'interrupted'})

    def create(self, run_id, scope: SubjectScope, *, entry_kind, entry_key, config_digest):
        if entry_kind not in {"chat", "report"} or not entry_key or not config_digest:
            raise ValueError("invalid_collaboration_binding")
        scope = SubjectScope.model_validate(scope)
        binding = fingerprint({"scope": scope.model_dump(mode="json"), "entry_kind": entry_kind,
                               "entry_key": entry_key, "config_digest": config_digest})
        now = time.time()
        with self.store.connect() as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                self.store.check_parent_guard(db, run_id)
                row = db.execute("SELECT * FROM collaboration_runs WHERE run_id=?", (run_id,)).fetchone()
                if row is not None:
                    if row["binding_hash"] != binding:
                        raise CollaborationConflict("collaboration_binding_changed")
                    self.store.commit(db)
                    return False
                other = db.execute("SELECT run_id FROM collaboration_runs WHERE entry_kind=? AND entry_key=?",
                                   (entry_kind, entry_key)).fetchone()
                if other:
                    raise CollaborationConflict("collaboration_entry_already_bound")
                db.execute("""INSERT INTO collaboration_runs
                    (run_id,entry_kind,entry_key,scope,binding_hash,state,created,updated)
                    VALUES(?,?,?,?,?,'accepted',?,?)""",
                    (run_id, entry_kind, entry_key, dumps(scope), binding, now, now))
                self._event(db, run_id, "run_created", "run_created", {"status": "accepted"})
                self.store.commit(db)
                return True
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def claim(self, run_id, owner, *, lease_seconds=30):
        if not owner or lease_seconds <= 0:
            raise ValueError("invalid_collaboration_owner")
        now = time.time()
        with self.store.connect() as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                parent_fence = self.store.check_parent_guard(db, run_id)
                now = time.time()
                row = db.execute("SELECT * FROM collaboration_runs WHERE run_id=?", (run_id,)).fetchone()
                if row is None:
                    raise ValueError("unknown_collaboration_run")
                if row["state"] in {"completed", "partial", "failed", "cancelled"}:
                    raise CollaborationConflict("collaboration_run_terminal")
                prior_stale = row['owner'] and self._prior_platform_owner_is_stale(db, row, now)
                if row["owner"] and (row["lease_until"] or 0) > now and not prior_stale:
                    if row["owner"] != owner:
                        raise CollaborationConflict("collaboration_owner_busy")
                    epoch = row["epoch"]
                else:
                    epoch = row["epoch"] + 1
                    if row["epoch"]:
                        # A remote request may have finished even though its former
                        # owner can no longer record the outcome. Do not refund it.
                        db.execute("""UPDATE budget_ledger SET status='indeterminate',finished=?
                            WHERE run_id=? AND status='running'""", (now, run_id))
                        self._interrupt_children(db, run_id, now, f'takeover:{epoch}')
                    interrupted = db.execute("""SELECT task_id,task_version FROM collaboration_nodes
                        WHERE run_id=? AND state='running'""", (run_id,)).fetchall()
                    db.execute("UPDATE collaboration_nodes SET state='interrupted',updated=? "
                               "WHERE run_id=? AND state='running'", (now, run_id))
                    for task in interrupted:
                        self._event(db, run_id, f"interrupted:{task['task_id']}:{task['task_version']}:{epoch}",
                                    "task_interrupted", {"task_id": task["task_id"],
                                    "task_version": task["task_version"], "status": "interrupted"})
                db.execute("UPDATE collaboration_runs SET owner=?,epoch=?,lease_until=?,state='running',updated=? "
                           "WHERE run_id=?", (owner, epoch, now + lease_seconds, now, run_id))
                self.store.commit(db)
                return CollaborationFence(run_id, owner, epoch, parent=parent_fence)
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def heartbeat(self, fence: CollaborationFence, *, lease_seconds=30):
        if lease_seconds <= 0:
            raise ValueError("invalid_collaboration_lease")
        now = time.time()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute("SELECT owner,epoch,state FROM collaboration_runs WHERE run_id=?",
                                 (fence.run_id,)).fetchone()
                if (row and row['owner'] == fence.owner and row['epoch'] == fence.epoch and
                        row['state'] in {'completed', 'partial', 'failed', 'cancelled'}):
                    self.store.commit(db)
                    return False
                self._check_owned(db, fence)
                self.store.check_parent_guard(db, fence.run_id)
                now = time.time()
                db.execute('UPDATE collaboration_runs SET lease_until=?,updated=? WHERE run_id=?',
                           (now + lease_seconds, now, fence.run_id))
                self.store.commit(db)
            except BaseException:
                db.execute('ROLLBACK')
                raise
        return True

    def interrupt(self, fence: CollaborationFence):
        """Fence an unfinished owner and leave in-flight task attempts unresolved."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._check_owned(db, fence)
                now = time.time()
                db.execute("UPDATE collaboration_nodes SET state='interrupted',updated=? "
                           "WHERE run_id=? AND state='running'", (now, fence.run_id))
                db.execute("""UPDATE budget_ledger SET status='indeterminate',finished=?
                    WHERE run_id=? AND status='running'""", (now, fence.run_id))
                self._interrupt_children(db, fence.run_id, now, f'interrupt:{fence.epoch}')
                db.execute("""UPDATE collaboration_runs SET state='interrupted',owner=NULL,lease_until=NULL,
                    updated=? WHERE run_id=?""", (now, fence.run_id))
                self._event(db, fence.run_id, f"run_interrupted:{fence.epoch}",
                            "run_interrupted", {"status": "interrupted"})
                self.store.commit(db)
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @contextmanager
    def owned(self, run_id, owner, *, lease_seconds=30):
        """Maintain a lease during long calls; all commits still verify the fence."""
        fence = self.claim(run_id, owner, lease_seconds=lease_seconds)
        stop, lost = threading.Event(), threading.Event()

        def heartbeat():
            while not stop.wait(max(0.05, lease_seconds / 3)):
                try:
                    if not self.heartbeat(fence, lease_seconds=lease_seconds):
                        return
                except Exception:
                    lost.set()
                    return

        worker = threading.Thread(target=heartbeat, daemon=True)
        worker.start()
        try:
            yield fence
            if lost.is_set():
                raise OwnershipLost("collaboration_ownership_lost")
            if self.snapshot(run_id)['state'] not in {'completed', 'partial', 'failed', 'cancelled'}:
                self.interrupt(fence)
        except BaseException:
            try:
                self.interrupt(fence)
            except (BudgetExceeded, CollaborationConflict, sqlite3.Error, OSError):
                pass
            raise
        finally:
            stop.set()
            # Do not release the enclosing activity gate while this writer is alive.
            worker.join()

    def install_graph(self, fence: CollaborationFence, graph: CollaborationGraph, *, form_keys, agent_types):
        graph = CollaborationGraph.model_validate(graph)
        with self.store.connect() as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                row = self._check_owned(db, fence)
                scope = SubjectScope.model_validate_json(row["scope"])
                errors = validate_task_scope(graph, scope, form_keys=set(form_keys), agent_types=set(agent_types))
                if errors:
                    raise ValueError(",".join(errors))
                digest = fingerprint(graph)
                prior = db.execute("SELECT digest FROM collaboration_graphs WHERE run_id=? AND graph_version=?",
                                   (fence.run_id, graph.graph_version)).fetchone()
                if prior:
                    if prior["digest"] != digest:
                        raise CollaborationConflict("graph_version_changed")
                    self.store.commit(db)
                    return False
                if graph.graph_version != row["graph_version"] + 1:
                    raise CollaborationConflict("graph_version_out_of_order")
                existing = {(r["task_id"], r["task_version"]) for r in db.execute(
                    "SELECT task_id,task_version FROM collaboration_graph_nodes WHERE run_id=? AND graph_version=?",
                    (fence.run_id, row["graph_version"]))}
                if not existing <= {task.key for task in graph.tasks}:
                    raise CollaborationConflict("graph_dropped_existing_task")
                now = time.time()
                db.execute("INSERT INTO collaboration_graphs VALUES(?,?,?,?)",
                           (fence.run_id, graph.graph_version, digest, now))
                for ordinal, task in enumerate(graph.tasks):
                    node_digest = fingerprint(task)
                    node = db.execute("SELECT digest FROM collaboration_nodes WHERE run_id=? AND task_id=? "
                                      "AND task_version=?", (fence.run_id, *task.key)).fetchone()
                    if node and node["digest"] != node_digest:
                        raise CollaborationConflict("task_version_changed")
                    if not node:
                        db.execute("""INSERT INTO collaboration_nodes
                            (run_id,task_id,task_version,kind,agent_type,definition,digest,state,created,updated)
                            VALUES(?,?,?,?,?,?,?,'pending',?,?)""", (fence.run_id, task.task_id,
                            task.task_version, task.kind, task.agent_type, dumps(task), node_digest, now, now))
                    db.execute("INSERT INTO collaboration_graph_nodes VALUES(?,?,?,?,?)",
                               (fence.run_id, graph.graph_version, task.task_id, task.task_version, ordinal))
                for task in graph.tasks:
                    for dep in task.depends_on:
                        db.execute("INSERT INTO collaboration_edges VALUES(?,?,?,?,?,?,?)",
                                   (fence.run_id, graph.graph_version, dep.task_id, dep.task_version,
                                    task.task_id, task.task_version, int(dep.allow_partial)))
                db.execute("UPDATE collaboration_runs SET graph_version=?,updated=? WHERE run_id=?",
                           (graph.graph_version, now, fence.run_id))
                self._event(db, fence.run_id, f"graph:{graph.graph_version}", "graph_installed",
                            {"graph_version": graph.graph_version, "task_count": len(graph.tasks)})
                self.store.commit(db)
                return True
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def claim_next(self, fence: CollaborationFence, *, kinds=None, task_ids=None):
        """Atomically claim the first ready node; never treat a failed dependency as success."""
        with self.store.connect() as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                run = self._check_owned(db, fence)
                while True:
                    skipped = False
                    nodes = db.execute("""SELECT n.* FROM collaboration_nodes n
                        JOIN collaboration_graph_nodes g ON n.run_id=g.run_id AND n.task_id=g.task_id
                          AND n.task_version=g.task_version
                        WHERE g.run_id=? AND g.graph_version=? AND n.state='pending' ORDER BY g.ordinal""",
                        (fence.run_id, run["graph_version"])).fetchall()
                    for node in nodes:
                        # A resumed graph may already contain future-round nodes of the
                        # same kind. Only the caller's current stage may claim or skip them.
                        if task_ids is not None and node['task_id'] not in task_ids:
                            continue
                        dependencies = db.execute("""SELECT n.state,e.allow_partial FROM collaboration_edges e
                            JOIN collaboration_nodes n ON n.run_id=e.run_id AND n.task_id=e.parent_id
                              AND n.task_version=e.parent_version
                            WHERE e.run_id=? AND e.graph_version=? AND e.child_id=? AND e.child_version=?""",
                            (fence.run_id, run["graph_version"], node["task_id"], node["task_version"])).fetchall()
                        if any(dep["state"] in {"failed", "skipped", "cancelled", "stale"} or
                               (dep["state"] == "partial" and not dep["allow_partial"]) for dep in dependencies):
                            db.execute("UPDATE collaboration_nodes SET state='skipped',updated=? WHERE run_id=? "
                                       "AND task_id=? AND task_version=?", (time.time(), fence.run_id,
                                       node["task_id"], node["task_version"]))
                            self._event(db, fence.run_id, f"blocked:{node['task_id']}:{node['task_version']}",
                                        "task_skipped", {"task_id": node["task_id"],
                                        "task_version": node["task_version"], "status": "skipped",
                                        "reason": "dependency_unavailable"})
                            skipped = True
                            continue
                        if any(dep["state"] != "succeeded" and not (
                                dep["state"] == "partial" and dep["allow_partial"]) for dep in dependencies):
                            continue
                        if kinds is not None and node["kind"] not in kinds:
                            continue
                        attempt_id = uuid.uuid4().hex
                        db.execute("UPDATE collaboration_nodes SET state='running',attempt_id=?,updated=? "
                                   "WHERE run_id=? AND task_id=? AND task_version=? AND state='pending'",
                                   (attempt_id, time.time(), fence.run_id, node["task_id"], node["task_version"]))
                        self._event(db, fence.run_id,
                                    f"started:{node['task_id']}:{node['task_version']}:{attempt_id}",
                                    "task_started", {"task_id": node["task_id"],
                                    "task_version": node["task_version"], "status": "running"})
                        self.store.commit(db)
                        return {"task": json.loads(node["definition"]), "attempt_id": attempt_id}
                    if not skipped:
                        break
                self.store.commit(db)
                return None
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def finish_task(self, fence: CollaborationFence, task_id, task_version, attempt_id, *, status, result):
        if status not in self.TERMINAL or not isinstance(result, dict):
            raise ValueError("invalid_task_result")
        digest = fingerprint({"status": status, "result": result})
        with self.store.connect() as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                run = self._check_owned(db, fence, allow_terminal=True)
                node = db.execute("SELECT * FROM collaboration_nodes WHERE run_id=? AND task_id=? "
                                  "AND task_version=?", (fence.run_id, task_id, task_version)).fetchone()
                if node is None:
                    raise ValueError("unknown_collaboration_task")
                if node["state"] in self.TERMINAL:
                    if node["attempt_id"] == attempt_id and node["result_digest"] == digest:
                        self.store.commit(db)
                        return False
                    raise CollaborationConflict("task_result_already_committed")
                if run["state"] in {"completed", "partial", "failed", "cancelled"}:
                    raise CollaborationConflict("collaboration_run_terminal")
                if node["state"] != "running" or node["attempt_id"] != attempt_id:
                    raise CollaborationConflict("task_attempt_not_running")
                if node["kind"] == "specialist.analyze":
                    specialist = AgentResult.model_validate(result)
                    definition = json.loads(node["definition"])
                    if (specialist.task_id != task_id or
                            (definition.get("case_version") is not None and
                             specialist.case_version != definition["case_version"]) or
                            {"completed": "succeeded", "partial": "partial", "failed": "failed",
                             "skipped_no_data": "skipped"}[specialist.status] != status):
                        raise ValueError("specialist_result_binding_mismatch")
                result_id = identity("collab_result", [task_id, task_version])
                db.execute("INSERT INTO objects(run_id,kind,object_id,version,payload) VALUES(?,?,?,1,?)",
                           (fence.run_id, "collaboration_result", result_id, dumps(result)))
                db.execute("""UPDATE collaboration_nodes SET state=?,result_id=?,result_digest=?,updated=?
                    WHERE run_id=? AND task_id=? AND task_version=?""",
                    (status, result_id, digest, time.time(), fence.run_id, task_id, task_version))
                self._event(db, fence.run_id, f"finished:{task_id}:{task_version}:{attempt_id}",
                            "task_finished", {"task_id": task_id, "task_version": task_version, "status": status})
                self.store.commit(db)
                return True
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def retry_interrupted(self, fence: CollaborationFence, task_id, task_version):
        """Explicitly requeue an interrupted attempt; its prior result remains unknown."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._check_owned(db, fence)
                changed = db.execute("""UPDATE collaboration_nodes SET state='pending',updated=?
                    WHERE run_id=? AND task_id=? AND task_version=? AND state='interrupted'""",
                    (time.time(), fence.run_id, task_id, task_version)).rowcount
                if not changed:
                    raise CollaborationConflict("task_not_interrupted")
                self._event(db, fence.run_id, f"retry:{task_id}:{task_version}:{fence.epoch}",
                            "task_retry_requested", {"task_id": task_id, "task_version": task_version,
                            "previous_attempt": "indeterminate"})
                self.store.commit(db)
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def finish_run(self, fence: CollaborationFence, *, force_partial=False, final=None):
        """Finish only after every task has reached a terminal state."""
        if final is not None and not isinstance(final, dict):
            raise ValueError("invalid_collaboration_final")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                run = self._check_owned(db, fence, allow_terminal=True)
                if run["state"] in {"completed", "partial"}:
                    if final is not None:
                        saved = db.execute("""SELECT payload FROM objects WHERE run_id=? AND kind='collaboration_final'
                            AND object_id='main' AND version=1""", (fence.run_id,)).fetchone()
                        if saved is None or saved["payload"] != dumps(final):
                            raise CollaborationConflict("collaboration_final_changed")
                    self.store.commit(db)
                    return run["state"]
                states = [row["state"] for row in db.execute("""SELECT n.state FROM collaboration_nodes n
                    JOIN collaboration_graph_nodes g ON n.run_id=g.run_id AND n.task_id=g.task_id
                      AND n.task_version=g.task_version
                    WHERE g.run_id=? AND g.graph_version=?""", (fence.run_id, run["graph_version"]))]
                if not states or any(state not in self.TERMINAL for state in states):
                    raise CollaborationConflict("collaboration_tasks_unfinished")
                # A read or model call may still be running after a branch timed
                # out. Freeze its outcome as unknown in the same transaction as
                # finalization so a late owner cannot declare it complete.
                unsettled = db.execute("""UPDATE budget_ledger SET status='indeterminate',finished=?
                    WHERE run_id=? AND status='running'""", (time.time(), fence.run_id)).rowcount
                status = ("completed" if not force_partial and not unsettled and
                          all(state == "succeeded" for state in states) else "partial")
                if final is not None:
                    db.execute("INSERT INTO objects(run_id,kind,object_id,version,payload) VALUES(?,?,'main',1,?)",
                               (fence.run_id, "collaboration_final", dumps(final)))
                db.execute("UPDATE collaboration_runs SET state=?,updated=? WHERE run_id=?",
                           (status, time.time(), fence.run_id))
                db.execute("UPDATE runs SET status=? WHERE run_id=?", (status, fence.run_id))
                self._event(db, fence.run_id, "run_finished", "run_finished", {"status": status})
                self.store.commit(db)
                return status
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def events(self, run_id, *, after=0, limit=100):
        if after < 0 or not 1 <= limit <= 200:
            raise ValueError("invalid_event_cursor")
        with self.store.connect() as db:
            rows = db.execute("""SELECT event_seq,kind,payload,created FROM collaboration_events
                WHERE run_id=? AND event_seq>? ORDER BY event_seq LIMIT ?""", (run_id, after, limit)).fetchall()
        return [{"event_seq": row["event_seq"], "kind": row["kind"],
                 "payload": json.loads(row["payload"]), "created": row["created"]} for row in rows]

    def snapshot(self, run_id):
        with self.store.connect() as db:
            run = db.execute("SELECT * FROM collaboration_runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                raise ValueError("unknown_collaboration_run")
            nodes = db.execute("""SELECT n.task_id,n.task_version,n.kind,n.agent_type,n.state,n.result_id
                FROM collaboration_nodes n JOIN collaboration_graph_nodes g
                  ON n.run_id=g.run_id AND n.task_id=g.task_id AND n.task_version=g.task_version
                WHERE g.run_id=? AND g.graph_version=? ORDER BY g.ordinal""",
                (run_id, run["graph_version"])).fetchall()
        return {"run_id": run_id, "state": run["state"], "graph_version": run["graph_version"],
                "tasks": [dict(row) for row in nodes]}

    def fenced_store(self, fence: CollaborationFence, *, check=None, before_model=None):
        """Bind legacy executor and model writes to the current run ownership."""
        return FencedRunStore(self.store, fence, check=check, before_model=before_model)


class FencedRunStore:
    """RunStore interface used by specialists; every mutation checks the fence atomically."""

    def __init__(self, store, fence, *, check=None, before_model=None):
        self.store, self.fence, self.check, self.before_model = store, fence, check, before_model
        self.path = store.path

    def _run_id(self, run_id):
        if run_id != self.fence.run_id:
            raise ValueError("fenced_store_run_mismatch")
        if self.check:
            self.check()

    @contextmanager
    def _write(self, run_id):
        self._run_id(run_id)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                CollaborationStore._check_fence(db, self.fence)
                self.store.check_parent_guard(db, run_id)
                yield db
                self.store.commit(db)
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def run(self, run_id):
        self._run_id(run_id)
        return self.store.run(run_id)

    def objects(self, run_id, kind=None):
        self._run_id(run_id)
        return self.store.objects(run_id, kind)

    def summary(self, run_id):
        self._run_id(run_id)
        return self.store.summary(run_id)

    def workflow_lock(self, run_id):
        self._run_id(run_id)
        return self.store.workflow_lock(run_id)

    def mark_interrupted(self, run_id):
        with self._write(run_id) as db:
            db.execute("UPDATE budget_ledger SET status='indeterminate',finished=? "
                       "WHERE run_id=? AND status='running'", (time.time(), run_id))

    def status(self, run_id, status):
        with self._write(run_id) as db:
            db.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))

    def save(self, run_id, kind, object_id, payload, version=1, expected_version=0):
        with self._write(run_id) as db:
            latest = db.execute("SELECT max(version) FROM objects WHERE run_id=? AND kind=? AND object_id=?",
                                (run_id, kind, object_id)).fetchone()[0] or 0
            if latest != expected_version or version != latest + 1:
                raise VersionConflict(f"expected {expected_version}, actual {latest}")
            db.execute("INSERT INTO objects VALUES(?,?,?,?,?)", (run_id, kind, object_id, version, dumps(payload)))

    def event(self, run_id, kind, payload):
        with self._write(run_id) as db:
            db.execute("INSERT INTO events(run_id,type,payload,created) VALUES(?,?,?,?)",
                       (run_id, kind, dumps(payload), time.time()))
        from neurogra.platform.control import report_event
        report_event(kind, payload)

    def reserve(self, run_id, kind, scope=None, scope_limit=None, reserved=0):
        from neurogra.platform.control import check_cancelled
        check_cancelled()
        if kind == 'llm' and self.before_model:
            self.before_model()
        if reserved < 0:
            raise ValueError("negative_reserved_budget")
        with self._write(run_id) as db:
            call_id, remaining = self.store.reserve_in_transaction(db, run_id, kind, scope, scope_limit, reserved)
        return call_id, remaining

    def settle(self, call_id, status, usage=None):
        with self._write(self.fence.run_id) as db:
            db.execute("""UPDATE budget_ledger SET status=?,finished=?,usage=?
                WHERE run_id=? AND call_id=? AND status='running'""",
                (status, time.time(), dumps(usage or {}), self.fence.run_id, call_id))
