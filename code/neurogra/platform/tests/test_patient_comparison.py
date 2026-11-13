import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from neurogra.clinical.config import ModelConfig
from neurogra.clinical.storage import BudgetExceeded
from neurogra.platform.chat_database import ChatDatabase
from neurogra.platform.patient_chat import PatientChat
from neurogra.platform.patient_comparison import compare, comparison_request, compute, read_scale
from neurogra.platform.patient_workspace import WorkspaceError


def visit(day, score, *, version='样例版本', scoring='样例口径', record_id=None):
    return {'date': day, 'record_id': record_id or day, 'fields': [
        ['总分', str(score)], ['量表版本', version], ['评分口径', scoring]]}


class PatientComparisonTests(unittest.TestCase):
    def test_longitudinal_reads_use_supplied_shared_budget(self):
        workspace = Mock()
        workspace.schema = SimpleNamespace(forms=[SimpleNamespace(key='moca'), SimpleNamespace(key='plasma')])
        dataset = {'moca': [visit('2025-01-01', 26), visit('2025-02-01', 23)],
                   'plasma': [{'date': '2025-02-01', 'record_id': 'p1', 'fields': [['项目', '样例值']]}]}
        workspace.form.side_effect = lambda _session, _patient, key, **_: {'title': key, 'records': dataset[key]}
        request = comparison_request('MoCA 评分下降的那次，同期血浆有什么记录？')
        scopes = []

        def read_patient(scope, read):
            scopes.append(scope)
            return read()

        compare(workspace, {}, {'patientKey': '1'}, request, {}, lambda: None, read_patient=read_patient)
        self.assertEqual(scopes, ['longitudinal:moca', 'longitudinal:plasma'])
        self.assertEqual(workspace.form.call_count, 2)

    def test_longitudinal_budget_denial_does_not_dispatch_form_read(self):
        workspace = Mock()
        workspace.schema = SimpleNamespace(forms=[SimpleNamespace(key='moca')])
        request = comparison_request('MoCA 最近两次趋势')
        def denied(_scope, _read):
            raise BudgetExceeded('patient_data')
        with self.assertRaises(BudgetExceeded):
            compare(workspace, {}, {'patientKey': '1'}, request, {}, lambda: None, read_patient=denied)
        workspace.form.assert_not_called()

    def test_longitudinal_read_does_not_swallow_lost_ownership(self):
        workspace = Mock()
        workspace.schema = SimpleNamespace(forms=[SimpleNamespace(key='moca')])
        workspace.form.side_effect = WorkspaceError('执行权已失效。', 409)
        request = comparison_request('MoCA 最近两次趋势')
        with self.assertRaisesRegex(WorkspaceError, '执行权已失效'):
            compare(workspace, {}, {'patientKey': '1'}, request, {}, lambda: None)

    def test_one_record_is_a_partial_longitudinal_result(self):
        workspace = Mock()
        workspace.schema = SimpleNamespace(forms=[SimpleNamespace(key='moca')])
        workspace.form.return_value = {'title': 'MoCA', 'records': [visit('2025-01-01', 26)]}
        request = comparison_request('MoCA 最近两次趋势')
        _, _, state = compare(workspace, {}, {'patientKey': '1'}, request, {}, lambda: None)
        self.assertEqual(state['result_status'], 'partial')

    def test_filters_unsafe_records_and_calculates_full_series(self):
        records = [visit(f'2025-{month:02}-01', score) for month, score in enumerate([26, 24, 25, 23, 22, 24, 21], 1)]
        records.extend([visit('', 30), visit('2025-08-01', 28, version=''),
                        visit('2025-09-01', 20), visit('2025-09-01', 24, record_id='conflict')])
        request = comparison_request('MoCA 2025年趋势')
        data = compute(read_scale({'records': records}, None, request['window']))
        self.assertEqual((data['included'], data['excluded'], data['direction']), (7, 4, '有波动'))
        self.assertEqual(data['changes'][1]['delta'], '+1')
        self.assertEqual(data['first']['value'], '26')
        self.assertEqual(data['last']['value'], '21')

    def test_relative_dates_use_same_scale_and_calendar(self):
        first = comparison_request('MMSE 最近两次相差多少？')
        self.assertEqual(first['window']['last'], 2)
        followup = comparison_request('只看近一年', first, today=date(2024, 2, 29))
        self.assertEqual(followup['scale'], 'mmse')
        self.assertEqual(followup['window'], {'start': '2023-02-28', 'end': '2024-02-29', 'last': None})
        self.assertIsNone(comparison_request('血压最近有什么趋势？', first))

    def test_patient_chat_cross_form_clarification_dependency_and_reuse(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = ChatDatabase(root)
            store = SimpleNamespace(database=lambda: database, transaction=database.transaction)
            workspace = Mock(base_url='http://localhost', source_id='synthetic-source')
            workspace.schema = SimpleNamespace(project_id=8, forms=[
                SimpleNamespace(key=key) for key in ('moca', 'mmse', 'plasma', 'csf')])
            workspace.patient.side_effect = lambda _session, key, **_: {'patientKey': key, 'id': key, 'name': '合成患者'}
            dataset = {'moca': [visit('2025-01-01', 26), visit('2025-02-01', 23), visit('2025-03-01', 21)],
                       'plasma': [{'date': '2025-02-01', 'record_id': 'p1', 'fields': [['项目', '样例值']]}],
                       'csf': []}
            workspace.form.side_effect = lambda _session, _patient, key, **_: {'title': key, 'records': dataset[key]}
            app = SimpleNamespace(root=root, config=SimpleNamespace(model=SimpleNamespace(model='sample')))
            chat = PatientChat(app, workspace, Mock(), store=store)
            session = {'account': 'sample'}
            sid = chat.create(session, '11')['id']

            def send(message, serial):
                body = {'message': message, 'request_id': f'comparison-{serial}'}
                return chat.send(session, '11', sid, body, Mock())['reply']

            pending = send('MoCA 评分下降的那次，同期检验有什么记录？', 1)
            self.assertIn('请指定', pending['text'])
            self.assertEqual(pending['collaboration'][0]['status'], 'waiting')
            self.assertEqual(workspace.form.call_count, 1)
            reply = send('2025-02-01', 2)
            self.assertTrue(reply['collaboration'][0]['reused'])
            self.assertEqual(reply['collaboration'][1]['depends_on'], '量表访视')
            self.assertIn('找到 1 条', reply['text'])
            self.assertEqual(chat.messages(session, '11', sid)['messages'][-1], reply)
            self.assertEqual(send('2025-02-01', 2), reply)
            after = send('2025-03-01', 3)
            self.assertTrue(after['collaboration'][0]['reused'])
            self.assertFalse(after['collaboration'][1]['reused'])
            self.assertNotIn('样例值', str(after))

            revised = send('改成访视前后三天，只看血浆', 4)
            self.assertIn('2025-02-26 至 2025-03-04', revised['text'])
            self.assertEqual(revised['workflow']['removed'], ['脑脊液'])
            self.assertEqual(revised['workflow']['reads'], 2)
            self.assertEqual(revised['workflow']['reused'], 2)
            self.assertEqual(revised['workflow']['model_calls'], 0)

            dataset['moca'][2] = visit('2025-03-01', 20)
            corrected = send('只看血浆', 5)
            self.assertIn('为 20', corrected['text'])
            actions = {task['title']: task['action'] for task in corrected['workflow']['tasks']}
            self.assertEqual(actions['计算量表变化'], 'executed')
            self.assertEqual(actions['确认访视'], 'executed')
            self.assertEqual(actions['血浆'], 'reused')

            app.config.model = ModelConfig()
            chat.transport = Mock(return_value={'message': {'content': json.dumps({
                'action': 'compare', 'radius': 7, 'cross': True, 'lab_keys': ['plasma']})}})
            planned = send('改成访视前后一周，只看血浆', 6)
            self.assertEqual(planned['workflow']['route'], 'model')
            self.assertEqual(planned['workflow']['model_calls'], 1)
            self.assertIn('2025-02-22 至 2025-03-08', planned['text'])
            chat.transport.assert_called_once()
            self.assertEqual(chat.messages(session, '11', sid)['messages'][-1], planned)

            chat.transport.return_value = {'message': {'content': '{"action":"compare","radius":900}'}}
            before_reads = workspace.form.call_count
            invalid = send('改成访视前后一周', 7)
            self.assertEqual(invalid['workflow']['route'], 'clarify')
            self.assertEqual(workspace.form.call_count, before_reads)

            app.config.patient_comparison_workflow = False
            fixed = send('改成访视前后三天，只看血浆', 8)
            self.assertIn('2025-02-26 至 2025-03-04', fixed['text'])
            self.assertNotIn('workflow', fixed)
