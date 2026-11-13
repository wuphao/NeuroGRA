import tempfile
import unittest
from pathlib import Path

from neurogra.clinical.collaboration_contracts import CollaborationGraph, CollaborationTask, SubjectScope
from neurogra.clinical.collaboration_store import CollaborationStore, OwnershipLost
from neurogra.clinical.storage import RunStore


class ParentFenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'clinical.sqlite'
        self.parent_id, self.child_id = 'ab' * 16, 'cd' * 16
        self.runs = RunStore(self.path)
        self.runs.create_run({'purpose': 'mixed_dialogue', 'budget_child_run_id': self.child_id},
                             {'seconds': 60, 'llm': 5, 'patient_data': 5}, run_id=self.parent_id)
        self.scope = SubjectScope(source_id='synthetic', project_id='1', primary_patient='1')
        self.parents = CollaborationStore(self.runs)
        self.parents.create(self.parent_id, self.scope, entry_kind='chat', entry_key='parent', config_digest='config')
        self.parent_fence = self.parents.claim(self.parent_id, 'parent-owner')
        self.child_store = RunStore(self.path, parent_fence=self.parent_fence)
        self.child_store.create_run({'purpose': 'patient_dialogue', 'budget_parent_run_id': self.parent_id},
                                    {'seconds': 60, 'llm': 5, 'patient_data': 5}, run_id=self.child_id)
        self.children = CollaborationStore(self.child_store)
        self.children.create(self.child_id, self.scope, entry_kind='chat', entry_key='child', config_digest='config')
        self.child_fence = self.children.claim(self.child_id, 'child-owner')

    def test_expired_parent_blocks_child_budget_cache_and_graph_writes(self):
        node = CollaborationTask(task_id='read', kind='patient.read', objective='读取合成记录', patient_keys=['1'])
        self.children.install_graph(self.child_fence, CollaborationGraph(tasks=[node]),
                                    form_keys=set(), agent_types=set())
        attempt = self.children.claim_next(self.child_fence)['attempt_id']
        fenced = self.children.fenced_store(self.child_fence)
        call_id, _ = fenced.reserve(self.child_id, 'patient_data')
        with self.runs.connect() as db:
            db.execute('UPDATE collaboration_runs SET lease_until=0 WHERE run_id=?', (self.parent_id,))
        for action in (
            lambda: self.child_store.reserve(self.child_id, 'patient_data'),
            lambda: self.child_store.save(self.child_id, 'preparation', 'late', {}),
            lambda: fenced.reserve(self.child_id, 'llm'),
            lambda: fenced.settle(call_id, 'completed'),
            lambda: fenced.save(self.child_id, 'cache', 'late', {}),
            lambda: self.children.finish_task(self.child_fence, 'read', 1, attempt,
                                              status='succeeded', result={'synthetic': True}),
        ):
            with self.subTest(action=action), self.assertRaises(OwnershipLost):
                action()
        self.assertFalse(self.runs.objects(self.child_id, 'cache'))
        self.assertFalse(self.runs.objects(self.child_id, 'preparation'))
        self.assertIn({'kind': 'patient_data', 'status': 'running', 'n': 1},
                      self.runs.summary(self.child_id)['calls'])

    def test_unguarded_child_store_cannot_mutate_a_linked_run(self):
        with self.assertRaises(OwnershipLost):
            self.runs.reserve(self.child_id, 'patient_data')
        with self.assertRaises(OwnershipLost):
            self.runs.save(self.child_id, 'cache', 'unowned', {})

    def test_terminal_parent_permits_only_bounded_source_revalidation(self):
        old_call, _ = self.child_store.reserve(self.child_id, 'patient_data', scope='dialogue_form:old')
        with self.runs.connect() as db:
            db.execute("UPDATE collaboration_runs SET state='partial',owner=NULL,lease_until=NULL WHERE run_id=?",
                       (self.parent_id,))
        verifier = RunStore(self.path, revalidate_parent_id=self.parent_id)
        call_id, _ = verifier.reserve(self.child_id, 'patient_data', scope='dialogue_form:moca')
        verifier.settle(call_id, 'completed')
        for kind, scope in [('llm', 'dialogue_form:moca'), ('retrieval', 'dialogue_form:moca'),
                            ('patient_data', 'other')]:
            with self.subTest(kind=kind, scope=scope), self.assertRaises(OwnershipLost):
                verifier.reserve(self.child_id, kind, scope=scope)
        with self.assertRaises(OwnershipLost):
            verifier.settle(old_call, 'completed')
        with self.assertRaises(OwnershipLost):
            verifier.save(self.child_id, 'cache', 'forbidden', {})
        with self.assertRaises(OwnershipLost):
            verifier.status(self.parent_id, 'failed')
        with self.assertRaises(OwnershipLost):
            verifier.create_run({'purpose': 'ordinary'}, {'seconds': 60})

    def test_child_guard_cannot_mutate_parent_or_changed_binding(self):
        with self.assertRaises(OwnershipLost):
            self.child_store.reserve(self.parent_id, 'patient_data')
        with self.runs.connect() as db:
            db.execute('UPDATE runs SET config=? WHERE run_id=?',
                       ('{"purpose":"mixed_dialogue","budget_child_run_id":"other"}', self.parent_id))
        with self.assertRaises(OwnershipLost):
            self.child_store.save(self.child_id, 'cache', 'bad-binding', {})
        with self.assertRaises(OwnershipLost):
            self.children.fenced_store(self.child_fence).reserve(self.child_id, 'patient_data')

    def test_takeover_invalidates_old_child_and_preserves_inflight_cost(self):
        node = CollaborationTask(task_id='read', kind='patient.read', objective='读取合成记录', patient_keys=['1'])
        self.children.install_graph(self.child_fence, CollaborationGraph(tasks=[node]),
                                    form_keys=set(), agent_types=set())
        self.children.claim_next(self.child_fence)
        old_call, _ = self.children.fenced_store(self.child_fence).reserve(self.child_id, 'patient_data')
        with self.runs.connect() as db:
            db.execute('UPDATE collaboration_runs SET lease_until=0 WHERE run_id=?', (self.parent_id,))
        replacement = self.parents.claim(self.parent_id, 'new-parent')
        snapshot = self.children.snapshot(self.child_id)
        self.assertEqual(snapshot['state'], 'interrupted')
        self.assertEqual(snapshot['tasks'][0]['state'], 'interrupted')
        self.assertIn({'kind': 'patient_data', 'status': 'indeterminate', 'n': 1},
                      self.runs.summary(self.child_id)['calls'])
        with self.assertRaises(OwnershipLost):
            self.children.fenced_store(self.child_fence).settle(old_call, 'completed')
        child_store = RunStore(self.path, parent_fence=replacement)
        children = CollaborationStore(child_store)
        new_fence = children.claim(self.child_id, 'new-child')
        self.assertEqual(new_fence.parent, replacement)
        new_call, _ = children.fenced_store(new_fence).reserve(self.child_id, 'patient_data')
        children.fenced_store(new_fence).settle(new_call, 'completed')


if __name__ == '__main__':
    unittest.main()
