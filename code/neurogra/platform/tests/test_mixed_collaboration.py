import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

from neurogra.clinical.collaboration_contracts import SubjectScope
from neurogra.clinical.collaboration_store import CollaborationConflict
from neurogra.clinical.storage import BudgetExceeded, RunStore
from neurogra.platform.configuration import resolve_config
from neurogra.platform.mixed_collaboration import MixedCollaboration, mixed_run_id
from neurogra.platform.model_queue import ModelQueueError
from neurogra.platform.patient_workspace import WorkspaceError


class MixedCollaborationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = resolve_config(root=self.root, profile='patient-only')
        self.child_id = 'ab' * 16
        self.scope = SubjectScope(source_id='synthetic-source', project_id='8', primary_patient='1',
                                  comparison_patients=['2'], cohort_binding='roster-hash')
        self.branches = {
            'focused': {'kind': 'dialogue.delegate', 'question': '局部分析', 'patient_keys': ['1']},
            'comparison': {'kind': 'comparison.run', 'question': '比较 MoCA',
                           'patient_keys': ['1', '2'], 'form_keys': ['moca']},
            'cohort': {'kind': 'cohort.aggregate', 'question': '统计 MoCA',
                       'patient_keys': [], 'form_keys': ['moca']},
            'report': {'kind': 'report.request', 'question': '生成报告', 'patient_keys': ['1']},
        }
        self.check = Mock()

    def coordinator(self, *, scope=None, branches=None, resume=False, binding='request-binding'):
        return MixedCollaboration(self.config, self.child_id, scope or self.scope, branches or self.branches,
            allowed_forms={'moca'}, binding=binding, execution_digest='execution-version',
            check=self.check, resume=resume)

    def test_all_branches_and_synthesis_are_persisted_and_replayed(self):
        values = {'focused': {'text': '合成局部分析'}, 'comparison': {'source_fingerprint': 'comparison-v1'},
                  'cohort': {'source_fingerprint': 'cohort-v1'}, 'report': {'job_id': 'synthetic-job'}}
        with self.coordinator() as run:
            for key, value in values.items():
                self.assertEqual(run.run(key, lambda value=value: value,
                                         partial=lambda _: key == 'report'), value)
            run.finish({'answer': '合成结果'}, partial=True)
            snapshot = run.coordination.snapshot(run.run_id)
        self.assertEqual(snapshot['state'], 'partial')
        self.assertEqual([item['kind'] for item in snapshot['tasks']],
                         ['dialogue.delegate', 'comparison.run', 'cohort.aggregate',
                          'report.request', 'answer.synthesize'])
        with closing(sqlite3.connect(self.config.resolve(self.config.database))) as db:
            parents = db.execute('''SELECT parent_id FROM collaboration_edges
                WHERE run_id=? AND child_id='mixed_synthesis' ORDER BY parent_id''', (mixed_run_id(self.child_id),))
            self.assertEqual([row[0] for row in parents],
                             ['mixed_cohort', 'mixed_comparison', 'mixed_focused', 'mixed_report'])
        with self.coordinator(resume=True) as run:
            for key, value in values.items():
                self.assertEqual(run.run(key, lambda value=value: value), value)
            run.finish({'answer': '合成结果'}, partial=True)

    def test_child_and_other_branches_debit_the_parent_read_budget(self):
        with self.coordinator() as run:
            child_store = RunStore(run.store.path, parent_fence=run.fence)
            child_store.create_run({'purpose': 'patient_dialogue', 'budget_parent_run_id': run.run_id},
                                 {'seconds': 60, 'patient_data': 20}, run_id=self.child_id)
            for index in range(11):
                call, _ = child_store.reserve(self.child_id, 'patient_data', scope=f'child-{index}')
                child_store.settle(call, 'completed')
            self.assertEqual(run.read_patient('comparison', lambda: 'synthetic'), 'synthetic')
            with self.assertRaises(BudgetExceeded):
                run.read_patient('cohort', lambda: self.fail('over-budget read must not execute'))
            calls = run.store.summary(run.run_id)['aggregate_calls']
            self.assertEqual(sum(row['n'] for row in calls if row['kind'] == 'patient_data'), 12)

    def test_read_with_unknown_outcome_is_not_refunded(self):
        with self.coordinator() as run:
            with self.assertRaises(TimeoutError):
                run.read_patient('comparison', lambda: (_ for _ in ()).throw(TimeoutError('unknown')))
            calls = run.store.summary(run.run_id)['calls']
            self.assertIn({'kind': 'patient_data', 'status': 'indeterminate', 'n': 1}, calls)

    def test_parent_read_does_not_settle_after_turn_validity_is_lost(self):
        checks = Mock(side_effect=[None, None, WorkspaceError('执行权已失效。', 409)])
        with self.assertRaisesRegex(WorkspaceError, '执行权已失效'):
            with MixedCollaboration(self.config, self.child_id, self.scope, self.branches,
                    allowed_forms={'moca'}, binding='request-binding', execution_digest='execution-version',
                    check=checks, resume=False) as run:
                run.read_patient('comparison', lambda: 'synthetic')
        calls = run.store.summary(run.run_id)['calls']
        self.assertIn({'kind': 'patient_data', 'status': 'indeterminate', 'n': 1}, calls)
        self.assertFalse(any(row['status'] == 'completed' for row in calls))

    def test_finish_marks_outstanding_read_unknown_and_does_not_accept_late_settlement(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def slow_read():
            started.set()
            if not release.wait(5):
                raise TimeoutError('test_read_not_released')
            return 'late result'

        with self.coordinator() as run, ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run.read_patient, 'comparison', slow_read)
            self.assertTrue(started.wait(3))
            for key in ('focused', 'comparison', 'cohort', 'report'):
                run.run(key, lambda: {'synthetic': True})
            run.finish({'answer': '部分完成'}, partial=True)
            release.set()
            with self.assertRaises(CollaborationConflict):
                future.result(timeout=3)
            calls = run.store.summary(run.run_id)['calls']
            self.assertIn({'kind': 'patient_data', 'status': 'indeterminate', 'n': 1}, calls)

    def test_interrupted_branch_reuses_committed_nodes_and_rejects_changed_data(self):
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            with self.coordinator() as run:
                run.run('focused', lambda: {'text': '合成局部分析'})
                run.run('comparison', lambda: {'source_fingerprint': 'comparison-v1'})
                raise RuntimeError('interrupted')
        with self.assertRaisesRegex(WorkspaceError, '数据已变化'):
            with self.coordinator(resume=True) as run:
                run.run('focused', lambda: {'text': '合成局部分析'})
                run.run('comparison', lambda: {'source_fingerprint': 'comparison-v2'})
        calls = []
        with self.coordinator(resume=True) as run:
            run.run('focused', lambda: {'text': '合成局部分析'})
            run.run('comparison', lambda: {'source_fingerprint': 'comparison-v1'})
            run.run('cohort', lambda: calls.append('cohort') or {'source_fingerprint': 'cohort-v1'})
            run.run('report', lambda: calls.append('report') or {'job_id': 'synthetic-job'})
            run.finish({'answer': '合成结果'})
        self.assertEqual(calls, ['cohort', 'report'])

    def test_scope_plan_and_form_changes_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            with self.coordinator():
                raise RuntimeError('interrupted')
        changed_scope = self.scope.model_copy(update={'comparison_patients': ['3']})
        with self.assertRaises((CollaborationConflict, WorkspaceError)):
            with self.coordinator(scope=changed_scope, resume=True):
                pass
        changed_plan = {**self.branches, 'comparison': {**self.branches['comparison'], 'form_keys': ['foreign']}}
        with self.assertRaises(WorkspaceError):
            with self.coordinator(branches=changed_plan, resume=True):
                pass

    def test_partial_branch_does_not_erase_other_results(self):
        with self.coordinator() as run:
            run.run('focused', lambda: {'text': '合成局部分析'})
            with self.assertRaises(WorkspaceError):
                run.run('comparison', lambda: (_ for _ in ()).throw(WorkspaceError('比较条件不明确。', 400)))
            self.assertEqual(run.run('cohort', lambda: {'text': '合成统计'}), {'text': '合成统计'})
            run.run('report', lambda: {'job_id': 'synthetic-job'})
            run.finish({'answer': '部分完成'}, partial=True)
        with self.coordinator(resume=True) as run:
            with self.assertRaisesRegex(WorkspaceError, '比较条件不明确'):
                run.run('comparison', lambda: self.fail('must reuse recorded partial outcome'))

    def test_read_only_branch_timeout_is_partial_and_replayed_without_retry(self):
        with self.coordinator() as run:
            run.run('focused', lambda: {'text': '合成局部分析'})
            with self.assertRaisesRegex(WorkspaceError, '超时') as error:
                run.run('comparison', lambda: (_ for _ in ()).throw(TimeoutError('private upstream detail')))
            self.assertEqual(error.exception.status, 504)
            self.assertNotIn('private upstream detail', str(error.exception))
            self.assertEqual(run.run('cohort', lambda: {'text': '合成统计'}), {'text': '合成统计'})
            run.run('report', lambda: {'job_id': 'synthetic-job'})
            run.finish({'answer': '部分完成'}, partial=True)
            node = next(row for row in run.coordination.snapshot(run.run_id)['tasks']
                        if row['task_id'] == 'mixed_comparison')
            self.assertEqual(node['state'], 'partial')
            self.assertNotIn('private upstream detail', str(run._saved(node['result_id'])))
        with self.coordinator(resume=True) as run:
            with self.assertRaisesRegex(WorkspaceError, '超时'):
                run.run('comparison', lambda: self.fail('a timed-out branch must not be replayed'))

    def test_turn_check_timeout_and_model_ownership_loss_are_not_isolated(self):
        checks = Mock(side_effect=[None, TimeoutError('turn_guard_timeout')])
        with self.assertRaisesRegex(TimeoutError, 'turn_guard_timeout'):
            with MixedCollaboration(self.config, self.child_id, self.scope, self.branches,
                    allowed_forms={'moca'}, binding='request-binding', execution_digest='execution-version',
                    check=checks, resume=False) as run:
                run.run('comparison', lambda: {'text': '合成比较'})
        with self.coordinator(resume=True) as run:
            with self.assertRaisesRegex(ModelQueueError, 'ownership_lost'):
                run.run('focused', lambda: (_ for _ in ()).throw(ModelQueueError('model_queue_ownership_lost')))

    def test_report_submission_timeout_is_not_converted_to_a_completed_branch(self):
        with self.assertRaisesRegex(TimeoutError, 'report_result_unknown'):
            with self.coordinator() as run:
                run.run('report', lambda: (_ for _ in ()).throw(TimeoutError('report_result_unknown')))

    def test_exhausted_model_queue_is_a_replayable_partial_focused_branch(self):
        for index, reason in enumerate(('model_queue_full', 'model_queue_deadline')):
            self.child_id = f'{index + 1:02x}' * 16
            with self.subTest(reason=reason), self.coordinator() as run:
                with self.assertRaisesRegex(WorkspaceError, '模型排队') as error:
                    run.run('focused', lambda: (_ for _ in ()).throw(ModelQueueError(reason)))
                self.assertEqual(error.exception.status, 503)
                self.assertIn('结果可能未知', str(error.exception))
                for key in ('comparison', 'cohort', 'report'):
                    run.run(key, lambda: {'synthetic': True})
                run.finish({'answer': '部分完成'}, partial=True)
            with self.coordinator(resume=True) as run:
                with self.assertRaisesRegex(WorkspaceError, '模型排队'):
                    run.run('focused', lambda: self.fail('queue failure must not be retried on replay'))

    def test_timeout_during_parent_validity_recheck_is_not_recorded_as_branch_timeout(self):
        checks = Mock(side_effect=[None, WorkspaceError('对话状态已失效。', 409)])
        with self.assertRaisesRegex(WorkspaceError, '状态已失效') as error:
            with MixedCollaboration(self.config, self.child_id, self.scope, self.branches,
                    allowed_forms={'moca'}, binding='request-binding', execution_digest='execution-version',
                    check=checks, resume=False) as run:
                run.run('comparison', lambda: (_ for _ in ()).throw(TimeoutError('branch_timeout')))
        self.assertEqual(error.exception.status, 409)
        with self.coordinator(resume=True) as run:
            node = next(row for row in run.coordination.snapshot(run.run_id)['tasks']
                        if row['task_id'] == 'mixed_comparison')
            self.assertNotEqual(node['state'], 'partial')
            self.assertIsNone(node['result_id'])

    def test_expired_interrupted_parent_cannot_be_resumed(self):
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            with self.coordinator():
                raise RuntimeError('interrupted')
        with closing(sqlite3.connect(self.config.resolve(self.config.database))) as db:
            db.execute('UPDATE runs SET deadline=0 WHERE run_id=?', (mixed_run_id(self.child_id),))
            db.commit()
        with self.assertRaisesRegex(WorkspaceError, '超过运行时限'):
            with self.coordinator(resume=True):
                pass
