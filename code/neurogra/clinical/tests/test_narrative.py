import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.schemas import FinalReport, CaseSnapshot, Observation, Claim
from neurogra.clinical.service import create_context
from neurogra.clinical.narrative import NarrativeDraft, NarrativeVerification, NarrativeSelection, add_narrative, narrative_input
from neurogra.clinical.utils import dumps


class NarrativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.config = ClinicalConfig(project_root=Path(self.tmp.name))
        self.store, self.run = create_context(self.config)
        self.snapshot = CaseSnapshot(case_id='c', patient_id='p', raw_input_hash='h', records=[],
            observations=[Observation(observation_id='o', name='总分', domains=['cognition'], value=22,
                quote='22', status='observed', context={'名称':'MoCA'}, source_refs=[])])
        self.report = FinalReport(patient_id='p', run_id=self.run, status='partial', assessment_status='unresolved',
            summary='summary', claims=[], unresolved=[], citations=[])

    def test_unreviewed_interpretation_excluded_and_inputs_bounded(self):
        self.report.claims = [Claim(claim_id='c', text='unreviewed', kind='interpretation', level='history',
            observation_ids=['o'], evidence_ids=['e'], strength='tentative')]
        self.snapshot.observations += [self.snapshot.observations[0].model_copy(update={'observation_id':str(i),'quote':'长记录'*300}) for i in range(100)]
        data = narrative_input(self.report, self.snapshot, 6000)
        self.assertLess(len(dumps(data)), 6000)
        self.assertEqual(data['允许解释'], [])
        self.assertGreater(data['未纳入事实数'], 0)
        self.assertEqual(data['事实'][0]['项目'], 'MoCA / 总分')

    def test_real_paragraph_field_and_durable_reuse(self):
        draft = NarrativeDraft(paragraph='现有记录提供了认知量表总分，但尚未完整记录评估条件，因此目前只能保留资料中的分数，不能仅凭这项记录确定病因。仍需结合病程、日常生活影响及其他专业资料核对，再形成综合判断。', source_ids=['F1'])
        with patch('neurogra.clinical.narrative.ModelGateway.generate_structured', return_value=draft) as generate:
            add_narrative(self.report, self.snapshot, self.config, self.store, self.run)
            add_narrative(self.report, self.snapshot, self.config, self.store, self.run)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(self.report.narrative_status, 'generated')
        self.assertEqual(self.report.narrative_source_ids, ['o'])
        self.assertIn('本次自动复查未完成', self.report.natural_language_report)
        self.assertNotIn('\n', self.report.natural_language_report)

    def test_invalid_source_cannot_be_published_as_generated(self):
        draft = NarrativeDraft(paragraph='资料不足，暂时无法形成确定判断。'*8, source_ids=['invented'])
        with patch('neurogra.clinical.narrative.ModelGateway.generate_structured', return_value=draft):
            add_narrative(self.report, self.snapshot, self.config, self.store, self.run)
        self.assertEqual(self.report.narrative_status, 'unavailable')
        self.assertEqual(self.report.narrative_source_ids, [])

    def test_grounding_failure_requires_repair_and_second_verification(self):
        self.config.verify_narrative = True
        draft = NarrativeDraft(paragraph='资料中的量表数值需要结合实际评估条件核对，目前仅保留原始记录，无法单独确认诊断。' * 3,
                               source_ids=['F1'])
        rejected = NarrativeVerification(supported=False, unsupported_statements=['项目名称混淆'], explanation='需纠正')
        accepted = NarrativeVerification(supported=True, unsupported_statements=[], explanation='仅概述已有来源')
        with patch('neurogra.clinical.narrative.ModelGateway.generate_structured',
                   side_effect=[draft, rejected, draft, accepted]) as generate:
            add_narrative(self.report, self.snapshot, self.config, self.store, self.run)
        self.assertEqual(generate.call_count, 4)
        self.assertEqual(self.report.narrative_status, 'generated')
        self.assertEqual(len(self.store.objects(self.run, 'narrative_verification')), 2)

    def test_twice_unsupported_narrative_is_not_published(self):
        self.config.verify_narrative = True
        draft = NarrativeDraft(paragraph='没有足够的来源支持该描述，仍需核对原始资料和检查条件。' * 4, source_ids=['F1'])
        rejected = NarrativeVerification(supported=False, unsupported_statements=['没有原文依据'], explanation='不支持')
        with patch('neurogra.clinical.narrative.ModelGateway.generate_structured',
                   side_effect=[draft, rejected, draft, rejected]):
            add_narrative(self.report, self.snapshot, self.config, self.store, self.run)
        self.assertEqual(self.report.narrative_status, 'unavailable')

    def test_rejected_prose_can_use_model_selected_exact_source_rendering(self):
        self.config.verify_narrative = True
        self.snapshot.observations[0].quote = '22'
        with patch('neurogra.clinical.narrative.ModelGateway.generate_structured',
                   side_effect=[ValueError('invalid prose'), NarrativeSelection(fact_ids=['F1'])]):
            add_narrative(self.report, self.snapshot, self.config, self.store, self.run)
        self.assertEqual(self.report.narrative_status, 'generated')
        self.assertEqual(self.report.narrative_method, 'source_rendered')
        self.assertIn('原文记录为“22”', self.report.natural_language_report)
        self.assertEqual(self.report.narrative_source_ids, ['o'])
        self.assertEqual(len(self.store.objects(self.run, 'narrative_source_validation')), 1)
