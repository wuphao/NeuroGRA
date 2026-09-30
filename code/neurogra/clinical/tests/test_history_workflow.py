import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.service import prepare_patient
from neurogra.clinical.storage import RunStore
from neurogra.clinical.schemas import Claim, AgentResult, ReviewResponse, ReviewRequest, EvidenceBundle
from neurogra.clinical.history import check_claims, HistoryAgent, RevisionDraft, HistoryDraft
from neurogra.clinical.workflow import continue_run, resume_run, apply_review, ReviewPlan, ReviewConcern, Synthesis


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = ClinicalConfig(project_root=Path(self.tmp.name))
        self.prep = prepare_patient({'患者ID': '../测试', '病史': '未提供既往用药记录。'}, self.config, False)
        self.store = RunStore(self.config.resolve(self.config.database))
        self.task = self.prep.plan.tasks[0]
        self.claim = Claim(claim_id='c1', text='未提供既往用药记录。', kind='description', level='history',
            observation_ids=[self.prep.snapshot.observations[0].observation_id], strength='descriptive')
        self.result = AgentResult(result_id='r1', task_id=self.task.task_id, case_version=1,
                                  status='completed', claims=[self.claim])

    def save_result(self):
        self.store.save(self.prep.run_id, 'result', 'r1', self.result)

    def test_fabricated_evidence_rejected(self):
        claim = self.claim.model_copy(update={'evidence_ids': ['fake']})
        with self.assertRaises(ValueError):
            check_claims([claim], self.prep.snapshot, [])

    def test_history_invalid_reference_repair_and_empty_retrieval(self):
        counter = []
        claim = self.claim
        class Tools:
            async def read_case_record(self, *args): return {'text': '未提供既往用药记录。'}
            async def search_knowledge(self, request):
                counter.append('search')
                return EvidenceBundle(bundle_id='b', request_id=request.request_id, status='empty', backend_status={'bm25': 'empty'})
            async def generate_structured(self, system, data, schema):
                counter.append('model')
                return HistoryDraft(claims=[claim.model_copy(update={'evidence_ids': ['fake']}) if counter.count('model') == 1 else claim])
        result = asyncio.run(HistoryAgent().analyze(self.task, SimpleNamespace(snapshot=self.prep.snapshot, tools=Tools())))
        self.assertEqual(counter, ['search', 'model', 'model'])
        self.assertEqual(result.claims, [claim])
        self.assertEqual(result.status, 'partial')

    def test_strength_escalation_rejected(self):
        claim = self.claim.model_copy(update={'strength': 'supported'})
        with self.assertRaises(ValueError):
            check_claims([claim], self.prep.snapshot, [])

    def test_stale_review_rejected_before_model(self):
        request = ReviewRequest(request_id='q', target_agent='history', target_result_id='r1',
            target_result_version=2, claim_ids=['c1'], question='核对', allowed_actions=[], success_criterion='核对', if_unresolved='保留')
        with self.assertRaises(ValueError):
            asyncio.run(HistoryAgent().review(request, self.result, self.prep.snapshot, [], None))

    def test_apply_replay_and_stale_version(self):
        self.save_result()
        revised = self.result.model_copy(update={'version': 2, 'claims': []})
        response = ReviewResponse(request_id='q', resolution='corrected', answers=['撤回'], revised_result=revised)
        apply_review(self.store, self.prep.run_id, self.result, response)
        apply_review(self.store, self.prep.run_id, self.result, response)
        self.assertEqual(len(self.store.objects(self.prep.run_id, 'result')), 2)
        changed = revised.model_copy(update={'claims': [self.claim]})
        with self.assertRaises(ValueError):
            apply_review(self.store, self.prep.run_id, self.result, response.model_copy(update={'revised_result': changed}))

    def test_completed_resume_no_calls(self):
        self.save_result()
        def generate(_, system, data, schema):
            return ReviewPlan() if schema == ReviewPlan else Synthesis(ordered_claim_ids=['c1'])
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured', generate):
            result = continue_run(self.prep, self.config)
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured', side_effect=AssertionError('must not call')):
            resumed = resume_run(result.run_id, self.config)
        self.assertEqual(result, resumed)
        self.assertTrue(Path(result.report_paths['markdown']).is_relative_to(self.config.project_root))

    def test_review_failure_quarantines_questioned_claims(self):
        self.save_result()
        def generate(_, system, data, schema):
            if schema == ReviewPlan:
                return ReviewPlan(concerns=[ReviewConcern(claim_ids=['c1'], question='请核对是否误将未记录当作阴性', success_criterion='保留原文明确的缺失状态')])
            raise RuntimeError('offline')
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured', generate):
            result = continue_run(self.prep, self.config)
        self.assertEqual(result.report.claims, [])
        self.assertEqual(result.status, 'partial')

    def test_no_data_does_not_call_model(self):
        prep = prepare_patient({'患者ID': 'empty'}, self.config, False)
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured', side_effect=AssertionError('no data')):
            result = continue_run(prep, self.config)
        self.assertEqual(result.report.assessment_status, 'insufficient_data')
        self.assertEqual(result.status, 'completed')

    def test_source_conflict_is_visible_even_when_models_fail(self):
        raw = {'患者ID': 'conflicting-source', '检验': [
            {'名称': '检验项目', '时间': '2026-09-01', '数值': 1},
            {'名称': '检验项目', '时间': '2026-09-01', '数值': 2}]}
        prep = prepare_patient(raw, self.config, False)
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured', side_effect=RuntimeError('offline')):
            result = continue_run(prep, self.config)
        self.assertTrue(any('同日同项目记录存在不同原值' in item for item in result.report.unresolved))
        for observation in prep.snapshot.observations:
            self.assertIn(observation.observation_id, Path(result.report_paths['provenance']).read_text(encoding='utf-8'))

    def test_lock_excludes_concurrent_resume(self):
        with self.store.workflow_lock(self.prep.run_id):
            with self.assertRaises(OSError):
                with self.store.workflow_lock(self.prep.run_id):
                    pass

    def test_unresolved_stops_without_retry(self):
        self.save_result()
        calls = []
        def generate(_, system, data, schema):
            calls.append(schema)
            if schema == ReviewPlan:
                return ReviewPlan(unresolved=['当前资料无法确定用药'])
            return Synthesis(ordered_claim_ids=['c1'])
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured', generate):
            result = continue_run(self.prep, self.config)
        self.assertEqual(calls.count(ReviewPlan), 1)
        self.assertIn('当前资料无法确定用药', result.report.unresolved)

    def test_synthesis_cannot_add_claim(self):
        self.save_result()
        def generate(_, system, data, schema):
            return ReviewPlan() if schema == ReviewPlan else Synthesis(ordered_claim_ids=['invented'])
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured', generate):
            result = continue_run(self.prep, self.config)
        self.assertEqual([c.claim_id for c in result.report.claims], ['c1'])
        self.assertEqual(result.status, 'partial')

    def test_targeted_correction_and_resume_after_report_write_crash(self):
        self.result.claims[0].text = '无既往用药。'
        self.save_result()
        calls = []
        def generate(_, system, data, schema):
            calls.append(schema)
            if schema == ReviewPlan:
                if calls.count(ReviewPlan) == 1:
                    return ReviewPlan(concerns=[ReviewConcern(claim_ids=['c1'], question='是否将未提供用药记录错误写成无用药', success_criterion='恢复原文缺失状态且不表述为阴性')])
                return ReviewPlan()
            if schema == RevisionDraft:
                return RevisionDraft(resolution='corrected', answers=['原文是未提供，不是无用药'],
                    claims=[self.claim.model_copy(update={'text': '未提供既往用药记录。'})])
            return Synthesis(ordered_claim_ids=['c1'])
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured', generate):
            with patch('neurogra.clinical.workflow.render_report', side_effect=RuntimeError('simulated crash')):
                with self.assertRaises(RuntimeError):
                    continue_run(self.prep, self.config)
            count_before = len(calls)
            result = resume_run(self.prep.run_id, self.config)
        self.assertEqual(len(calls), count_before)
        self.assertEqual(result.report.claims[0].text, '未提供既往用药记录。')
        self.assertEqual(len(self.store.objects(self.prep.run_id, 'result')), 2)

    def test_child_can_maintain_with_evidence(self):
        self.save_result()
        def generate(_, system, data, schema):
            if schema == ReviewPlan:
                return ReviewPlan(concerns=[ReviewConcern(claim_ids=['c1'], question='请核对未提供是否原文明确写明', success_criterion='以原文澄清主Agent对该结论的疑问')])
            if schema == RevisionDraft:
                return RevisionDraft(resolution='maintained_with_evidence', answers=['原文明确写明未提供既往用药记录'], claims=[self.claim])
            return Synthesis(ordered_claim_ids=['c1'])
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured', generate):
            result = continue_run(self.prep, self.config)
        self.assertEqual(result.stop_reason, 'no_new_information')
        self.assertEqual(result.report.claims, [self.claim])

    def test_report_description_uses_patient_quote_not_added_causal_text(self):
        self.result.claims[0].text = '未提供既往用药记录，因此无法排除药物性认知障碍。'
        self.save_result()
        def generate(_, system, data, schema):
            return ReviewPlan() if schema == ReviewPlan else Synthesis(ordered_claim_ids=['c1'])
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured', generate):
            result = continue_run(self.prep, self.config)
        self.assertEqual(result.report.claims[0].text, '未提供既往用药记录。')
        self.assertIn('因此', self.store.objects(self.prep.run_id, 'result')[0]['payload']['claims'][0]['text'])


if __name__ == '__main__':
    unittest.main()
