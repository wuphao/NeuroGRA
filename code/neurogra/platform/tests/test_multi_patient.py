import json
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from neurogra.clinical.rwe import RweComparison, RweForm
from neurogra.clinical.schemas import AgentResult, Claim
from neurogra.platform.chat_database import ChatDatabase
from neurogra.platform.application import Application
from neurogra.platform.async_chat import AsyncChat
from neurogra.platform.configuration import resolve_config
from neurogra.platform.mixed_collaboration import MixedCollaboration, mixed_run_id
from neurogra.platform.multi_patient import (
    aggregate,
    compare,
    is_cohort_question,
    is_cross_question,
    reauthorize_cohort,
)
from neurogra.platform.patient_chat import PatientChat, request_fingerprint
from neurogra.platform.patient_agents import DialoguePlan
from neurogra.clinical.collaboration_store import CollaborationStore
from neurogra.clinical.storage import BudgetExceeded, RunStore
from neurogra.platform.patient_workspace import WorkspaceError


class SyntheticWorkspace:
    source_id = 'synthetic-source'
    base_url = 'http://localhost:8081'

    def __init__(self):
        self.schema = SimpleNamespace(project_id=8, forms=[
            RweForm(key='moca', name='MoCA量表', form_id=55, date_field='visit')])
        self.allowed = {'1', '2', '3'}
        self.rows = {'1': [{'id': 101, 'patient_id': 1, 'visit': '2026-01-01', 'score': 22}],
                     '2': [{'id': 102, 'patient_id': 2, 'visit': '2026-02-01', 'score': 28}],
                     '3': []}

    def snapshot(self, session, *, refresh=False):
        return {'patients': [self.patient(session, key) for key in sorted(self.allowed)]}

    def patient(self, _session, key, *, refresh=False):
        if key not in self.allowed:
            raise WorkspaceError('患者不可访问。', 404)
        return {'patientKey': key, 'id': 'P' + key, 'name': '合成患者' + key}

    def authorize_forms(self, session, key):
        self.patient(session, key)

    def reader(self, _session):
        return self

    def query(self, path, body):
        if path == '/form/getFormDetailsBatch':
            return {'55': {'fields': [{'enName': 'score', 'cnName': '总分'}]}}
        rows = [row for key in body['patientIds'] for row in self.rows.get(str(key), [])]
        return {'55': {'list': rows, 'pagination': {'total': len(rows)}}}

    def form(self, session, key, form_key, **kwargs):
        self.patient(session, key)
        return {'title': 'MoCA量表', 'records': [
            {'record_id': str(row['id']), 'date': row['visit'],
             'fields': [['总分', str(row['score'])]],
             'source_fields': [['score', '总分', str(row['score'])]]}
            for row in self.rows[key]]}


class MultiPatientTests(unittest.TestCase):
    def setUp(self):
        self.workspace = SyntheticWorkspace()
        self.session = {'account': 'synthetic'}
        self.check = lambda: None

    def test_authorized_cohort_denominator_and_permission_recheck(self):
        question = '项目中 MoCA 总分低于 26 的患者占多少？'
        self.assertTrue(is_cohort_question(question))
        result = aggregate(self.workspace, self.session, question, self.check)
        self.assertIn('符合条件的患者 1 人', result['text'])
        self.assertIn('有效数值 2 人的 50.00%', result['text'])
        self.assertIn('缺失 1 人', result['text'])
        binding = {**result, 'source_id': self.workspace.source_id, 'project_id': '8'}
        reauthorize_cohort(self.workspace, self.session, binding)
        self.workspace.allowed.remove('2')
        with self.assertRaises(WorkspaceError):
            reauthorize_cohort(self.workspace, self.session, binding)

    def test_failed_cohort_batch_is_partial_even_when_failure_is_later_recovered(self):
        original, failed_once = self.workspace.query, False

        def sometimes_unavailable(path, body):
            nonlocal failed_once
            if path == '/form/queryDataBatch' and not failed_once:
                failed_once = True
                raise WorkspaceError('合成数据源暂不可用。', 503)
            return original(path, body)

        with patch.object(self.workspace, 'query', side_effect=sometimes_unavailable):
            result = aggregate(self.workspace, self.session,
                               '统计项目中 MoCA 总分低于 26 的比例', self.check)
        self.assertEqual(result['result_status'], 'partial')
        self.assertIn('读取失败 3 人', result['text'])

    def test_explicit_comparison_uses_each_patients_own_records(self):
        result = compare(self.workspace, self.session, self.workspace.patient(self.session, '1'), ['2'],
                         '比较两位患者的 MoCA', self.check)
        self.assertEqual(result['patients'], ['1', '2'])
        self.assertIn('合成患者1（P1） · 2026-01-01：总分：22', result['text'])
        self.assertIn('合成患者2（P2） · 2026-02-01：总分：28', result['text'])
        self.assertNotIn('后一位患者减前一位', result['text'])  # Version and scoring are unknown.
        self.assertEqual(result['result_status'], 'partial')
        self.assertIn('版本或评分口径不足', result['text'])

    def test_comparison_does_not_read_another_patient_after_budget_is_exhausted(self):
        dispatched = []

        def limited(scope, read):
            if dispatched:
                raise BudgetExceeded('patient_data')
            dispatched.append(scope)
            return read()

        with patch.object(self.workspace, 'form', wraps=self.workspace.form) as form:
            with self.assertRaises(BudgetExceeded):
                compare(self.workspace, self.session, self.workspace.patient(self.session, '1'), ['2'],
                        '比较两位患者的 MoCA', self.check, read_patient=limited)
        self.assertEqual(form.call_count, 1)
        self.assertEqual(len(dispatched), 1)

    def test_cohort_budget_exhaustion_retains_earlier_batch_as_partial(self):
        self.workspace.allowed.update(str(index) for index in range(4, 102))
        dispatched = []

        def limited(scope, read):
            if dispatched:
                raise BudgetExceeded('patient_data')
            dispatched.append(scope)
            return read()

        with patch.object(self.workspace, 'query', wraps=self.workspace.query) as query:
            result = aggregate(self.workspace, self.session, '统计项目中 MoCA 总分低于 26 的比例',
                               self.check, read_patient=limited)
        self.assertEqual(sum(call.args[0] == '/form/queryDataBatch' for call in query.call_args_list), 1)
        self.assertEqual(result['result_status'], 'partial')
        self.assertIn('预算', result['text'])
        self.assertIn('读取失败 0 人', result['text'])
        self.assertIn('1 人因读取预算耗尽尚未查询', result['text'])

    def test_cohort_read_does_not_swallow_lost_ownership(self):
        def lost(_scope, _read):
            raise WorkspaceError('执行权已失效。', 409)
        with self.assertRaisesRegex(WorkspaceError, '执行权已失效'):
            aggregate(self.workspace, self.session, '统计项目中 MoCA 总分低于 26 的比例',
                      self.check, read_patient=lost)

    def test_missing_comparison_patient_record_stays_partial(self):
        result = compare(self.workspace, self.session, self.workspace.patient(self.session, '1'), ['3'],
                         '比较两位患者的 MoCA', self.check)
        self.assertIn('无记录', result['text'])
        self.assertEqual(result['result_status'], 'partial')

    def test_verified_scale_versions_allow_completed_comparison(self):
        form = self.workspace.schema.forms[0]
        self.workspace.schema.forms[0] = form.model_copy(update={'comparison': RweComparison(
            score_field='score', fixed_version='合成版本', fixed_scoring='合成口径')})
        result = compare(self.workspace, self.session, self.workspace.patient(self.session, '1'), ['2'],
                         '比较两位患者的 MoCA', self.check)
        self.assertIn('后一位患者减前一位为 +6 分', result['text'])
        self.assertEqual(result['result_status'], 'completed')

    def test_source_fingerprints_change_even_when_displayed_comparison_or_statistic_does_not(self):
        question = '项目中 MoCA 总分低于 26 的患者占多少？'
        first_compare = compare(self.workspace, self.session, self.workspace.patient(self.session, '1'), ['2'],
                                '比较两位患者的 MoCA', self.check)
        first_cohort = aggregate(self.workspace, self.session, question, self.check)
        self.workspace.rows['1'].append({'id': 100, 'patient_id': 1, 'visit': '2025-01-01', 'score': 20})
        next_compare = compare(self.workspace, self.session, self.workspace.patient(self.session, '1'), ['2'],
                               '比较两位患者的 MoCA', self.check)
        next_cohort = aggregate(self.workspace, self.session, question, self.check)
        self.assertEqual(first_compare['text'], next_compare['text'])
        self.assertEqual(first_cohort['text'], next_cohort['text'])
        self.assertNotEqual(first_compare['source_fingerprint'], next_compare['source_fingerprint'])
        self.assertNotEqual(first_cohort['source_fingerprint'], next_cohort['source_fingerprint'])

    def test_single_patient_longitudinal_comparison_is_not_cross_patient(self):
        self.assertFalse(is_cross_question('比较患者最近两次 MoCA', []))
        self.assertTrue(is_cross_question('比较两位患者的 MoCA', []))

    def test_explicitly_declined_operations_do_not_dispatch_other_patient_tools(self):
        cases = [
            ('不要比较两位患者，只统计项目中 MoCA 总分低于 26 的比例', 'compare', 'aggregate', '50.00%'),
            ('不要统计群体，只比较两位患者的 MoCA', 'aggregate', 'compare', 'P2'),
        ]
        for question, declined, requested, heading in cases:
            with self.subTest(question=question), TemporaryDirectory() as temporary:
                root = Path(temporary)
                app = Application(root, resolve_config(root=root, profile='patient-only'))
                chat = PatientChat(app, self.workspace, Mock())
                sid = chat.create(self.session, '1')['id']
                functions = {'compare': compare, 'aggregate': aggregate}
                with patch('neurogra.platform.multi_patient.' + declined) as refused, \
                     patch('neurogra.platform.multi_patient.' + requested, wraps=functions[requested]) as allowed:
                    reply = chat.send(self.session, '1', sid, {'request_id': 'negative-operation',
                        'message': question, 'comparison_patient_ids': ['2']}, self.check)['reply']
                refused.assert_not_called()
                allowed.assert_called_once()
                self.assertIn(heading, reply['text'])

    def test_urgent_question_returns_immediately_without_model_or_report(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
            app = SimpleNamespace(root=root, config=resolve_config(root=root, profile='patient-only'))
            analyses = Mock()
            chat = PatientChat(app, self.workspace, analyses, store=store)
            sid = chat.create(self.session, '1')['id']
            reply = chat.send(self.session, '1', sid, {'request_id': 'urgent-1',
                              'message': '患者突然言语不清，综合分析这个患者'}, self.check)['reply']
            self.assertIn('急救', reply['text'])
            analyses.report_status.assert_not_called()

    def test_chat_hides_cross_patient_history_after_access_revoked(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction,
                                    memory=lambda scope: nullcontext(SimpleNamespace(
                                        format_for_system_prompt=lambda name: '')))
            app = SimpleNamespace(root=root, config=resolve_config(root=root, profile='patient-only'))
            chat = PatientChat(app, self.workspace, Mock(), store=store)
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'compare-1', 'message': '比较两位患者的 MoCA', 'comparison_patient_ids': ['2']}
            result = chat.send(self.session, '1', sid, body, self.check)['reply']
            self.assertIn('P2', result['text'])
            chat.model_reply = Mock(return_value=('根据前面的比较，P2 的分数为 28。', []))
            chat.send(self.session, '1', sid, {'request_id': 'derived-1', 'message': '你好'}, self.check)
            self.assertTrue(db.dependencies(sid))
            self.workspace.allowed.remove('2')
            self.assertEqual(chat.messages(self.session, '1', sid)['messages'], [])
            with self.assertRaises(WorkspaceError):
                chat.send(self.session, '1', sid, body, self.check)
            chat.model_reply = Mock(return_value=('可以继续讨论当前患者。', []))
            chat.send(self.session, '1', sid, {'request_id': 'followup-1', 'message': '你好'}, self.check)
            self.assertEqual(chat.model_reply.call_args.args[2], [])

    def test_comparison_revoked_after_read_is_not_committed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
            app = SimpleNamespace(root=root, config=resolve_config(root=root, profile='patient-only'))
            chat = PatientChat(app, self.workspace, Mock(), store=store)
            sid = chat.create(self.session, '1')['id']

            def compare_then_revoke(*args, **kwargs):
                result = compare(*args, **kwargs)
                self.workspace.allowed.remove('2')
                return result

            body = {'request_id': 'comparison-revoked', 'message': '比较两位患者的 MoCA',
                    'comparison_patient_ids': ['2']}
            with patch('neurogra.platform.patient_chat.multi_patient.compare', side_effect=compare_then_revoke):
                with self.assertRaises(WorkspaceError):
                    chat.send(self.session, '1', sid, body, self.check)
            self.assertFalse(any(row['role'] == 'assistant' for row in db.get_messages(sid)))
            self.assertEqual([row['patient_key'] for row in db.subjects(sid)], ['1'])

    def test_cohort_roster_changed_after_read_is_not_committed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
            app = SimpleNamespace(root=root, config=resolve_config(root=root, profile='patient-only'))
            chat = PatientChat(app, self.workspace, Mock(), store=store)
            sid = chat.create(self.session, '1')['id']

            def aggregate_then_revoke(*args, **kwargs):
                result = aggregate(*args, **kwargs)
                self.workspace.allowed.remove('2')
                return result

            body = {'request_id': 'cohort-revoked', 'message': '项目中 MoCA 总分低于 26 的患者占多少？'}
            with patch('neurogra.platform.patient_chat.multi_patient.aggregate', side_effect=aggregate_then_revoke):
                with self.assertRaises(WorkspaceError):
                    chat.send(self.session, '1', sid, body, self.check)
            self.assertFalse(any(row['role'] == 'assistant' for row in db.get_messages(sid)))
            self.assertEqual(db.cohorts(sid), [])

    def test_standalone_comparison_and_cohort_have_durable_task_graphs(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = Application(root, resolve_config(root=root, profile='patient-only'))
            chat = PatientChat(app, self.workspace, Mock())
            sid = chat.create(self.session, '1')['id']
            bodies = [
                {'request_id': 'single-comparison', 'message': '比较两位患者的 MoCA',
                 'comparison_patient_ids': ['2']},
                {'request_id': 'single-cohort', 'message': '统计项目中 MoCA 总分低于 26 的比例'},
            ]
            engine = RunStore(app.config.resolve(app.config.database))
            for body, kind in zip(bodies, ('comparison.run', 'cohort.aggregate'), strict=True):
                reply = chat.send(self.session, '1', sid, body, self.check)['reply']
                self.assertTrue(reply['text'])
                binding = chat.store.database().collaboration_binding(sid, body['request_id'])
                graph = CollaborationStore(engine).snapshot(mixed_run_id(binding['run_id']))
                self.assertEqual([task['kind'] for task in graph['tasks']], [kind, 'answer.synthesize'])
                self.assertEqual(graph['state'], 'partial' if kind == 'comparison.run' else 'completed')

    def test_standalone_comparison_and_cohort_timeout_return_partial_conversation(self):
        cases = (
            ('compare', {'request_id': 'single-timeout-compare', 'message': '比较两位患者的 MoCA',
                         'comparison_patient_ids': ['2']}, '患者比较未完成'),
            ('aggregate', {'request_id': 'single-timeout-cohort',
                           'message': '统计项目中 MoCA 总分低于 26 的比例'}, '群体统计未完成'),
        )
        for branch, body, expected in cases:
            with self.subTest(branch=branch), TemporaryDirectory() as temporary:
                root = Path(temporary)
                app = Application(root, resolve_config(root=root, profile='patient-only'))
                chat = PatientChat(app, self.workspace, Mock())
                sid = chat.create(self.session, '1')['id']
                with patch('neurogra.platform.multi_patient.' + branch,
                           side_effect=TimeoutError('private upstream timeout')) as failed:
                    reply = chat.send(self.session, '1', sid, body, self.check)['reply']
                    repeated = chat.send(self.session, '1', sid, body, self.check)['reply']
                self.assertEqual(reply['result_status'], 'partial')
                self.assertIn(expected, reply['text'])
                self.assertIn('超时', reply['text'])
                self.assertNotIn('private upstream timeout', reply['text'])
                self.assertEqual(repeated['text'], reply['text'])
                failed.assert_called_once()
                binding = chat.store.database().collaboration_binding(sid, body['request_id'])
                graph = CollaborationStore(RunStore(app.config.resolve(app.config.database))).snapshot(
                    mixed_run_id(binding['run_id']))
                self.assertEqual(graph['state'], 'partial')
                self.assertEqual(sum(row['role'] == 'assistant'
                                     for row in chat.store.database().get_messages(sid)), 1)

    def test_standalone_comparison_checks_sources_before_chat_commit_replay(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = Application(root, resolve_config(root=root, profile='patient-only'))
            chat = PatientChat(app, self.workspace, Mock())
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'comparison-commit', 'message': '比较两位患者的 MoCA',
                    'comparison_patient_ids': ['2']}
            with patch.object(chat.store, 'transaction', side_effect=RuntimeError('chat_commit_interruption')):
                with self.assertRaisesRegex(RuntimeError, 'chat_commit_interruption'):
                    chat.send(self.session, '1', sid, body, self.check)
            self.workspace.rows['2'][0]['score'] = 27
            with self.assertRaises(WorkspaceError) as changed:
                chat.send(self.session, '1', sid, body, self.check, resume_collaboration=True)
            self.assertEqual(changed.exception.status, 409)
            self.workspace.rows['2'][0]['score'] = 28
            reply = chat.send(self.session, '1', sid, body, self.check, resume_collaboration=True)['reply']
            self.assertIn('总分：28', reply['text'])
            self.assertEqual(sum(row['role'] == 'assistant' for row in chat.store.database().get_messages(sid)), 1)

    def test_mixed_comparison_and_cohort_keep_both_scopes_and_results(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
            app = SimpleNamespace(root=root, config=resolve_config(root=root, profile='patient-only'))
            chat = PatientChat(app, self.workspace, Mock(), store=store)
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'mixed-one', 'comparison_patient_ids': ['2'],
                    'message': '比较两位患者的 MoCA，并统计项目中 MoCA 总分低于 26 的比例'}
            reply = chat.send(self.session, '1', sid, body, self.check)['reply']
            self.assertIn('患者比较', reply['text'])
            self.assertIn('P2', reply['text'])
            self.assertIn('群体统计', reply['text'])
            self.assertIn('50.00%', reply['text'])
            self.assertEqual(reply['result_status'], 'partial')
            self.assertEqual({row['patient_key'] for row in db.subjects(sid)}, {'1', '2'})
            self.assertEqual(len(db.cohorts(sid)), 1)
            self.assertEqual(chat.messages(self.session, '1', sid)['messages'][-1]['result_status'], 'partial')
            self.workspace.allowed.remove('2')
            self.assertEqual(chat.messages(self.session, '1', sid)['messages'], [])

    def test_mixed_comparison_and_cohort_continue_independently_after_timeout(self):
        for failed_branch in ('compare', 'aggregate'):
            with self.subTest(failed_branch=failed_branch), TemporaryDirectory() as temporary:
                root = Path(temporary)
                db = ChatDatabase(root)
                store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
                app = SimpleNamespace(root=root, config=resolve_config(root=root, profile='patient-only'))
                chat = PatientChat(app, self.workspace, Mock(), store=store)
                sid = chat.create(self.session, '1')['id']
                body = {'request_id': 'mixed-timeout-' + failed_branch, 'comparison_patient_ids': ['2'],
                        'message': '比较两位患者的 MoCA，并统计项目中 MoCA 总分低于 26 的比例'}
                with patch('neurogra.platform.multi_patient.' + failed_branch,
                           side_effect=TimeoutError('private upstream timeout')) as failed:
                    reply = chat.send(self.session, '1', sid, body, self.check)['reply']
                failed.assert_called_once()
                self.assertEqual(reply['result_status'], 'partial')
                self.assertNotIn('private upstream timeout', reply['text'])
                if failed_branch == 'compare':
                    self.assertIn('患者比较未完成', reply['text'])
                    self.assertIn('50.00%', reply['text'])
                    self.assertEqual(len(db.cohorts(sid)), 1)
                else:
                    self.assertIn('P2', reply['text'])
                    self.assertIn('群体统计未完成', reply['text'])
                    self.assertEqual({row['patient_key'] for row in db.subjects(sid)}, {'1', '2'})

    def test_mixed_missing_patient_selection_keeps_cohort_result(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
            app = SimpleNamespace(root=root, config=resolve_config(root=root, profile='patient-only'))
            chat = PatientChat(app, self.workspace, Mock(), store=store)
            sid = chat.create(self.session, '1')['id']
            reply = chat.send(self.session, '1', sid, {'request_id': 'mixed-two',
                'message': '比较两位患者的 MoCA，并统计项目中 MoCA 总分低于 26 的比例'}, self.check)['reply']
            self.assertIn('请先显式选择', reply['text'])
            self.assertIn('50.00%', reply['text'])
            self.assertEqual(reply['result_status'], 'partial')
            self.assertEqual(reply['interaction']['type'], 'select_patients')

    def test_comparison_cohort_and_report_are_preserved_in_one_request(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
            platform_store = SimpleNamespace(dispatched_job=lambda _key: None)
            app = SimpleNamespace(root=root, store=platform_store,
                                  config=resolve_config(root=root, profile='patient-only'))
            analyses = Mock()
            analyses.report_status.return_value = {'status': 'missing'}
            analyses.submit.return_value = {'job_id': 'synthetic-job', 'status': 'queued'}
            chat = PatientChat(app, self.workspace, analyses, store=store)
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'mixed-three', 'comparison_patient_ids': ['2'],
                    'message': '比较两位患者的 MoCA，统计项目中 MoCA 总分低于 26 的比例，并生成完整分析报告'}
            reply = chat.send(self.session, '1', sid, body, self.check)['reply']
            self.assertIn('患者比较', reply['text'])
            self.assertIn('50.00%', reply['text'])
            self.assertIn('完整报告', reply['text'])
            self.assertEqual(reply['analysis_job_id'], 'synthetic-job')
            self.assertEqual(reply['result_status'], 'partial')
            self.assertEqual({row['patient_key'] for row in db.subjects(sid)}, {'1', '2'})
            self.assertEqual(len(db.cohorts(sid)), 1)
            analyses.submit.assert_called_once()

    def test_mixed_read_budget_limits_branches_without_blocking_report(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
            app = SimpleNamespace(root=root, store=SimpleNamespace(dispatched_job=lambda _key: None),
                                  config=resolve_config(root=root, profile='patient-only'))
            analyses = Mock()
            analyses.report_status.return_value = {'status': 'missing'}
            analyses.submit.return_value = {'job_id': 'synthetic-job', 'status': 'queued'}
            chat = PatientChat(app, self.workspace, analyses, store=store)
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'mixed-budget', 'comparison_patient_ids': ['2'],
                    'message': '比较两位患者的 MoCA，统计项目中 MoCA 总分低于 26 的比例，并生成完整分析报告'}
            original_create = RunStore.create_run

            def limited(instance, config, budget, *, run_id=None):
                if config.get('purpose') == 'mixed_dialogue':
                    budget = {**budget, 'patient_data': 1}
                return original_create(instance, config, budget, run_id=run_id)

            with patch.object(RunStore, 'create_run', new=limited), \
                 patch.object(self.workspace, 'form', wraps=self.workspace.form) as form:
                reply = chat.send(self.session, '1', sid, body, self.check)['reply']
            self.assertEqual(form.call_count, 1)
            self.assertIn('患者比较未完成', reply['text'])
            self.assertIn('预算', reply['text'])
            self.assertIn('群体统计', reply['text'])
            self.assertIn('尚未查询', reply['text'])
            self.assertEqual(reply['analysis_job_id'], 'synthetic-job')
            self.assertEqual(reply['result_status'], 'partial')
            analyses.submit.assert_called_once()
            binding = db.collaboration_binding(sid, body['request_id'])
            calls = RunStore(app.config.resolve(app.config.database)).summary(mixed_run_id(binding['run_id']))['calls']
            self.assertIn({'kind': 'patient_data', 'status': 'completed', 'n': 1}, calls)

    def test_configured_dialogue_budget_limits_mixed_reads_without_changing_report_budget(self):
        from neurogra.platform.settings import load_settings, save_settings

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = resolve_config(root=root, profile='patient-only')
            saved = save_settings(root, config, {'dialogue_budget': {'patient_data': 1}})
            app = Application(root, load_settings(root))
            self.assertEqual(app.config.budget, config.budget)
            analyses = Mock()
            analyses.report_status.return_value = {'status': 'missing'}
            analyses.submit.return_value = {'job_id': 'synthetic-job', 'status': 'queued'}
            chat = PatientChat(app, self.workspace, analyses)
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'configured-budget', 'comparison_patient_ids': ['2'],
                    'message': '比较两位患者的 MoCA，统计项目中 MoCA 总分低于 26 的比例，并生成完整分析报告'}
            with patch.object(self.workspace, 'form', wraps=self.workspace.form) as form:
                reply = chat.send(self.session, '1', sid, body, self.check)['reply']
            self.assertEqual(saved.dialogue_budget.patient_data, 1)
            self.assertEqual(form.call_count, 1)
            self.assertIn('预算', reply['text'])
            self.assertEqual(reply['analysis_job_id'], 'synthetic-job')
            binding = chat.store.database().collaboration_binding(sid, body['request_id'])
            engine = RunStore(app.config.resolve(app.config.database))
            self.assertEqual(json.loads(engine.run(mixed_run_id(binding['run_id']))['limits'])['patient_data'], 1)

    def test_mixed_task_stops_when_budget_configuration_changes_before_child_dispatch(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = Application(root, resolve_config(root=root, profile='patient-only'))
            chat = PatientChat(app, self.workspace, Mock())
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'budget-changed', 'comparison_patient_ids': ['2'],
                    'message': '解释这个患者的 MoCA，比较两位患者的 MoCA'}
            authorize = self.workspace.authorize_forms

            def change_after_parent_created(session, key):
                if key == '1':
                    budget = app.config.dialogue_budget.model_copy(update={'patient_data': 2})
                    app.config = app.config.model_copy(update={'dialogue_budget': budget})
                return authorize(session, key)

            with patch.object(self.workspace, 'authorize_forms', side_effect=change_after_parent_created), \
                 patch('neurogra.platform.patient_agents.answer', return_value={
                     'text': '合成结果', 'sources': [], 'collaboration': [], 'result_status': 'completed'}) as focused:
                with self.assertRaisesRegex(WorkspaceError, '配置.*变化') as error:
                    chat.send(self.session, '1', sid, body, self.check)
            self.assertEqual(error.exception.status, 409)
            focused.assert_not_called()
            self.assertFalse(any(row['role'] == 'assistant' for row in chat.store.database().get_messages(sid)))

    def test_partial_longitudinal_comparison_does_not_become_completed_beside_a_report(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = Application(root, resolve_config(root=root, profile='patient-only'))
            analyses = Mock()
            analyses.report_status.return_value = {'status': 'missing'}
            analyses.submit.return_value = {'job_id': 'synthetic-job', 'status': 'completed'}
            chat = PatientChat(app, self.workspace, analyses)
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'partial-trend',
                    'message': '比较患者最近两次 MoCA 趋势，并生成完整分析报告'}
            reply = chat.send(self.session, '1', sid, body, self.check)['reply']
            self.assertIn('没有可比记录', reply['text'])
            self.assertEqual(reply['result_status'], 'partial')
            binding = chat.store.database().collaboration_binding(sid, body['request_id'])
            graph = CollaborationStore(RunStore(app.config.resolve(app.config.database))).snapshot(
                mixed_run_id(binding['run_id']))
            focused = next(node for node in graph['tasks'] if node['task_id'] == 'mixed_focused')
            self.assertEqual(focused['state'], 'partial')

    def test_longitudinal_budget_exhaustion_keeps_report_branch_available(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = Application(root, resolve_config(root=root, profile='patient-only'))
            analyses = Mock()
            analyses.report_status.return_value = {'status': 'missing'}
            analyses.submit.return_value = {'job_id': 'synthetic-job', 'status': 'queued'}
            chat = PatientChat(app, self.workspace, analyses)
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'trend-budget', 'message': '比较患者最近两次 MoCA 趋势，并生成完整分析报告'}
            original_create = RunStore.create_run

            def limited(instance, config, budget, *, run_id=None):
                if config.get('purpose') == 'mixed_dialogue':
                    budget = {**budget, 'patient_data': 0}
                return original_create(instance, config, budget, run_id=run_id)

            with patch.object(RunStore, 'create_run', new=limited), \
                 patch.object(self.workspace, 'form', wraps=self.workspace.form) as form:
                reply = chat.send(self.session, '1', sid, body, self.check)['reply']
            form.assert_not_called()
            self.assertIn('预算', reply['text'])
            self.assertIn('局部分析未完成', reply['text'])
            self.assertEqual(reply['analysis_job_id'], 'synthetic-job')
            self.assertEqual(reply['result_status'], 'partial')
            analyses.submit.assert_called_once()

    def test_focused_comparison_and_cohort_share_budget_and_report_stays_independent(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = Application(root, resolve_config(root=root, profile='kb-only'))
            analyses = Mock()
            analyses.report_status.return_value = {'status': 'missing'}
            analyses.submit.return_value = {'job_id': 'synthetic-job', 'status': 'queued'}
            chat = PatientChat(app, self.workspace, analyses)
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'shared-budget', 'comparison_patient_ids': ['2'],
                    'message': '解释这个患者的 MoCA，比较两位患者的 MoCA，'
                               '统计项目中 MoCA 总分低于 26 的比例，并生成完整分析报告'}
            plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)

            async def specialist(task_plan, snapshot, **_kwargs):
                task = task_plan.tasks[0]
                observation = next(item for item in snapshot.observations if item.name == '总分')
                return [AgentResult(result_id='result', task_id=task.task_id, case_version=snapshot.version,
                    status='completed', claims=[Claim(claim_id='fact', text=observation.quote,
                        kind='description', level='cognition', observation_ids=[observation.observation_id],
                        strength='descriptive')])]

            original_create = RunStore.create_run

            def limited(instance, config, budget, *, run_id=None):
                if config.get('purpose') == 'mixed_dialogue':
                    budget = {**budget, 'patient_data': 3}
                return original_create(instance, config, budget, run_id=run_id)

            with patch.object(RunStore, 'create_run', new=limited), \
                 patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
                 patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                       side_effect=specialist):
                reply = chat.send(self.session, '1', sid, body, self.check)['reply']
            self.assertIn('局部分析', reply['text'])
            self.assertIn('患者比较', reply['text'])
            self.assertIn('预算', reply['text'])
            self.assertIn('尚未查询', reply['text'])
            self.assertEqual(reply['analysis_job_id'], 'synthetic-job')
            self.assertEqual(reply['result_status'], 'partial')
            binding = chat.store.database().collaboration_binding(sid, body['request_id'])
            engine = RunStore(app.config.resolve(app.config.database))
            calls = engine.summary(mixed_run_id(binding['run_id']))['aggregate_calls']
            self.assertEqual(sum(row['n'] for row in calls if row['kind'] == 'patient_data'), 3)
            self.assertIn({'kind': 'patient_data', 'status': 'completed', 'n': 1},
                          engine.summary(binding['run_id'])['calls'])
            analyses.submit.assert_called_once()

    def test_mixed_replay_revalidates_sources_against_the_original_shared_budget(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = Application(root, resolve_config(root=root, profile='kb-only'))
            analyses = Mock()
            analyses.report_status.return_value = {'status': 'missing'}
            analyses.submit.return_value = {'job_id': 'synthetic-job', 'status': 'queued'}
            chat = PatientChat(app, self.workspace, analyses)
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'shared-budget-resume', 'comparison_patient_ids': ['2'],
                    'message': '解释这个患者的 MoCA，比较两位患者的 MoCA，'
                               '统计项目中 MoCA 总分低于 26 的比例，并生成完整分析报告'}
            plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)

            async def specialist(task_plan, snapshot, **_kwargs):
                task = task_plan.tasks[0]
                observation = next(item for item in snapshot.observations if item.name == '总分')
                return [AgentResult(result_id='result', task_id=task.task_id, case_version=snapshot.version,
                    status='completed', claims=[Claim(claim_id='fact', text=observation.quote,
                        kind='description', level='cognition', observation_ids=[observation.observation_id],
                        strength='descriptive')])]

            original_create = RunStore.create_run

            def limited(instance, config, budget, *, run_id=None):
                if config.get('purpose') == 'mixed_dialogue':
                    budget = {**budget, 'patient_data': 8}
                return original_create(instance, config, budget, run_id=run_id)

            with patch.object(RunStore, 'create_run', new=limited), \
                 patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan) as planner, \
                 patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                       side_effect=specialist) as executor:
                with patch.object(chat.store, 'transaction', side_effect=RuntimeError('chat_commit_interrupted')):
                    with self.assertRaisesRegex(RuntimeError, 'chat_commit_interrupted'):
                        chat.send(self.session, '1', sid, body, self.check)
                binding = chat.store.database().collaboration_binding(sid, body['request_id'])
                engine = RunStore(app.config.resolve(app.config.database))
                parent = mixed_run_id(binding['run_id'])
                self.assertEqual(sum(row['n'] for row in engine.summary(parent)['aggregate_calls']
                                     if row['kind'] == 'patient_data'), 4)
                reply = chat.send(self.session, '1', sid, body, self.check, resume_collaboration=True)['reply']
            self.assertIn('局部分析', reply['text'])
            self.assertIn('患者比较', reply['text'])
            self.assertIn('群体统计', reply['text'])
            self.assertEqual(reply['analysis_job_id'], 'synthetic-job')
            self.assertEqual(sum(row['n'] for row in engine.summary(parent)['aggregate_calls']
                                 if row['kind'] == 'patient_data'), 8)
            self.assertEqual(planner.call_count, 1)
            self.assertEqual(executor.call_count, 1)
            analyses.submit.assert_called_once()

    def test_replay_stops_before_source_read_when_shared_budget_is_exhausted(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = Application(root, resolve_config(root=root, profile='patient-only'))
            chat = PatientChat(app, self.workspace, Mock())
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'budget-replay', 'comparison_patient_ids': ['2'],
                    'message': '比较两位患者的 MoCA'}
            original_create = RunStore.create_run

            def limited(instance, config, budget, *, run_id=None):
                if config.get('purpose') == 'mixed_dialogue':
                    budget = {**budget, 'patient_data': 2}
                return original_create(instance, config, budget, run_id=run_id)

            with patch.object(RunStore, 'create_run', new=limited), \
                 patch.object(self.workspace, 'form', wraps=self.workspace.form) as form:
                with patch.object(chat.store, 'transaction', side_effect=RuntimeError('chat_commit_interrupted')):
                    with self.assertRaisesRegex(RuntimeError, 'chat_commit_interrupted'):
                        chat.send(self.session, '1', sid, body, self.check)
                self.assertEqual(form.call_count, 2)
                with self.assertRaisesRegex(WorkspaceError, '预算') as error:
                    chat.send(self.session, '1', sid, body, self.check, resume_collaboration=True)
            self.assertEqual(error.exception.status, 409)
            self.assertEqual(form.call_count, 2)
            self.assertFalse(any(row['role'] == 'assistant' for row in chat.store.database().get_messages(sid)))

    def test_focused_analysis_comparison_cohort_and_report_are_all_preserved(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
            app = SimpleNamespace(root=root, store=SimpleNamespace(dispatched_job=lambda _key: None),
                                  config=resolve_config(root=root, profile='patient-only'))
            analyses = Mock()
            analyses.report_status.return_value = {'status': 'missing'}
            analyses.submit.return_value = {'job_id': 'synthetic-job', 'status': 'queued'}
            chat = PatientChat(app, self.workspace, analyses, store=store)
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'four-goals', 'comparison_patient_ids': ['2'],
                    'message': '解释这个患者的 MoCA，比较两位患者的 MoCA，'
                               '统计项目中 MoCA 总分低于 26 的比例，并生成完整分析报告'}
            local = {'text': '合成局部观点', 'sources': [], 'collaboration': [],
                     'result_status': 'completed', 'workflow': {'source': 'rwe', 'route': 'model',
                         'model_calls': 1, 'removed': [], 'tasks': {}}}
            with patch('neurogra.platform.patient_agents.answer', return_value=local) as focused:
                reply = chat.send(self.session, '1', sid, body, self.check)['reply']
            self.assertIn('局部分析：\n合成局部观点', reply['text'])
            self.assertIn('患者比较', reply['text'])
            self.assertIn('50.00%', reply['text'])
            self.assertIn('完整报告', reply['text'])
            self.assertEqual(focused.call_args.args[4], '解释这个患者的 MoCA')
            self.assertEqual(reply['analysis_job_id'], 'synthetic-job')
            self.assertEqual(reply['result_status'], 'partial')
            self.assertEqual({item['title'] for item in reply['workflow']['tasks']}, {'患者比较', '群体统计', '完整报告'})
            self.assertEqual({row['patient_key'] for row in db.subjects(sid)}, {'1', '2'})
            self.assertEqual(len(db.cohorts(sid)), 1)
            analyses.submit.assert_called_once()

    def test_mixed_question_preserves_medical_connectives_in_focused_analysis(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
            app = SimpleNamespace(root=root, config=resolve_config(root=root, profile='patient-only'))
            chat = PatientChat(app, self.workspace, Mock(), store=store)
            questions = [
                ('分析患者合并糖尿病并发记忆下降的认知情况，并比较两位患者的 MoCA',
                 '合并糖尿病并发记忆下降'),
                ('分析患者病史以及认知情况，同时比较两位患者的 MoCA', '病史以及认知情况'),
                ('分析患者并未出现失语的病史，并统计项目中 MoCA 总分低于 26 的比例',
                 '并未出现失语'),
                ('患者合并糖尿病，分析认知情况，同时比较两位患者的 MoCA', '合并糖尿病'),
            ]
            for question, phrase in questions:
                with self.subTest(question=question):
                    parts = chat.focused_parts_with_multi_patient(question, ['2'])
                    self.assertIsNotNone(parts)
                    self.assertIn(phrase, parts['focused'])

            focused = chat.focused_part_with_report(
                '分析患者认知情况，合并糖尿病并未出现失语，生成完整分析报告')
            self.assertIn('合并糖尿病并未出现失语', focused)

    def test_mixed_request_sends_medical_context_to_analysis_and_runs_other_branches(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
            app = SimpleNamespace(root=root, config=resolve_config(root=root, profile='patient-only'))
            chat = PatientChat(app, self.workspace, Mock(), store=store)
            sid = chat.create(self.session, '1')['id']
            local = {'text': '合成局部结果', 'sources': [], 'collaboration': [], 'result_status': 'completed'}
            body = {'request_id': 'context-mixed', 'comparison_patient_ids': ['2'],
                    'message': '患者合并糖尿病并未出现失语，分析认知情况，同时比较两位患者的 MoCA，'
                               '并统计项目中 MoCA 总分低于 26 的比例'}
            with patch('neurogra.platform.patient_agents.answer', return_value=local) as focused:
                reply = chat.send(self.session, '1', sid, body, self.check)['reply']
            self.assertIn('合并糖尿病并未出现失语', focused.call_args.args[4])
            self.assertIn('分析认知情况', focused.call_args.args[4])
            self.assertIn('患者比较', reply['text'])
            self.assertIn('50.00%', reply['text'])

    def test_focused_analysis_survives_missing_comparison_selection(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
            app = SimpleNamespace(root=root, config=resolve_config(root=root, profile='patient-only'))
            chat = PatientChat(app, self.workspace, Mock(), store=store)
            sid = chat.create(self.session, '1')['id']
            local = {'text': '合成局部观点', 'sources': [], 'collaboration': [], 'result_status': 'completed'}
            body = {'request_id': 'mixed-missing', 'message': '解释患者的 MoCA，比较两位患者的 MoCA，'
                    '并统计项目中 MoCA 总分低于 26 的比例'}
            with patch('neurogra.platform.patient_agents.answer', return_value=local):
                reply = chat.send(self.session, '1', sid, body, self.check)['reply']
            self.assertIn('合成局部观点', reply['text'])
            self.assertIn('50.00%', reply['text'])
            self.assertEqual(reply['interaction']['type'], 'select_patients')
            self.assertEqual(reply['result_status'], 'partial')

    def test_report_runs_when_comparison_patient_selection_is_pending(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: db, transaction=db.transaction)
            platform_store = SimpleNamespace(dispatched_job=lambda _key: None)
            app = SimpleNamespace(root=root, store=platform_store,
                                  config=resolve_config(root=root, profile='patient-only'))
            analyses = Mock()
            analyses.report_status.return_value = {'status': 'missing'}
            analyses.submit.return_value = {'job_id': 'synthetic-job', 'status': 'queued'}
            chat = PatientChat(app, self.workspace, analyses, store=store)
            sid = chat.create(self.session, '1')['id']
            reply = chat.send(self.session, '1', sid, {'request_id': 'mixed-four',
                'message': '比较两位患者的 MoCA，统计项目中 MoCA 总分低于 26 的比例，并生成完整分析报告'},
                self.check)['reply']
            self.assertEqual(reply['interaction']['type'], 'select_patients')
            self.assertIn('已提交完整病例分析', reply['text'])
            self.assertEqual(reply['analysis_job_id'], 'synthetic-job')
            self.assertEqual(reply['result_status'], 'partial')
            analyses.submit.assert_called_once()

    def test_finished_report_does_not_mark_unselected_comparison_complete(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = Application(root, resolve_config(root=root, profile='patient-only'))
            analyses = Mock()
            analyses.report_status.return_value = {
                'status': 'unchanged', 'job': {'job_id': 'synthetic-job', 'status': 'succeeded'}}
            chat = PatientChat(app, self.workspace, analyses)
            sid = chat.create(self.session, '1')['id']
            reply = chat.send(self.session, '1', sid, {'request_id': 'comparison-pending',
                'message': '比较两位患者的 MoCA，并生成完整分析报告'}, self.check)['reply']
            self.assertEqual(reply['interaction']['type'], 'select_patients')
            self.assertEqual(reply['result_status'], 'partial')
            self.assertEqual(reply['analysis_job_id'], 'synthetic-job')

    def test_mixed_graph_rechecks_comparison_and_cohort_before_resuming(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = Application(root, resolve_config(root=root, profile='patient-only'))
            analyses = Mock()
            analyses.report_status.return_value = {'status': 'missing'}
            analyses.submit.return_value = {'job_id': 'synthetic-job', 'status': 'queued'}
            chat = PatientChat(app, self.workspace, analyses)
            manager = AsyncChat(chat, self.workspace)
            sid = chat.create(self.session, '1')['id']
            body = {'request_id': 'mixed-graph', 'comparison_patient_ids': ['2'],
                    'message': '比较两位患者的 MoCA，统计项目中 MoCA 总分低于 26 的比例，并生成完整分析报告',
                    'capabilities': ['async_turns_v1', 'interactions_v1']}
            digest = request_fingerprint(body['message'], None, body['comparison_patient_ids'])
            database = chat.store.database()
            database.begin_turn(sid, body['request_id'], digest, '1', 'synthetic-owner', body['message'])
            original, stopped = MixedCollaboration.run, False

            def interrupt_report(run, key, *args, **kwargs):
                nonlocal stopped
                if key == 'report' and not stopped:
                    stopped = True
                    raise RuntimeError('synthetic_interruption')
                return original(run, key, *args, **kwargs)

            with patch.object(MixedCollaboration, 'run', autospec=True, side_effect=interrupt_report):
                with self.assertRaisesRegex(RuntimeError, 'synthetic_interruption'):
                    chat.send(self.session, '1', sid, body, self.check)
                progress = manager.collaboration_progress(self.session, '1', sid, body['request_id'])
                self.assertEqual([item['kind'] for item in progress['tasks']],
                                 ['comparison.run', 'cohort.aggregate', 'report.request', 'answer.synthesize'])
                self.assertNotIn('patient_keys', str(progress))
                self.assertNotIn('合成患者', str(progress))
                self.workspace.allowed.remove('2')
                with self.assertRaises(WorkspaceError):
                    manager.collaboration_progress(self.session, '1', sid, body['request_id'])
                self.workspace.allowed.add('2')
                self.workspace.allowed.remove('3')
                with self.assertRaises(WorkspaceError):
                    manager.collaboration_progress(self.session, '1', sid, body['request_id'])
                self.workspace.allowed.add('3')
                # The old visit does not change the displayed comparison, but is still a changed source.
                self.workspace.rows['1'].append({'id': 100, 'patient_id': 1, 'visit': '2025-01-01', 'score': 20})
                with self.assertRaises(WorkspaceError) as changed:
                    chat.send(self.session, '1', sid, body, self.check, resume_collaboration=True)
                self.assertEqual(changed.exception.status, 409)
                self.workspace.rows['1'].pop()
                # A cohort-only source change must also prevent joining an old aggregate to new work.
                self.workspace.rows['3'].append({'id': 103, 'patient_id': 3, 'visit': '2026-02-01', 'score': 25})
                with self.assertRaises(WorkspaceError) as cohort_changed:
                    chat.send(self.session, '1', sid, body, self.check, resume_collaboration=True)
                self.assertEqual(cohort_changed.exception.status, 409)
                self.workspace.rows['3'].clear()
                reply = chat.send(self.session, '1', sid, body, self.check, resume_collaboration=True)['reply']
            self.assertIn('50.00%', reply['text'])
            self.assertEqual(reply['analysis_job_id'], 'synthetic-job')
            analyses.submit.assert_called_once()
            binding = database.collaboration_binding(sid, body['request_id'])
            engine = RunStore(app.config.resolve(app.config.database))
            self.assertEqual(CollaborationStore(engine).snapshot(mixed_run_id(binding['run_id']))['state'], 'partial')
            self.assertEqual(sum(row['role'] == 'assistant' for row in database.get_messages(sid)), 1)


if __name__ == '__main__':
    unittest.main()
