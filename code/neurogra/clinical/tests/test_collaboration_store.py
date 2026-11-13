import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from neurogra.clinical.collaboration_contracts import (
    CollaborationGraph,
    CollaborationTask,
    SubjectScope,
    TaskDependency,
)
from neurogra.clinical.collaboration_store import (
    CollaborationConflict,
    CollaborationStore,
    OwnershipLost,
)
from neurogra.clinical.storage import RunStore


class CollaborationStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.runs = RunStore(self.root / "clinical.sqlite")
        self.run_id = self.runs.create_run({}, {"llm": 4, "retrieval": 4, "seconds": 60})
        self.scope = SubjectScope(source_id="rwe", project_id="project", primary_patient="11")
        self.store = CollaborationStore(self.runs)
        self.store.create(self.run_id, self.scope, entry_kind="chat", entry_key="session:request",
                          config_digest="fixed-config")
        self.fence = self.store.claim(self.run_id, "worker-1")

    def task(self, task_id, *, depends=(), kind="patient.read"):
        return CollaborationTask(task_id=task_id, kind=kind, objective="读取或核查",
            patient_keys=["11"] if kind == "patient.read" else [], depends_on=list(depends))

    def install(self, tasks, version=1):
        return self.store.install_graph(self.fence, CollaborationGraph(graph_version=version, tasks=tasks),
                                        form_keys={"moca"}, agent_types={"cognition"})

    def test_migration_backup_and_idempotent_reopen(self):
        backups = list(self.root.glob("clinical.sqlite.pre-collaboration-*.sqlite"))
        self.assertEqual(len(backups), 1)
        CollaborationStore(self.runs)
        self.assertEqual(len(list(self.root.glob("clinical.sqlite.pre-collaboration-*.sqlite"))), 1)
        with self.runs.connect() as db:
            self.assertIsNone(db.execute("PRAGMA foreign_key_check").fetchone())
            self.assertEqual(db.execute("SELECT count(*) FROM collaboration_schema_migrations").fetchone()[0], 1)

    def test_migration_backup_excludes_writes_until_schema_commit(self):
        runs = RunStore(self.root / "concurrent.sqlite")
        existing = runs.create_run({}, {"seconds": 60})
        native_connect = sqlite3.connect
        blocked = []

        def connecting(path, *args, **kwargs):
            if ".pre-collaboration-" in str(path):
                with closing(native_connect(runs.path, timeout=.05)) as writer:
                    try:
                        writer.execute("UPDATE runs SET status='concurrent' WHERE run_id=?", (existing,))
                    except sqlite3.OperationalError as error:
                        blocked.append(str(error))
                    else:
                        self.fail("concurrent write was allowed between backup and migration")
            return native_connect(path, *args, **kwargs)

        with patch("neurogra.clinical.collaboration_store.sqlite3.connect", side_effect=connecting):
            CollaborationStore(runs)
        self.assertEqual(len(blocked), 1)
        backup, = self.root.glob("concurrent.sqlite.pre-collaboration-*.sqlite")
        with closing(native_connect(backup)) as db:
            self.assertEqual(db.execute("SELECT status FROM runs WHERE run_id=?", (existing,)).fetchone()[0],
                             "received")
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                         "AND name='collaboration_runs'").fetchone())
        with runs.connect() as db:
            db.execute("UPDATE runs SET status='after_migration' WHERE run_id=?", (existing,))

    def test_run_binding_is_idempotent_and_cannot_change_scope(self):
        self.assertFalse(self.store.create(self.run_id, self.scope, entry_kind="chat",
                                           entry_key="session:request", config_digest="fixed-config"))
        changed = self.scope.model_copy(update={"primary_patient": "12"})
        with self.assertRaisesRegex(CollaborationConflict, "collaboration_binding_changed"):
            self.store.create(self.run_id, changed, entry_kind="chat", entry_key="session:request",
                              config_digest="fixed-config")
        other = self.runs.create_run({}, {"seconds": 60})
        with self.assertRaisesRegex(CollaborationConflict, "collaboration_entry_already_bound"):
            self.store.create(other, self.scope, entry_kind="chat", entry_key="session:request",
                              config_digest="fixed-config")

    def test_graph_update_is_versioned_and_cannot_change_or_drop_tasks(self):
        first = self.task("read")
        self.assertTrue(self.install([first]))
        self.assertFalse(self.install([first]))
        second = self.task("review", kind="evidence.review", depends=[TaskDependency(task_id="read")])
        self.assertTrue(self.install([first, second], version=2))
        self.assertEqual(self.store.snapshot(self.run_id)["graph_version"], 2)
        with self.assertRaisesRegex(CollaborationConflict, "graph_dropped_existing_task"):
            self.install([first], version=3)
        changed = first.model_copy(update={"objective": "different"})
        with self.assertRaisesRegex(CollaborationConflict, "task_version_changed"):
            self.install([changed, second], version=3)
        self.assertEqual(self.store.snapshot(self.run_id)["graph_version"], 2)

    def test_scope_validation_rolls_back_graph(self):
        outside = self.task("outside").model_copy(update={"patient_keys": ["12"]})
        with self.assertRaisesRegex(ValueError, "patient_outside_scope"):
            self.install([outside])
        self.assertEqual(self.store.snapshot(self.run_id)["graph_version"], 0)
        self.assertTrue(self.install([self.task("inside")]))

    def test_dependencies_and_partial_failure_do_not_become_success(self):
        root = self.task("read")
        child = self.task("strict", kind="evidence.review", depends=[TaskDependency(task_id="read")])
        allowed = self.task("partial_ok", kind="evidence.review", depends=[
            TaskDependency(task_id="read", allow_partial=True)])
        # The order intentionally places dependents before the parent.
        self.install([child, allowed, root])
        first = self.store.claim_next(self.fence)
        self.assertEqual(first["task"]["task_id"], "read")
        self.store.finish_task(self.fence, "read", 1, first["attempt_id"], status="partial", result={"items": []})
        next_task = self.store.claim_next(self.fence)
        self.assertEqual(next_task["task"]["task_id"], "partial_ok")
        self.store.finish_task(self.fence, "partial_ok", 1, next_task["attempt_id"], status="succeeded", result={})
        self.assertIsNone(self.store.claim_next(self.fence))
        self.assertEqual({row["task_id"]: row["state"] for row in self.store.snapshot(self.run_id)["tasks"]},
                         {"read": "partial", "strict": "skipped", "partial_ok": "succeeded"})
        self.assertEqual(self.store.finish_run(self.fence), "partial")

    def test_concurrent_claims_do_not_claim_the_same_node(self):
        self.install([self.task("one"), self.task("two")])
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.store.claim_next(self.fence), range(4)))
        claimed = [item["task"]["task_id"] for item in results if item]
        self.assertEqual(sorted(claimed), ["one", "two"])

    def test_stage_claim_does_not_start_future_round_of_same_kind(self):
        self.install([self.task('first')])
        first = self.store.claim_next(self.fence, task_ids={'first'})
        self.store.finish_task(self.fence, 'first', 1, first['attempt_id'], status='succeeded', result={})
        self.install([self.task('first'), self.task('future'), self.task('current')], version=2)
        current = self.store.claim_next(self.fence, kinds={'patient.read'}, task_ids={'current'})
        self.assertEqual(current['task']['task_id'], 'current')
        state = {item['task_id']: item['state'] for item in self.store.snapshot(self.run_id)['tasks']}
        self.assertEqual(state['future'], 'pending')
        self.assertIsNone(self.store.claim_next(self.fence, task_ids=set()))

    def test_expired_owner_cannot_commit_and_retry_is_explicit(self):
        self.install([self.task("read")])
        old_attempt = self.store.claim_next(self.fence)["attempt_id"]
        with self.runs.connect() as db:
            db.execute("UPDATE collaboration_runs SET lease_until=0 WHERE run_id=?", (self.run_id,))
        new_fence = self.store.claim(self.run_id, "worker-2")
        self.assertGreater(new_fence.epoch, self.fence.epoch)
        self.assertEqual(self.store.snapshot(self.run_id)["tasks"][0]["state"], "interrupted")
        with self.assertRaises(OwnershipLost):
            self.store.finish_task(self.fence, "read", 1, old_attempt, status="succeeded", result={})
        self.assertIsNone(self.store.claim_next(new_fence))
        self.store.retry_interrupted(new_fence, "read", 1)
        new_attempt = self.store.claim_next(new_fence)["attempt_id"]
        self.assertNotEqual(old_attempt, new_attempt)
        result = {"private_patient_value": "synthetic-private"}
        self.assertTrue(self.store.finish_task(new_fence, "read", 1, new_attempt, status="succeeded", result=result))
        self.assertFalse(self.store.finish_task(new_fence, "read", 1, new_attempt, status="succeeded", result=result))
        with self.assertRaisesRegex(CollaborationConflict, "task_result_already_committed"):
            self.store.finish_task(new_fence, "read", 1, new_attempt, status="succeeded", result={})
        self.assertNotIn("synthetic-private", str(self.store.events(self.run_id)))
        self.assertEqual(self.store.finish_run(new_fence), "completed")
        self.assertFalse(self.store.finish_task(new_fence, "read", 1, new_attempt,
                                                status="succeeded", result=result))
        with self.assertRaisesRegex(CollaborationConflict, "collaboration_run_terminal"):
            self.store.install_graph(new_fence, CollaborationGraph(graph_version=2, tasks=[self.task("read")]),
                                     form_keys=set(), agent_types=set())

    def test_stale_executor_cannot_save_cache_or_settle_model_call(self):
        old_store = self.store.fenced_store(self.fence)
        call_id, _ = old_store.reserve(self.run_id, 'llm', scope='specialist', scope_limit=2)
        old_store.save(self.run_id, 'validated_batch', 'prior', {'validated': True})
        with self.runs.connect() as db:
            db.execute("UPDATE collaboration_runs SET lease_until=0 WHERE run_id=?", (self.run_id,))
        new_fence = self.store.claim(self.run_id, 'worker-2')
        with self.assertRaises(OwnershipLost):
            old_store.save(self.run_id, 'result', 'late', {'unsafe': True})
        with self.assertRaises(OwnershipLost):
            old_store.settle(call_id, 'completed')
        with self.assertRaises(OwnershipLost):
            old_store.reserve(self.run_id, 'llm')
        with self.runs.connect() as db:
            row = db.execute('SELECT status,finished FROM budget_ledger WHERE call_id=?', (call_id,)).fetchone()
            self.assertEqual(row['status'], 'indeterminate')
            self.assertIsNotNone(row['finished'])
        new_store = self.store.fenced_store(new_fence)
        new_store.save(self.run_id, 'result', 'replacement', {'validated': True})
        self.assertEqual([item['object_id'] for item in self.runs.objects(self.run_id, 'result')], ['replacement'])

    def test_model_permission_is_checked_before_each_budget_reservation(self):
        calls = []

        def authorize():
            calls.append('checked')
            if len(calls) == 2:
                raise PermissionError('patient_access_revoked')

        guarded = self.store.fenced_store(self.fence, before_model=authorize)
        first, _ = guarded.reserve(self.run_id, 'llm')
        guarded.settle(first, 'completed')
        with self.assertRaisesRegex(PermissionError, 'patient_access_revoked'):
            guarded.reserve(self.run_id, 'llm')
        with self.runs.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM budget_ledger WHERE run_id=?',
                                        (self.run_id,)).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
