import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.llm import ModelGateway
from neurogra.clinical.schemas import CaseSnapshot
from neurogra.clinical.storage import RunStore
from neurogra.clinical.utils import fingerprint
from neurogra.platform.chat_database import ChatDatabase
from neurogra.platform.patient_chat import PatientChat
from neurogra.platform.patient_workspace import WorkspaceError
from neurogra.platform.report_followup import AnswerSelection, FollowupDecision, bound_snapshot


class ReportFollowupTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = ClinicalConfig(project_root=self.root)
        app = SimpleNamespace(root=self.root, config=self.config)
        self.engine = RunStore(self.config.resolve(self.config.database))
        self.snapshot = CaseSnapshot(case_id='case-a', patient_id='SYN-001', raw_input_hash='example', records=[],
            observations=[
                {'observation_id': 'cog', 'domains': ['cognition'], 'name': 'MoCA 总分', 'value': 23,
                 'status': 'observed', 'source_refs': [], 'quote': 'MoCA 总分 23'},
                {'observation_id': 'lab', 'domains': ['laboratory'], 'name': '检验项目', 'value': 12,
                 'status': 'observed', 'source_refs': [], 'quote': '检验项目 12'},
            ],
            inventory=[{'domain': 'cognition', 'availability': 'available', 'observation_ids': ['cog']},
                       {'domain': 'laboratory', 'availability': 'available', 'observation_ids': ['lab']}])
        self.report = {'patient_id': 'SYN-001', 'run_id': 'origin', 'summary': '合成摘要',
                       'claims': [{'claim_id': 'original', 'text': '已有报告结论'}],
                       'snapshot_binding': {'case_id': self.snapshot.case_id, 'version': self.snapshot.version,
                                            'digest': fingerprint(self.snapshot)}}
        self.engine.create_run({'purpose': 'synthetic_report'}, {'seconds': 60}, run_id='origin')
        self.engine.save('origin', 'main_loop_outcome', 'main', {'snapshot': self.snapshot.model_dump(mode='json')})
        self.path = self.root / 'report.json'
        self.path.write_text(json.dumps(self.report), encoding='utf-8')
        self.reference = {'job_id': 'job', 'artifact_id': 'artifact'}
        self.session = {'account': 'synthetic'}
        workspace = Mock(base_url='http://localhost', source_id='synthetic-source')
        workspace.schema.project_id = 1
        workspace.patient.return_value = {'patientKey': '11', 'id': 'SYN-001', 'name': '合成测试'}
        analyses = Mock()
        analyses.artifact.return_value = ({'kind': 'json'}, self.path)
        self.db = ChatDatabase(self.root)
        chat_store = SimpleNamespace(database=lambda: self.db, transaction=self.db.transaction)
        self.chat = PatientChat(app, workspace, analyses, store=chat_store)
        self.sid = self.chat.create(self.session, '11')['id']
        self.db.append_message(self.sid, 'tool', json.dumps({'patient_id': '11', 'job_id': 'job'}),
                               tool_name='neurogra_analysis')
        self.request = {'message': '核查认知与检验', 'request_id': 'report-test', 'report': self.reference}

    def send(self):
        return self.chat.send(self.session, '11', self.sid, self.request, Mock())['reply']

    @staticmethod
    def delegate(_prompt, data, schema):
        if schema is FollowupDecision:
            return schema(route='delegate', tasks=[
                {'role': 'cognition', 'question': '核对量表', 'refs': ['cog']},
                {'role': 'laboratory', 'question': '核对检验', 'refs': ['lab']},
            ])
        if schema is AnswerSelection:
            return schema(refs=[row['id'] for row in data['evidence']] + ['foreign'])
        return schema(claims=[], uncertainties=[], requests=[])

    def test_delegate_scopes_sources_and_replays_after_chat_commit_failure(self):
        with patch.object(ModelGateway, 'generate_structured', side_effect=self.delegate) as model:
            with patch.object(self.chat.store, 'transaction', side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):
                    self.send()
            count = model.call_count
            reply = self.send()
            self.assertEqual(model.call_count, count)
            self.assertEqual(self.send(), reply)
        self.assertEqual([item['role'] for item in reply['collaboration']], ['cognition', 'laboratory'])
        self.assertEqual([item['sources'][0]['refs'] for item in reply['collaboration']], [['cog'], ['lab']])
        self.assertNotIn('foreign', [row['id'] for row in reply['sources']])
        self.assertEqual(self.chat.messages(self.session, '11', self.sid)['messages'][-1], reply)
        self.assertEqual(sum(row['role'] == 'assistant' for row in self.db.get_messages(self.sid)), 1)

    def test_report_answer_skips_specialists_and_permissions_cover_replay(self):
        with patch.object(ModelGateway, 'generate_structured',
                          return_value=FollowupDecision(route='answer_from_report', report_refs=['original'])) as model:
            reply = self.send()
            self.assertEqual(model.call_count, 1)
        self.assertIn('已有报告结论', reply['text'])
        self.assertNotIn('collaboration', reply)
        self.chat.analyses.artifact.side_effect = WorkspaceError('撤回访问权限', 404)
        with self.assertRaises(WorkspaceError):
            self.send()
        self.assertEqual([message['kind'] for message in self.chat.messages(self.session, '11', self.sid)['messages']],
                         ['analysis'])

    def test_report_permission_is_checked_again_before_the_model_call(self):
        self.chat.analyses.artifact.side_effect = [({'kind': 'json'}, self.path), WorkspaceError('无权查看', 403)]
        with patch('neurogra.clinical.generation.GenerationClient.request') as request:
            with self.assertRaises(WorkspaceError):
                self.send()
        request.assert_not_called()

    def test_snapshot_digest_mismatch_and_disabled_delegate_exclude_candidates(self):
        self.assertIsNone(bound_snapshot(self.engine, {**self.report, 'snapshot_binding': {
            **self.report['snapshot_binding'], 'digest': 'wrong'}}))
        self.config.report_followup_delegation = False
        with patch.object(ModelGateway, 'generate_structured', side_effect=self.delegate) as model:
            reply = self.send()
        self.assertEqual(model.call_count, 1)
        self.assertEqual(model.call_args.args[1]['candidates'], {})
        self.assertNotIn('collaboration', reply)

    def test_out_of_role_reference_cannot_reach_executor(self):
        with patch.object(ModelGateway, 'generate_structured', return_value=FollowupDecision(
                route='delegate', tasks=[{'role': 'cognition', 'question': '检验', 'refs': ['lab']}])) as model:
            reply = self.send()
        self.assertEqual(model.call_count, 1)
        self.assertNotIn('collaboration', reply)

    def test_timed_out_specialist_preserves_other_specialist_evidence(self):
        with patch.object(ModelGateway, 'generate_structured', side_effect=self.delegate), patch(
                'neurogra.platform.report_followup.CognitionAgent.analyze',
                new_callable=AsyncMock, side_effect=TimeoutError):
            reply = self.send()
        self.assertEqual(reply['collaboration'][0]['status'], 'timed_out')
        self.assertEqual(reply['collaboration'][0]['sources'], [])
        self.assertIn('检验项目 12', reply['text'])
