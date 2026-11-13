"""Layer publication, source-type boundaries and target-label masking."""
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path

from neurogra.clinical.diagnostic_outcomes import (
    LayerDraft, LayerReview, validate_draft, validate_review, source_groups,
)
from neurogra.clinical.label_masking import mask_labels
from neurogra.clinical.schemas import CaseSnapshot, Observation


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.data = {'groups': [{'id': 'G1'}], 'evidence': [{'id': 'K1'}]}
        self.draft = LayerDraft(status='working_diagnosis', primary_label='测试工作判断', reasoning='基于提供的事实形成测试工作判断。',
                                group_ids=['G1'], evidence_ids=['K1'])

    def test_patient_facts_cannot_require_patient_in_medical_paper(self):
        review = LayerReview(supported=False, issues=[dict(quote='提供的事实', kind='patient_fact',
            basis_ids=['K1'], reason='论文没有该患者的诊断', severity='major')])
        with self.assertRaisesRegex(ValueError, 'patient_fact_must_use_patient_sources'):
            validate_review(review, self.draft, self.data)

    def test_medical_rule_requires_medical_source(self):
        review = LayerReview(supported=False, issues=[dict(quote='提供的事实', kind='medical_rule',
            basis_ids=['G1'], reason='规则与原文不符', severity='major')])
        with self.assertRaisesRegex(ValueError, 'medical_rule_requires_medical_sources'):
            validate_review(review, self.draft, self.data)

    def test_review_cannot_quote_invented_sentence(self):
        review = LayerReview(supported=False, issues=[dict(quote='患者确诊AD', kind='medical_rule',
            basis_ids=['K1'], reason='原文不支持', severity='major')])
        with self.assertRaisesRegex(ValueError, 'review_quote'):
            validate_review(review, self.draft, self.data)

    def test_conditional_requires_explicit_conditions(self):
        self.draft.status = 'conditional_diagnosis'
        with self.assertRaisesRegex(ValueError, 'requires_conditions'):
            validate_draft(self.draft, self.data)
        self.draft.missing_conditions = ['方法条件未核实']
        validate_draft(self.draft, self.data)

    def test_diagnosis_cannot_cite_only_unrecorded_patient_groups(self):
        data = {'groups': [{'id': 'G1', 'records': [[None, None, 'not_recorded']]},
                           {'id': 'G2', 'records': [['2025-01-01', 23, 'observed']]}],
                'evidence': [{'id': 'K1'}]}
        with self.assertRaisesRegex(ValueError, 'requires_recorded_patient_fact'):
            validate_draft(self.draft, data)
        self.draft.group_ids = ['G2']
        validate_draft(self.draft, data)

    def test_unknown_and_duplicate_ids_rejected(self):
        self.draft.group_ids = ['G2']
        with self.assertRaisesRegex(ValueError, 'unknown_patient_group'):
            validate_draft(self.draft, self.data)
        self.draft.group_ids = ['G1', 'G1']
        with self.assertRaisesRegex(ValueError, 'duplicate_source'):
            validate_draft(self.draft, self.data)

    def test_group_expands_more_than_sixteen_real_records_without_truncation(self):
        observations = [Observation(observation_id=f'o{i}', name='MMSE总分', value=i, quote=str(i),
            domains=['cognition'], source_refs=[], status='observed') for i in range(30)]
        snapshot = CaseSnapshot(case_id='c', patient_id='p', raw_input_hash='h', observations=observations, records=[])
        groups, mapping = source_groups(snapshot, 'syndrome')
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]['records']), 30)
        self.assertEqual(len(mapping['G1']), 30)

    def test_masking_covers_duplicate_fields_and_free_text_without_mutating_source(self):
        raw = {'patient': {'patient_number': 'p'}, 'demographics': {'患病情况': 'AD'},
               'forms': {'records': [{'患病情况': 'MCI', '摘要': '既往AD待随访', 'MMSE总分': 25}]},
               'ADAS-Cog': 17, '病史': '记忆下降'}
        masked, manifest = mask_labels(raw)
        self.assertEqual(raw['demographics']['患病情况'], 'AD')
        self.assertIsNone(masked['demographics']['患病情况'])
        self.assertIsNone(masked['forms']['records'][0]['摘要'])
        self.assertEqual(masked['forms']['records'][0]['MMSE总分'], 25)
        self.assertEqual(masked['ADAS-Cog'], 17)
        self.assertEqual(masked['病史'], '记忆下降')
        self.assertEqual(len(manifest['removed_paths']), 3)

    def test_blinded_mode_rejects_unsanitized_attachment(self):
        with self.assertRaisesRegex(ValueError, 'sanitized_attachments'):
            mask_labels({'影像': {'path': 'file.nii'}})

    def test_status_code_is_never_published_as_clinical_label(self):
        self.draft.primary_label = 'working_diagnosis'
        with self.assertRaisesRegex(ValueError, 'clinical_name_not_status'):
            validate_draft(self.draft, self.data)

    def test_blinded_preparation_stores_only_masked_model_input(self):
        from neurogra.clinical.config import ClinicalConfig, KnowledgeAccess
        from neurogra.clinical.service import prepare_patient
        from neurogra.clinical.storage import RunStore
        with tempfile.TemporaryDirectory() as temporary:
            config = ClinicalConfig(project_root=Path(temporary), diagnosis_task_mode='blinded_classification',
                main_agent=KnowledgeAccess(use_kb=False, use_kg=False), agents={})
            preparation = prepare_patient({'患者ID': 'test', '患病情况': 'AD', '病史': '记忆下降', 'MMSE总分': 24},
                                          config, use_model=False)
            store = RunStore(config.resolve(config.database))
            for row in store.objects(preparation.run_id, 'patient_input'):
                self.assertIsNone(row['payload']['raw']['患病情况'])
            self.assertTrue(store.objects(preparation.run_id, 'label_masking'))
            self.assertFalse(any(o.value == 'AD' for o in preparation.snapshot.observations))
