import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.schemas import CaseSnapshot, Observation, EvidenceItem, FinalReport, DiagnosticAssessment
from neurogra.clinical.storage import RunStore
from neurogra.clinical.diagnostic_synthesis import (
    add_diagnostic_assessment, validate_assessment, synthesis_input, AssessmentReview,
)
from neurogra.clinical.reporting import render_report
from neurogra.clinical.service import PreparationResult
from neurogra.clinical.schemas import TaskPlan
from neurogra.clinical.diagnostic_outcomes import LayerDraft, LayerReview


class DiagnosticSynthesisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = ClinicalConfig(project_root=Path(self.tmp.name))
        self.store = RunStore(Path(self.tmp.name) / 'run.sqlite')
        self.run = self.store.create_run({}, self.config.budget.model_dump())
        self.snapshot = CaseSnapshot(case_id='c', patient_id='p', raw_input_hash='h', records=[], observations=[
            Observation(observation_id='obs_a', name='MMSE总分', value=25, quote='25',
                        domains=['cognition'], source_refs=[], context={}, status='observed'),
            Observation(observation_id='obs_b', name='日常功能', value='独立', quote='独立',
                        domains=['history'], source_refs=[], context={}, status='observed')])
        self.evidence = [EvidenceItem(evidence_id='evidence_a', document_id='doc', span_id='span',
            quote='用于测试引用绑定的原文，不是临床建议。' * 10, title='测试指南', version='1', matched_by=[],
            allowed_use='direct_support', applicability='applicable')]
        self.report = FinalReport(patient_id='p', run_id=self.run, status='partial', assessment_status='unresolved',
            summary='', claims=[], unresolved=[], citations=[])
        self.draft = DiagnosticAssessment(primary_label='测试候选', conclusion='综合考虑测试候选。',
            steps=[dict(topic='认知表现', finding='MMSE记录25。', interpretation='测试解释一。',
                        observation_ids=['obs_a'], evidence_ids=['evidence_a']),
                   dict(topic='功能表现', finding='日常功能记录独立。', interpretation='测试解释二。',
                        observation_ids=['obs_b'], evidence_ids=['evidence_a'])])
        self.layer = LayerDraft(status='working_diagnosis', primary_label='测试候选', reasoning='测试解释一，综合考虑测试候选。',
                                group_ids=['G1'], evidence_ids=['K1'])

    def test_verified_synthesis_is_published_with_reasoning_and_citations(self):
        with patch('neurogra.clinical.diagnostic_synthesis.ModelGateway.generate_structured',
                   side_effect=[self.layer, LayerReview(supported=True), self.layer, LayerReview(supported=True)]):
            self.assertTrue(add_diagnostic_assessment(self.report, self.snapshot, self.evidence,
                                                     self.config, self.store, self.run))
        self.assertEqual(self.report.diagnostic_outcome.status, 'working_diagnosis')
        preparation = PreparationResult(run_id=self.run, status='prepared', snapshot=self.snapshot,
            plan=TaskPlan(tasks=[]), knowledge_release_id=None, issues=[], artifact_path='')
        text = render_report(self.report, preparation, [], self.evidence, {}, 'test')
        for phrase in ('首选工作判断：测试候选', 'MMSE', '测试解释一', '综合考虑测试候选', '#reference-1'):
            self.assertIn(phrase, text)

    def test_review_rejection_never_publishes_model_claimed_verified_flag(self):
        self.draft.verified = True
        rejection = LayerReview(supported=False, issues=[dict(quote='测试解释一', kind='medical_rule',
            basis_ids=['K1'], reason='临床推断不受来源支持', severity='major')])
        with patch('neurogra.clinical.diagnostic_synthesis.ModelGateway.generate_structured',
                   side_effect=[self.layer, rejection] * 4):
            self.assertFalse(add_diagnostic_assessment(self.report, self.snapshot, self.evidence,
                                                      self.config, self.store, self.run))
        self.assertIsNone(self.report.diagnostic_assessment)
        self.assertEqual(self.report.diagnostic_outcome.status, 'indeterminate')
        self.assertIsNone(self.report.diagnostic_outcome.primary_working_label)

    def test_unknown_and_missing_references_are_rejected(self):
        data = synthesis_input(self.snapshot, self.evidence, self.config.model)
        self.draft.steps[0].observation_ids = ['invented']
        with self.assertRaisesRegex(ValueError, 'unknown_observation'):
            validate_assessment(self.draft, data)
        self.draft.steps[0].observation_ids = ['obs_a']
        self.draft.steps[0].evidence_ids = ['invented']
        with self.assertRaisesRegex(ValueError, 'missing_evidence'):
            validate_assessment(self.draft, data)
        with self.assertRaises(ValueError):
            self.draft.steps[0].evidence_ids = []

    def test_no_evidence_does_not_trigger_ungrounded_diagnosis(self):
        with patch('neurogra.clinical.diagnostic_synthesis.ModelGateway.generate_structured') as generate:
            self.assertFalse(add_diagnostic_assessment(self.report, self.snapshot, [],
                                                      self.config, self.store, self.run))
        generate.assert_not_called()
        self.assertIsNone(self.report.diagnostic_assessment)
        self.assertEqual(self.report.diagnostic_outcome.status, 'indeterminate')

    def test_label_must_be_part_of_conclusion(self):
        self.draft.conclusion = '仅复述量表'
        with self.assertRaisesRegex(ValueError, 'label_not_in_conclusion'):
            validate_assessment(self.draft, synthesis_input(self.snapshot, self.evidence, self.config.model))

    def test_successful_revision_is_rechecked_before_publication(self):
        with patch('neurogra.clinical.diagnostic_synthesis.ModelGateway.generate_structured', side_effect=[
                self.layer, LayerReview(supported=True, issues=[dict(quote='测试解释一', kind='wording',
                    basis_ids=['G1'], reason='改写', severity='minor')]),
                self.layer, LayerReview(supported=True), self.layer, LayerReview(supported=True)]) as generate:
            self.assertTrue(add_diagnostic_assessment(self.report, self.snapshot, self.evidence,
                                                     self.config, self.store, self.run))
        self.assertEqual(generate.call_count, 6)
        self.assertIn('corrections', generate.call_args_list[2].args[1])

    def test_patient_fact_values_are_rendered_from_references_not_model_text(self):
        self.draft.steps[0].finding = 'MMSE 99分，模型错误改写。'
        with patch('neurogra.clinical.diagnostic_synthesis.ModelGateway.generate_structured',
                   side_effect=[self.layer, LayerReview(supported=True), self.layer, LayerReview(supported=True)]) as generate:
            self.assertTrue(add_diagnostic_assessment(self.report, self.snapshot, self.evidence,
                                                     self.config, self.store, self.run))
        self.assertEqual(self.report.diagnostic_outcome.layers[0].review_status, 'passed')
        self.assertIn('groups', generate.call_args_list[1].args[1])
        self.assertNotIn('99', str(generate.call_args_list[1].args[1]['groups']))


if __name__ == '__main__':
    unittest.main()
