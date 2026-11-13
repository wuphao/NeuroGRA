import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from neurogra.clinical.collaboration_contracts import SubjectScope
from neurogra.clinical.collaboration_store import CollaborationStore, OwnershipLost
from neurogra.clinical.storage import BudgetExceeded, RunStore


class BudgetHierarchyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = RunStore(Path(temporary.name) / 'clinical.sqlite')
        self.parent = 'a1' * 16
        self.child = 'b2' * 16

    def create_pair(self, *, parent_limit=2, child_limit=5):
        self.store.create_run({'purpose': 'mixed_dialogue', 'budget_child_run_id': self.child},
                              {'seconds': 60, 'patient_data': parent_limit, 'llm': parent_limit,
                               'retrieval': parent_limit, 'embedding': parent_limit},
                              run_id=self.parent)
        coordination = CollaborationStore(self.store)
        coordination.create(self.parent, SubjectScope(source_id='synthetic', project_id='1', primary_patient='1'),
                            entry_kind='chat', entry_key='parent', config_digest='version')
        parent_fence = coordination.claim(self.parent, 'parent-owner')
        self.child_store = RunStore(self.store.path, parent_fence=parent_fence)
        self.child_store.create_run({'purpose': 'patient_dialogue', 'budget_parent_run_id': self.parent},
                              {'seconds': 60, 'patient_data': child_limit, 'llm': child_limit,
                               'retrieval': child_limit, 'embedding': child_limit},
                              run_id=self.child)

    def test_parent_and_child_share_the_same_limit_and_keep_separate_receipts(self):
        self.create_pair()
        first, _ = self.child_store.reserve(self.child, 'patient_data', scope='initial')
        self.child_store.settle(first, 'completed')
        second, _ = self.store.reserve(self.parent, 'patient_data', scope='comparison')
        self.store.settle(second, 'indeterminate')
        with self.assertRaises(BudgetExceeded):
            self.child_store.reserve(self.child, 'patient_data', scope='supplement')
        with self.assertRaises(BudgetExceeded):
            self.store.reserve(self.parent, 'patient_data', scope='cohort')
        self.assertEqual(sum(row['n'] for row in self.store.summary(self.child)['calls']), 1)
        self.assertEqual(sum(row['n'] for row in self.store.summary(self.parent)['calls']), 1)
        aggregate = self.store.summary(self.parent)['aggregate_calls']
        self.assertEqual(sum(row['n'] for row in aggregate), 2)
        self.assertIn({'kind': 'patient_data', 'status': 'indeterminate', 'n': 1}, aggregate)

    def test_fenced_child_reservation_obeys_parent_budget(self):
        self.create_pair(parent_limit=1)
        coordination = CollaborationStore(self.child_store)
        coordination.create(self.child, SubjectScope(source_id='synthetic', project_id='1', primary_patient='1'),
                            entry_kind='chat', entry_key='child', config_digest='version')
        fence = coordination.claim(self.child, 'owner')
        child = coordination.fenced_store(fence)
        call_id, _ = child.reserve(self.child, 'llm', scope='plan')
        child.settle(call_id, 'completed')
        with self.assertRaises(BudgetExceeded):
            child.reserve(self.child, 'llm', scope='analysis')
        with self.assertRaises(BudgetExceeded):
            self.store.reserve(self.parent, 'llm', scope='other')

    def test_retrieval_and_embedding_each_obey_their_own_parent_limit(self):
        self.create_pair(parent_limit=2)
        for kind in ('retrieval', 'embedding'):
            with self.subTest(kind=kind):
                self.child_store.reserve(self.child, kind)
                self.store.reserve(self.parent, kind)
                with self.assertRaises(BudgetExceeded):
                    self.child_store.reserve(self.child, kind)


    def test_concurrent_parent_and_child_reservations_cannot_exceed_limit(self):
        self.create_pair(parent_limit=2)
        start = threading.Barrier(8)

        def reserve(index):
            start.wait(timeout=5)
            try:
                store = self.child_store if index % 2 else self.store
                return store.reserve(self.child if index % 2 else self.parent, 'patient_data')[0]
            except BudgetExceeded:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            calls = list(pool.map(reserve, range(8)))
        self.assertEqual(sum(call is not None for call in calls), 2)
        self.assertEqual(sum(row['n'] for row in self.store.summary(self.parent)['aggregate_calls']), 2)

    def test_invalid_parent_binding_cannot_create_an_unaccounted_child(self):
        self.store.create_run({'purpose': 'mixed_dialogue', 'budget_child_run_id': 'other'},
                              {'seconds': 60, 'patient_data': 2}, run_id=self.parent)
        with self.assertRaises(ValueError):
            self.store.create_run({'purpose': 'patient_dialogue', 'budget_parent_run_id': self.parent},
                                  {'seconds': 60, 'patient_data': 5}, run_id=self.child)
        with self.assertRaises(ValueError):
            self.store.run(self.child)

    def test_expired_parent_stops_further_child_reservations(self):
        self.create_pair()
        with self.store.connect() as db:
            db.execute('UPDATE runs SET deadline=0 WHERE run_id=?', (self.parent,))
        with self.assertRaises(BudgetExceeded):
            self.child_store.reserve(self.child, 'patient_data')

    def test_missing_link_does_not_silently_bypass_parent_limit(self):
        self.create_pair()
        with self.store.connect() as db:
            db.execute('DELETE FROM budget_links WHERE child_run_id=?', (self.child,))
        with self.assertRaisesRegex(OwnershipLost, 'budget_parent_binding_changed'):
            self.child_store.reserve(self.child, 'patient_data')
        with self.assertRaisesRegex(ValueError, 'budget_parent_binding_changed'):
            self.store.reserve(self.parent, 'patient_data')


if __name__ == '__main__':
    unittest.main()
